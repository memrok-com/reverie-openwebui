"""Offline validation for digest-bound, report-only Actions dispatches."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import intake as i


def dispatch(ui_digest, correlation, env):
    """Return the mode; never silently downgrade a malformed pinned request."""
    if ui_digest == correlation == '':
        return 'legacy'
    if not i.digest(ui_digest) or not i.matches(correlation, '[a-f0-9]{32,64}'):
        raise i.IntakeError('invalid-pinned-inputs')
    if (env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REPOSITORY') != i.REPO
            or env.get('GITHUB_REPOSITORY_ID') != str(i.REPO_ID)
            or not env.get('GITHUB_REF', '').startswith('refs/heads/')):
        raise i.IntakeError('invalid-pinned-context')
    return 'pinned'


def verdict(candidate, first, repeat=None, control=None):
    """Bind a narrow verdict to pilot identities and completed scoped cleanup."""
    if not i.digest(candidate):
        raise i.IntakeError('invalid-candidate')
    ref = i.UI_REPO + '@' + candidate
    if (i.observation(first, ref, 'tested/pass') and first.get('check') == i.PASS
            and first.get('bootstrap') == i.READY
            and first['bootstrap']['install'] is True and first['bootstrap']['login'] is True):
        installer, runtime, check = 'supported', 'pass', 'tested/pass'
    else:
        pair = i.qualify(candidate, first, repeat or {}, control or {})
        if pair is None:
            raise i.IntakeError('indeterminate-report')
        check = pair.check
        installer = 'unsupported-version' if check == 'unsupported-version' else 'supported'
        runtime = 'failed' if check in i.JOURNEY else 'not-tested'
    return dict(schema=1, ui_digest=candidate, adapter_digest=i.ADAPTER_DIGEST,
                installer=installer, runtime=runtime, check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest='cmd', required=True)
    cmds.add_parser('dispatch')
    report = cmds.add_parser('report')
    report.add_argument('--candidate', required=True)
    for name in ('first', 'repeat', 'control'):
        report.add_argument('--' + name, type=Path, required=name == 'first')
    args = parser.parse_args()
    try:
        if args.cmd == 'dispatch':
            print(dispatch(os.environ.get('INPUT_UI_DIGEST', ''),
                           os.environ.get('INPUT_CORRELATION', ''), os.environ))
        else:
            rows = [i.read_object(path) if path else None for path in (args.first, args.repeat, args.control)]
            print(json.dumps(verdict(args.candidate, *rows), separators=(',', ':')))
    except (i.IntakeError, OSError, ValueError, TypeError, KeyError, AttributeError):
        print('report: invalid inputs or indeterminate evidence', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
