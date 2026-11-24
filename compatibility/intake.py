"""Qualify a fresh-install incompatibility, file once, and verify intake receipts."""
import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Any
from zipfile import BadZipFile, ZipFile

from .pilot import ADAPTER, ROOT, UI

REPO = 'memrok-com/reverie-openwebui'
REPO_ID = 1469823243
API_PATH = 'repos/' + REPO
API_REPO = 'https://api.github.com/' + API_PATH
BOT, BOT_ID = 'github-actions[bot]', 41898282
WORKFLOW = '.github/workflows/compatibility.yml'
PROBE_JOB = 'Probe upstream latest'
EVENTS = ('schedule', 'workflow_dispatch')
UI_REPO, UI_DIGEST = UI.split('@')
ADAPTER_REPO, ADAPTER_DIGEST = ADAPTER.split('@')
CLEAN = 'scoped-compose-down-completed'
PASS = 'native-synthetic-install-chat-memorize-recall'
FLOW_HASH = hashlib.sha256((ROOT/'demo/flow.json').read_bytes()).hexdigest()
READY = {'status': 'ready', 'version': '0.13.4', 'login': True, 'install': True}
JOURNEY = {
    'ordinary-chat-isolation': 'Ordinary chat was not isolated from Reverie.',
    'unexpected-initial-recall': 'The source scene received memory before it was formed.',
    'published-memory-not-recalled': 'The later scene did not recall the published memory.',
    'recall-context-contract': 'The recalled context did not match the integration contract.',
}
FINDINGS = JOURNEY | {
    'unsupported-version': 'The candidate reports a version outside the released installer’s support gate. This is an installer refusal, not an observed runtime regression.',
    'released-installer-readback': 'The released installer could not verify the installed configuration.',
}
MARKER = '<!-- reverie-compatibility-v1 '


class IntakeError(Exception):
    """A stable, content-free intake failure."""


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def digest(value):
    return matches(value, r'sha256:[a-f0-9]{64}')


def positive(n):
    return type(n) is int and 0 < n <= 2**53 - 1


def object_json(data, max_size=8192):
    def unique(items):
        out = {}
        for key, value in items:
            if key in out:
                raise IntakeError('duplicate-json-key')
            out[key] = value
        return out

    try:
        if len(data) <= max_size:
            value = json.loads(data, object_pairs_hook=unique)
            if isinstance(value, dict):
                return value
    except (ValueError, RecursionError):
        pass
    raise IntakeError('invalid-json-object')


@dataclass(frozen=True)
class Pair:
    ui: str
    adapter: str
    check: str

    def __post_init__(self):
        if not digest(self.ui) or not digest(self.adapter) or self.check not in FINDINGS:
            raise IntakeError('invalid-pair')

    @property
    def key(self):
        return self.ui, self.adapter


@dataclass(frozen=True)
class Source:
    run: int
    attempt: int
    sha: str
    event: str

    def __post_init__(self):
        if not (positive(self.run) and positive(self.attempt) and matches(self.sha, '[a-f0-9]{40}')
                and self.event in EVENTS):
            raise IntakeError('invalid-run-identity')


