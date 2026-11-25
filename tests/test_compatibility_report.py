"""Synthetic offline coverage; no registry, Docker or GitHub calls."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from compatibility import intake as i, report as r
from test_compatibility_intake import D, report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY=i.REPO,
                        GITHUB_REPOSITORY_ID=str(i.REPO_ID), GITHUB_REF='refs/heads/main')
        self.candidate = D('c')
        self.first = report(self.candidate)
        self.repeat = report(self.candidate, project='b')
        self.control = report(i.UI_DIGEST, 'tested/pass', 'c')

    def test_paired_inputs_and_review_branch(self):
        self.assertEqual(r.dispatch('', '', {}), 'legacy')
        for ref in ('refs/heads/main', 'refs/heads/review-report'):
            for size in (32, 64):
                self.assertEqual(r.dispatch(self.candidate, 'a' * size, self.env | {'GITHUB_REF': ref}), 'pinned')
        for digest, nonce in [('', 'a'*32), (self.candidate, ''), (self.candidate, 'a'*31),
                              (self.candidate, 'a'*65), (self.candidate, 'A'*32),
                              (self.candidate, 'a'*32+'\n'), ('latest', 'a'*32),
                              ('ghcr.io/open-webui/open-webui@'+self.candidate, 'a'*32),
                              (D('A'), 'a'*32), (self.candidate+'\n', 'a'*32),
                              ('sha256:'+'a'*63, 'a'*32), (self.candidate, '$(id)')]:
            with self.subTest(digest=digest, nonce=nonce), self.assertRaises(i.IntakeError):
                r.dispatch(digest, nonce, self.env)

    def test_pinned_context_cannot_elevate_or_run_in_fork(self):
        for changes in [dict(GITHUB_REPOSITORY='fork/repo'), dict(GITHUB_REPOSITORY_ID='1'),
                        dict(GITHUB_REF='refs/tags/review'), dict(GITHUB_EVENT_NAME='pull_request'),
                        dict(GITHUB_EVENT_NAME='schedule')]:
            with self.subTest(changes=changes), self.assertRaises(i.IntakeError):
                r.dispatch(self.candidate, 'a'*32, self.env | changes)

    def test_exact_schema_and_supported_control_can_pass(self):
        for candidate in (self.candidate, i.UI_DIGEST):
            result = r.verdict(candidate, report(candidate, 'tested/pass'))
            self.assertEqual(result, dict(schema=1, ui_digest=candidate, adapter_digest=i.ADAPTER_DIGEST,
                                         installer='supported', runtime='pass', check='tested/pass'))

    def test_refusal_and_runtime_failure_remain_distinct(self):
        result = r.verdict(self.candidate, self.first, self.repeat, self.control)
        self.assertEqual((result['installer'], result['runtime'], result['check']),
                         ('unsupported-version', 'not-tested', 'unsupported-version'))
        for check in i.FINDINGS:
            first, repeat = copy.deepcopy(self.first), copy.deepcopy(self.repeat)
            for row in (first, repeat):
                row['check'] = check
                if check in i.JOURNEY:
                    row['bootstrap'] = i.READY.copy()
                elif check == 'released-installer-readback':
                    row['bootstrap'] = dict(status='incompatible', check=check)
            result = r.verdict(self.candidate, first, repeat, self.control)
            self.assertEqual(result['runtime'], 'failed' if check in i.JOURNEY else 'not-tested')
            self.assertEqual(result['installer'], 'unsupported-version' if check == 'unsupported-version' else 'supported')

    def test_negative_needs_existing_matching_repeat_and_green_control(self):
        for repeat, control in [(None, None), (self.repeat, None),
                                (self.repeat | {'check': 'unknown'}, self.control),
                                (self.repeat, self.control | {'status': 'indeterminate'}),
                                (self.repeat | {'package_sha256': '5'*64}, self.control),
                                (self.repeat | {'project': self.first['project']}, self.control)]:
            with self.subTest(repeat=repeat, control=control), self.assertRaises(i.IntakeError):
                r.verdict(self.candidate, self.first, repeat, control)

    def test_success_requires_validated_identity_bootstrap_and_cleanup(self):
        first = report(self.candidate, 'tested/pass')
        for changes in [dict(status='indeterminate'), dict(check='unknown'), dict(images={}),
                        dict(ui=i.UI), dict(adapter='other'), dict(cleanup='failed'),
                        dict(flow_sha256='5'*64), dict(package_sha256=None),
                        dict(bootstrap=i.READY | {'install': 1}), dict(platform='linux/arm64')]:
            with self.subTest(changes=changes), self.assertRaises(i.IntakeError):
                r.verdict(self.candidate, first | changes)

    def test_cli_never_emits_verdict_on_missing_or_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'result.json'
            for content in ('{}', '{"status":"tested/pass","status":"incompatible"}'):
                path.write_text(content)
                stdout = io.StringIO()
                with patch('sys.argv', ['report', 'report', '--candidate', self.candidate, '--first', str(path)]), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(r.main(), 1)
                self.assertEqual(stdout.getvalue(), '')
