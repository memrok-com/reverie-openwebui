import copy
from contextlib import closing
from dataclasses import asdict
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zipfile import ZipFile

from compatibility import intake as i

D = lambda char: 'sha256:' + char * 64


def report(digest, verdict='incompatible', project='a') -> dict[str, Any]:
    ui = i.UI_REPO + '@' + digest
    check = i.PASS if verdict == 'tested/pass' else 'unsupported-version'
    fields = dict(version='0.13.4', login=True, install=True) if verdict == 'tested/pass' else dict(check=check, version='0.13.5')
    return dict(ui=ui, adapter=i.ADAPTER, platform='linux/amd64', required_version='0.13.4',
                upgrade='not-tested', cleanup=i.CLEAN, project='reverie-pilot-' + project * 32,
                status=verdict, check=check, flow_sha256=i.FLOW_HASH, package_sha256='4' * 64,
                images={i.UI: D('1'), ui: D('2' if ui != i.UI else '1'), i.ADAPTER: D('3')},
                bootstrap={'status': 'ready' if verdict == 'tested/pass' else verdict, **fields})


class Fake:
    def __init__(self):
        self.items = {}
        self.posts = 0
        self.pending = False
        self.bad_job = False
        self.bad_run = False
        self.evidence = {}
        self.missing_runs = set()
        self.missing_issues = set()
        self.lost = False
        self.hidden = False

    def issues(self, state):
        return [copy.deepcopy(x) for x in self.items.values()
                if not self.hidden and (state == 'all' or x['state'] == state)]

    def issue(self, number):
        if number in self.missing_issues:
            raise i.ResourceUnavailable('github-resource-unavailable')
        return copy.deepcopy(self.items[number])

    def create(self, title, body):
        self.posts += 1
        n = 10 + self.posts
        self.items[n] = dict(title=title, body=body, number=n, id=n+100, state='open',
                             user={'login': i.BOT, 'id': i.BOT_ID}, repository_url=i.API_REPO)
        if self.lost:
            raise i.IntakeError('post-response-lost')
        return self.issue(n)

    def run(self, source):
        if source in self.missing_runs:
            raise i.ResourceUnavailable('github-resource-unavailable')
        return dict(id=source.run, run_attempt=source.attempt, path=i.WORKFLOW,
                    event='pull_request' if self.bad_run else source.event,
                    head_branch='main', head_sha=source.sha, repository={'id': i.REPO_ID},
                    head_repository={'id': i.REPO_ID}, status='in_progress' if self.pending else 'completed')

    def jobs(self, source):
        return [dict(name=i.PROBE_JOB, head_sha=source.sha, status='in_progress' if self.pending else 'completed', run_id=source.run,
                     conclusion='failure' if self.bad_job else 'success')]

    def proof(self, source):
        evidence = self.evidence[source]
        if isinstance(evidence, Exception):
            raise evidence
        return evidence


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.candidate = D('c')
        self.first = report(self.candidate)
        self.repeat = report(self.candidate, project='b')
        self.control = report(i.UI_DIGEST, 'tested/pass', 'c')
        self.source = i.Source(123, 1, 'a' * 40, 'schedule')
        record = i.qualify(self.candidate, self.first, self.repeat, self.control)
        assert record is not None
        self.record = record
        self.api = Fake()
        self.api.evidence[self.source] = self.record

    def test_repeated_refusal_with_passing_control(self):
        self.assertEqual(self.record, i.Pair(self.candidate, i.ADAPTER_DIGEST, 'unsupported-version'))
        self.assertIsNone(i.qualify(i.UI_DIGEST, self.control, self.control, self.control))

    def test_never_file_on_unmatched_incomplete_or_unverified_reports(self):
        mutations = [{'status': 'indeterminate'}, {'cleanup': 'failed'}, {'project': self.first['project']},
                     {'images': {**self.repeat['images'], i.ADAPTER: D('5')}}, {'package_sha256': '5'*64},
                     {'check': 'unexpected-code'}, {'flow_sha256': '5'*64}, {'platform': 'linux/arm64'},
                     {'bootstrap': dict(self.repeat['bootstrap'], version=None)},
                     {'bootstrap': dict(self.repeat['bootstrap'], version='0.13.6')}]
        for changes in mutations:
            with self.subTest(changes=changes):
                self.assertIsNone(i.qualify(self.candidate, self.first, self.repeat | changes, self.control))
        self.assertIsNone(i.qualify(self.candidate, self.first, self.repeat,
                                    self.control | {'status': 'indeterminate'}))

    def test_write_once_across_reruns_and_closed_issue(self):
        self.assertEqual(i.file_issue(self.api, self.record, self.source), 11)
        self.api.items[11]['state'] = 'closed'
        self.assertIsNone(i.file_issue(self.api, self.record, i.Source(123, 2, 'a'*40, 'schedule')))
        self.assertEqual(self.api.posts, 1)

    def test_unknown_write_once_then_refetch(self):
        self.api.lost = True
        self.assertEqual(i.file_issue(self.api, self.record, self.source), 11)
        self.assertEqual(self.api.posts, 1)
        self.api.items.clear()
        self.api.hidden = True
        with self.assertRaises(i.IntakeError):
            i.file_issue(self.api, self.record, self.source)
        self.assertEqual(self.api.posts, 2)

    def test_edited_issue_cannot_confirm_an_uncertain_write(self):
        original_create = self.api.create

        def create_with_lost_response(title, body):
            original_create(title, body)
            self.api.items[11]['body'] += '\nNot the canonical record'
            raise i.IntakeError('github-request-failed')

        with patch.object(self.api, 'create', side_effect=create_with_lost_response):
            with self.assertRaises(i.IntakeError):
                i.file_issue(self.api, self.record, self.source)
        self.assertIsNone(i.file_issue(self.api, self.record, self.source))
        self.assertEqual(self.api.posts, 1)

    def test_untrusted_and_pending_cases_never_claim(self):
        i.file_issue(self.api, self.record, self.source)
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/'ledger.sqlite'
            self.api.pending = True
            self.assertEqual(i.scan(self.api, state), [])
            self.api.pending = False
            self.api.bad_run = True
            self.assertEqual(i.scan(self.api, state), [])
            self.api.bad_run = False
            self.api.bad_job = True
            self.assertEqual(i.scan(self.api, state), [])
            self.api.bad_job = False
            self.api.items[11]['title'] += ' tampered'
            self.assertEqual(i.scan(self.api, state), [])
            self.api.items[11]['title'] = i.title(self.record)
            self.api.items[11]['user']['id'] = 1
            self.assertEqual(i.scan(self.api, state), [])
            self.api.items[11]['user']['id'] = i.BOT_ID
            self.assertEqual([x['issue'] for x in i.scan(self.api, state)], [11])

    def test_interrupted_handoff_retries_until_ack_and_then_stays_quiet(self):
        i.file_issue(self.api, self.record, self.source)
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/'ledger.sqlite'
            first = i.scan(self.api, state)
            self.assertEqual([x['issue'] for x in first], [11])
            # No triage acknowledgment: a new process must see the same work.
            self.assertEqual(i.scan(self.api, state), first)
            self.api.items[11]['title'] += ' tampered'
            self.assertEqual(i.scan(self.api, state), [])
            self.api.items[11]['title'] = i.title(self.record)
            with self.assertRaisesRegex(i.IntakeError, 'unknown-intake-issue'):
                i.acknowledge(state, 42)
            with ThreadPoolExecutor(max_workers=2) as workers:
                self.assertEqual(sorted(workers.map(lambda _: i.acknowledge(state, 11), range(2))), [False, True])
            self.assertEqual(i.scan(self.api, state), [])
            second = i.Source(124, 1, 'a' * 40, 'schedule')
            self.api.evidence[second] = i.Pair(D('d'), i.ADAPTER_DIGEST, 'unsupported-version')
            i.file_issue(self.api, self.api.evidence[second], second)
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: i.scan(self.api, state), range(2)))
            self.assertEqual([[x['issue'] for x in batch] for batch in results], [[12], [12]])
            with closing(sqlite3.connect(state)) as db:
                self.assertEqual(db.execute('select count(*) from intake').fetchone()[0], 2)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_no_ledger_is_created_when_acknowledging_unknown_work(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/'ledger.sqlite'
            with self.assertRaisesRegex(i.IntakeError, 'unknown-intake-issue'):
                i.acknowledge(state, 11)
            self.assertFalse(state.exists())

    def test_filing_context_refuses_untrusted_ref_or_unarmed(self):
        env = dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY=i.REPO, GITHUB_REPOSITORY_ID=str(i.REPO_ID),
                   GITHUB_REF='refs/heads/main', GITHUB_WORKFLOW_REF=i.REPO + '/' + i.WORKFLOW + '@refs/heads/main',
                   GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', GITHUB_SHA='a'*40,
                   GITHUB_EVENT_NAME='schedule', REVERIE_COMPAT_ARMED='true')
        self.assertEqual(i.source_from_env(env), self.source)
        for changes in [dict(GITHUB_REF='refs/heads/test'), dict(GITHUB_EVENT_NAME='pull_request'),
                        dict(REVERIE_COMPAT_ARMED='false'), dict(GITHUB_WORKFLOW_REF='another.yml'),
                        dict(GITHUB_RUN_ID='0')]:
            with self.assertRaises(i.IntakeError):
                i.source_from_env(env | changes)

    def test_machine_marker_and_payload_cannot_be_edited(self):
        i.file_issue(self.api, self.record, self.source)
        self.api.items[11]['body'] += '\nUntrusted free text'
        self.assertIsNone(i.file_issue(self.api, self.record, self.source))
        self.assertEqual(self.api.posts, 1)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(i.scan(self.api, Path(d)/'ledger.sqlite'), [])

    def test_edited_closed_issue_reserves_only_its_own_pair(self):
        i.file_issue(self.api, self.record, self.source)
        self.api.items[11].update(title='Triaged', state='closed',
                                  body=self.api.items[11]['body'] + '\n' + 'x' * 5000)
        other = i.Source(124, 1, 'b'*40, 'schedule')
        self.api.evidence[other] = i.Pair(D('d'), i.ADAPTER_DIGEST, 'unsupported-version')
        self.assertEqual(i.file_issue(self.api, self.api.evidence[other], other), 12)
        self.assertIsNone(i.file_issue(self.api, self.record, self.source))
        self.assertEqual(self.api.posts, 2)

    def test_missing_marker_with_original_title_blocks_only_its_own_pair(self):
        i.file_issue(self.api, self.record, self.source)
        self.api.items[11]['body'] = 'Notes, original marker accidentally removed'
        other = i.Source(124, 1, 'b'*40, 'schedule')
        self.api.evidence[other] = i.Pair(D('d'), i.ADAPTER_DIGEST, 'unsupported-version')
        self.assertIsNone(i.file_issue(self.api, self.record, self.source))
        self.assertEqual(i.file_issue(self.api, self.api.evidence[other], other), 12)

    def test_bad_evidence_or_vanished_issue_does_not_hide_valid_work(self):
        other = i.Source(124, 1, 'b'*40, 'schedule')
        new = i.Pair(D('d'), i.ADAPTER_DIGEST, 'unsupported-version')
        i.file_issue(self.api, self.record, self.source)
        self.api.evidence[other] = new
        i.file_issue(self.api, new, other)
        cases = ('invalid-or-expired-proof', 'missing-or-ambiguous-proof', 'deleted-run', 'vanished-issue')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                self.api.evidence[self.source] = self.record if case in cases[2:] else i.ProofError(case)
                self.api.missing_runs = {self.source} if case == 'deleted-run' else set()
                self.api.missing_issues = {11} if case == 'vanished-issue' else set()
                state = Path(d)/'ledger.sqlite'
                self.assertEqual([x['issue'] for x in i.scan(self.api, state)], [12])
                with closing(sqlite3.connect(state)) as db:
                    self.assertEqual(db.execute('SELECT issue FROM intake').fetchall(), [(12,)])

    def test_transport_failure_still_fails_the_sweep_without_receipt(self):
        i.file_issue(self.api, self.record, self.source)
        self.api.evidence[self.source] = i.IntakeError('github-request-failed')
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/'ledger.sqlite'
            with self.assertRaisesRegex(i.IntakeError, 'github-request-failed'):
                i.scan(self.api, state)
            self.assertFalse(state.exists())

    def test_gh_classifies_only_scoped_404_410_as_missing_evidence(self):
        api = i.GitHub()
        for code in (401, 403, 404, 410, 429, 500):
            error = subprocess.CalledProcessError(1, ['gh'], stderr=f'gh: Error (HTTP {code})\n'.encode())
            with self.subTest(code=code), patch('compatibility.intake.subprocess.run', side_effect=error):
                with self.assertRaises(i.ResourceUnavailable if code in (404, 410) else i.IntakeError) as result:
                    api.run(self.source)
                self.assertEqual(type(result.exception), i.ResourceUnavailable if code in (404, 410) else i.IntakeError)
                with self.assertRaises(i.IntakeError) as listing:
                    api.issues('open')
                self.assertIs(type(listing.exception), i.IntakeError)

    def test_duplicate_pair_fails_closed(self):
        i.file_issue(self.api, self.record, self.source)
        row = self.api.issue(11)
        self.api.create(row['title'], row['body'])
        with self.assertRaises(i.IntakeError):
            i.file_issue(self.api, self.record, self.source)
        self.assertEqual(self.api.posts, 2)


    def test_exact_proof_is_required_for_intake_and_issue_creation(self):
        i.file_issue(self.api, self.record, self.source)
        wrong = i.Pair(D('d'), i.ADAPTER_DIGEST, 'unsupported-version')
        with self.assertRaises(i.IntakeError):
            i.file_issue(self.api, wrong, self.source)
        self.api.evidence[self.source] = wrong
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(i.scan(self.api, Path(d)/'ledger.sqlite'), [])

    def test_proof_archive_reads_one_typed_entry_without_extracting(self):
        for name in ('proof.json', '../proof.json', 'unexpected.py'):
            b = io.BytesIO()
            with ZipFile(b, 'w') as archive:
                archive.writestr(name, json.dumps(asdict(self.record)))
            if name == 'proof.json':
                self.assertEqual(i.decode_proof(b.getvalue()), self.record)
            else:
                with self.assertRaises(i.IntakeError):
                    i.decode_proof(b.getvalue())