def source_from_env(env):
    expected = {'REVERIE_COMPAT_ARMED': 'true', 'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': REPO,
                'GITHUB_REPOSITORY_ID': str(REPO_ID), 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': REPO + '/' + WORKFLOW + '@refs/heads/main'}
    if any(env.get(k) != v for k, v in expected.items()):
        raise IntakeError('untrusted-filing-context')
    numbers = [env.get(k, '') for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
    if not all(matches(n, '[1-9][0-9]{0,15}') for n in numbers):
        raise IntakeError('invalid-run-identity')
    return Source(int(numbers[0]), int(numbers[1]), env.get('GITHUB_SHA', ''), env.get('GITHUB_EVENT_NAME', ''))


def observation(r, expected_ui, status):
    images = r.get('images')
    return (r.get('status') == status and r.get('platform') == 'linux/amd64'
            and r.get('required_version') == '0.13.4' and r.get('upgrade') == 'not-tested'
            and r.get('ui') == expected_ui and r.get('adapter') == ADAPTER
            and r.get('cleanup') == CLEAN and r.get('flow_sha256') == FLOW_HASH
            and matches(r.get('package_sha256'), '[a-f0-9]{64}')
            and matches(r.get('project'), 'reverie-pilot-[a-f0-9]{32}')
            and isinstance(images, dict) and set(images) == {UI, expected_ui, ADAPTER}
            and all(digest(value) for value in images.values()))


def incompatible_bootstrap(check, bootstrap):
    if not isinstance(bootstrap, dict):
        return False
    if check == 'unsupported-version':
        version = bootstrap.get('version')
        return (bootstrap == {'status': 'incompatible', 'check': check, 'version': version}
                and matches(version, r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:[-+][a-zA-Z0-9.-]{1,48})?')
                and version != READY['version'])
    if check == 'released-installer-readback':
        return bootstrap == {'status': 'incompatible', 'check': check}
    return (isinstance(check, str) and check in JOURNEY and bootstrap == READY
            and bootstrap['login'] is True and bootstrap['install'] is True)


def qualify(candidate, first, repeat, control):
    """Refusal or a check failure repeated on distinct fresh installations plus a green control."""
    if not digest(candidate) or candidate == UI_DIGEST:
        return None
    ref = UI_REPO + '@' + candidate
    check = first.get('check')
    if not (observation(first, ref, 'incompatible') and observation(repeat, ref, 'incompatible')
            and observation(control, UI, 'tested/pass') and control.get('check') == PASS
            and control.get('bootstrap') == READY and control['bootstrap']['install'] is True
            and control['bootstrap']['login'] is True
            and check == repeat.get('check') and incompatible_bootstrap(check, first.get('bootstrap'))
            and first.get('bootstrap') == repeat.get('bootstrap')):
        return None
    if (len({r['project'] for r in (first, repeat, control)}) != 3
            or len({r['package_sha256'] for r in (first, repeat, control)}) != 1
            or first['images'] != repeat['images']
            or any(first['images'][k] != control['images'][k] for k in (UI, ADAPTER))
            or first['images'][ref] == control['images'][UI]):
        return None
    return Pair(candidate, ADAPTER_DIGEST, check)


def title(pair):
    return f'Open WebUI compatibility: {pair.ui[7:19]} / {pair.adapter[7:19]}'


def body(pair, source):
    meta = json.dumps({'pair': asdict(pair), 'source': asdict(source)}, sort_keys=True, separators=(',', ':'))
    evidence = f'https://github.com/{REPO}/actions/runs/{source.run}/attempts/{source.attempt}'
    return (f'{MARKER}{meta} -->\n'
            f'**Finding:** {FINDINGS[pair.check]}\n\n'
            f'- Candidate: `{UI_REPO}@{pair.ui}`\n'
            f'- Released adapter: `{ADAPTER_REPO}@{pair.adapter}`\n'
            f'- Evidence: [exact CI attempt]({evidence}) (`{source.sha}`); two matching candidate results '
            'and a passing supported control.\n'
            '- Scope: synthetic fresh install on Linux amd64. No upgrade, promotion, merge, '
            'or deployment decision.\n')


def pair_from_fields(fields):
    if (type(fields) is not dict or set(fields) != {'ui', 'adapter', 'check'}
            or any(not isinstance(x, str) for x in fields.values())):
        raise IntakeError('invalid-pair')
    return Pair(**fields)


def parse(row):
    text, author = row.get('body'), row.get('user')
    if (not isinstance(author, dict) or author.get('id') != BOT_ID or author.get('login') != BOT
            or not isinstance(text, str) or not text.startswith(MARKER) or 'pull_request' in row):
        return None
    if (len(text) > 4096 or row.get('repository_url') != API_REPO
            or not positive(row.get('number')) or not positive(row.get('id'))
            or row.get('state') not in ('open', 'closed')):
        raise IntakeError('invalid-machine-issue')
    first = text.split('\n', 1)[0]
    if not first.endswith(' -->'):
        raise IntakeError('invalid-machine-issue')
    fields = object_json(first[len(MARKER):-4], 1024)
    if set(fields) != {'pair', 'source'} or not isinstance(fields['source'], dict):
        raise IntakeError('invalid-machine-issue')
    pair = pair_from_fields(fields['pair'])
    try:
        source = Source(**fields['source'])
    except TypeError:
        raise IntakeError('invalid-machine-issue') from None
    if text != body(pair, source) or row.get('title') != title(pair):
        raise IntakeError('invalid-machine-issue')
    return pair, source


def prior(api, pair):
    related = []
    for row in api.issues('all'):
        ref = parse(row)
        if ref is not None and ref[0].key == pair.key:
            related.append((row, ref))
    if len(related) > 1:
        raise IntakeError('duplicate-pair-issues')
    return related[0] if related else None


def file_issue(api, pair, source):
    if pair.adapter != ADAPTER_DIGEST or pair.ui == UI_DIGEST:
        raise IntakeError('invalid-filing-pair')
    if prior(api, pair):
        return None
    if api.proof(source) != pair:
        raise IntakeError('filing-proof-mismatch')
    try:
        created = api.create(title(pair), body(pair, source))
        number = created.get('number')
        if not positive(number):
            raise IntakeError('ambiguous-create-response')
    except IntakeError:
        # A timed-out POST may have succeeded. Refetch once; never retry the write here.
        found = prior(api, pair)
        if not found or found[1] != (pair, source):
            raise IntakeError('create-unconfirmed') from None
        number = found[0]['number']
    row = api.issue(number)
    if row.get('number') != number or parse(row) != (pair, source):
        raise IntakeError('issue-readback-mismatch')
    return number


def verified(api, pair, source):
    run = api.run(source)
    # The notifier is itself a job in this attempt. Do not wait for it to finish.
    if (run.get('status') not in ('completed', 'in_progress') or run.get('id') != source.run
            or run.get('run_attempt') != source.attempt or run.get('path') != WORKFLOW
            or run.get('event') != source.event or run.get('head_branch') != 'main'
            or run.get('head_sha') != source.sha
            or any((run.get(k) or {}).get('id') != REPO_ID for k in ('repository', 'head_repository'))):
        return False
    jobs = [j for j in api.jobs(source) if j.get('name') == PROBE_JOB]
    return (len(jobs) == 1 and jobs[0].get('status') == 'completed' and jobs[0].get('conclusion') == 'success'
            and jobs[0].get('head_sha') == source.sha and jobs[0].get('run_id') == source.run
            and api.proof(source) == pair)


def ledger(state):
    fd = os.open(state, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
            raise IntakeError('unsafe-intake-ledger')
    finally:
        os.close(fd)
    return sqlite3.connect(state, timeout=5)


TABLE = ('CREATE TABLE IF NOT EXISTS intake (ui TEXT, adapter TEXT, issue INTEGER UNIQUE NOT NULL, '
         'receipt TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (acknowledged IN (0, 1)), '
         'PRIMARY KEY (ui, adapter))')


def matches_receipt(db, item):
    seen = db.execute('SELECT receipt, acknowledged FROM intake WHERE (ui = ? AND adapter = ?) OR issue = ?',
                      (item['ui'], item['adapter'], item['issue'])).fetchall()
    receipt = json.dumps(item, sort_keys=True, separators=(',', ':'))
    if seen and (len(seen) != 1 or seen[0][0] != receipt):
        raise IntakeError('conflicting-intake-receipt')
    return seen[0][1] if seen else None, receipt


def pending(state, rows):
    if not rows:
        return []
    # Deliberately no GitHub I/O while holding the write transaction.
    out = []
    with closing(ledger(state)) as db, db:
        db.execute(TABLE)
        db.execute('BEGIN IMMEDIATE')
        for item in rows:
            acknowledged, receipt = matches_receipt(db, item)
            if acknowledged is None:
                db.execute('INSERT INTO intake (ui, adapter, issue, receipt) VALUES (?, ?, ?, ?)',
                           (item['ui'], item['adapter'], item['issue'], receipt))
            if acknowledged != 1:
                out.append(item)
    return out


def acknowledge(state, issue):
    if not positive(issue) or not state.exists():
        raise IntakeError('unknown-intake-issue')
    with closing(ledger(state)) as db, db:
        db.execute(TABLE)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT acknowledged FROM intake WHERE issue = ?', (issue,)).fetchone()
        if row is None:
            raise IntakeError('unknown-intake-issue')
        if row[0]:
            return False
        db.execute('UPDATE intake SET acknowledged = 1 WHERE issue = ?', (issue,))
        return True


def scan(api, state):
    rows = api.issues('open')
    normalized = []
    seen = set()
    for stub in rows:
        try:
            ref = parse(stub)
        except IntakeError:
            continue  # Edited/malformed records are never agent input.
        if ref is None:
            continue
        n = stub['number']
        row = api.issue(n)
        try:
            if (row.get('number') != n or row.get('state') != 'open'
                    or row.get('id') != stub.get('id') or parse(row) != ref):
                continue
        except IntakeError:
            continue
        pair, source = ref
        if pair.key in seen:
            raise IntakeError('duplicate-pair-issues')
        seen.add(pair.key)
        row = {'issue': n, 'issue_id': row['id'], **asdict(pair), **asdict(source)}
        acknowledged = None
        if state.exists():
            with closing(ledger(state)) as db, db:
                db.execute(TABLE)
                acknowledged, _ = matches_receipt(db, row)
        if acknowledged == 0 or (acknowledged is None and verified(api, pair, source)):
            normalized.append(row)
    return pending(state, normalized)


class GitHub:
    def call(self, endpoint, *, post=None, pages=False, raw=False) -> Any:
        args = ['gh', 'api', '--hostname', 'github.com', API_PATH + '/' + endpoint]
        if pages:
            args += ['--paginate', '--slurp']
        if post is not None:
            args += ['--method', 'POST', '--input', '-']
        try:
            response = subprocess.run(args, input=json.dumps(post).encode() if post is not None else b'',
                                      capture_output=True, timeout=15, check=True,
                                      env=os.environ | {'GH_PROMPT_DISABLED': '1'})
            return response.stdout if raw else json.loads(response.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise IntakeError('github-request-failed') from None

    def issues(self, state):
        pages = self.call(f'issues?creator=github-actions%5Bbot%5D&state={state}&per_page=100', pages=True)
        return [row for page in pages for row in page]

    def issue(self, number):
        return self.call(f'issues/{number}')

    def create(self, title, body):
        return self.call('issues', post={'title': title, 'body': body, 'labels': [], 'assignees': []})

    def run(self, source):
        return self.call(f'actions/runs/{source.run}/attempts/{source.attempt}')

    def jobs(self, source):
        pages = self.call(f'actions/runs/{source.run}/attempts/{source.attempt}/jobs?per_page=100', pages=True)
        jobs = [job for page in pages for job in page['jobs']]
        if not pages or any(len(jobs) != p['total_count'] for p in pages):
            raise IntakeError('incomplete-attempt-jobs')
        return jobs

    def proof(self, source):
        name = f'compatibility-proof-{source.attempt}'
        pages = self.call(f'actions/runs/{source.run}/artifacts?name={name}&per_page=100', pages=True)
        artifacts = [a for page in pages for a in page['artifacts']]
        if not pages or any(len(artifacts) != p['total_count'] for p in pages):
            raise IntakeError('incomplete-attempt-artifacts')
        if len(artifacts) != 1:
            raise IntakeError('missing-or-ambiguous-proof')
        a = artifacts[0]
        size = a.get('size_in_bytes')
        if (a.get('name') != name or a.get('expired') is not False or not positive(a.get('id'))
                or not positive(size) or size > 4096 or not digest(a.get('digest'))):
            raise IntakeError('invalid-or-expired-proof')
        archive = self.call(f"actions/artifacts/{a['id']}/zip", raw=True)
        if len(archive) > 4096 or 'sha256:' + hashlib.sha256(archive).hexdigest() != a['digest']:
            raise IntakeError('proof-archive-mismatch')
        return decode_proof(archive)


def decode_proof(archive):
    # Never extract artifacts to disk or execute a file from the image-probe job.
    try:
        with ZipFile(io.BytesIO(archive)) as z:
            entries = z.infolist()
            if (len(entries) == 1 and entries[0].filename == 'proof.json'
                    and entries[0].file_size <= 1024):
                return pair_from_fields(object_json(z.read(entries[0]), 1024))
    except (BadZipFile, RuntimeError, NotImplementedError):
        pass
    raise IntakeError('invalid-proof-archive')


def read_object(path):
    with path.open('rb') as f:
        return object_json(f.read(8193))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    cmds = p.add_subparsers(dest='cmd', required=True)
    q = cmds.add_parser('qualify')
    q.add_argument('--candidate', required=True)
    for field in ('first', 'repeat', 'control'):
        q.add_argument('--' + field, type=Path, required=True)
    cmds.add_parser('file')
    s = cmds.add_parser('scan')
    s.add_argument('--state', required=True, type=Path)
    s.add_argument('--gate', action='store_true', help='Emit a Hermes pre-run gate envelope.')
    a = cmds.add_parser('ack')
    a.add_argument('--state', required=True, type=Path)
    a.add_argument('--issue', required=True, type=int)
    args = p.parse_args()
    try:
        if args.cmd == 'qualify':
            pair = qualify(args.candidate, *(read_object(path) for path in (args.first, args.repeat, args.control)))
            if pair:
                print(json.dumps(asdict(pair), separators=(',', ':')))
        elif args.cmd == 'file':
            source = source_from_env(os.environ)
            pair = pair_from_fields(object_json(sys.stdin.buffer.read(1025), 1024))
            number = file_issue(GitHub(), pair, source)
            if number:
                print(number)
        elif args.cmd == 'scan':
            rows = scan(GitHub(), args.state)
            if args.gate:
                print(json.dumps({'wakeAgent': bool(rows), 'issues': rows}, separators=(',', ':')))
            elif rows:
                print(json.dumps(rows, separators=(',', ':')))
        elif args.cmd == 'ack':
            print('acknowledged' if acknowledge(args.state, args.issue) else 'already acknowledged')
    except IntakeError as e:
        print(f'intake: {e}', file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, KeyError, ValueError, TypeError, AttributeError):
        print('intake: invalid-or-unavailable-source', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
