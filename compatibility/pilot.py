#!/usr/bin/env python3
"""Report-only released-artifact compatibility pilot. Docker runs only on explicit invocation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

if __package__:
    from .inside import PROMPTS, PASSWORD
else:
    from inside import PROMPTS, PASSWORD

ROOT = Path(__file__).resolve().parents[1]
UI = 'ghcr.io/open-webui/open-webui@sha256:d6c085678f7e3f474aa2a92ac9831cb68759dcfd951de250ebbb37dac5cd0255'
ADAPTER = 'ghcr.io/memrok-com/reverie-openwebui@sha256:58932d1336f75614a63ac06b49150a4dc6606d4d1e0cdf90aba5eb20cbe974d1'
TOKEN = 'synthetic-only-pilot-never-use-in-production-00000000'


def compose(ui=UI):
    """No inherited Compose configuration, host credentials, or production volumes."""
    guard = {'networks': ['private'], 'cap_drop': ['ALL'],
             'security_opt': ['no-new-privileges:true'], 'pids_limit': 256}
    return {'services': {
        'ui': {**guard, 'image': ui, 'platform': 'linux/amd64', 'pull_policy': 'never',
            'ports': ['127.0.0.1::8080'], 'mem_limit': '3g',
            'environment': {
                'ENABLE_OLLAMA_API': 'false', 'ENABLE_OPENAI_API': 'true',
                'OPENAI_API_BASE_URL': 'http://fixture:8000/v1',
                'OPENAI_API_KEY': 'synthetic-not-a-secret', 'DEFAULT_USER_ROLE': 'user',
                'ENABLE_SIGNUP': 'true', 'ENABLE_PERSISTENT_CONFIG': 'false',
                'ENABLE_VERSION_UPDATE_CHECK': 'false', 'ENABLE_TITLE_GENERATION': 'false',
                'ENABLE_TAGS_GENERATION': 'false', 'ENABLE_FOLLOW_UP_GENERATION': 'false',
                'ENABLE_AUTOCOMPLETE_GENERATION': 'false', 'OFFLINE_MODE': 'true',
                'HF_HUB_OFFLINE': '1', 'DO_NOT_TRACK': 'true', 'RAG_EMBEDDING_ENGINE': 'openai',
                'WEBUI_SECRET_KEY': 'synthetic-only-pilot-session-signing-key',
                'REVERIE_ADAPTER_TOKEN': TOKEN, 'REVERIE_ADAPTER_URL': 'http://adapter:8766',
                'REVERIE_REFLECTION_URL': 'http://fixture:8000/v1/chat/completions',
                'REVERIE_REFLECTION_MODEL': 'synthetic-companion',
                'REVERIE_REFLECTION_REASONING_EFFORT': 'none'},
            'volumes': ['ui_data:/app/backend/data',
                {'type': 'bind', 'source': str(ROOT/'compatibility/inside.py'),
                 'target': '/pilot/inside.py', 'read_only': True}],
            'depends_on': ['adapter', 'fixture']},
        'adapter': {**guard, 'image': ADAPTER, 'platform': 'linux/amd64', 'pull_policy': 'never',
            'user': '10001:10001', 'read_only': True, 'mem_limit': '256m',
            'environment': {'REVERIE_ADAPTER_TOKEN': TOKEN}, 'volumes': ['engine_data:/data']},
        'fixture': {**guard, 'image': UI, 'platform': 'linux/amd64', 'pull_policy': 'never',
            'entrypoint': ['python3', '-B', '/pilot/fixture.py'], 'read_only': True,
            'healthcheck': {'disable': True}, 'mem_limit': '128m',
            'volumes': [{'type': 'bind', 'source': str(ROOT/'demo/fixture.py'),
                         'target': '/pilot/fixture.py', 'read_only': True}]}},
        'networks': {'private': {'internal': True}}, 'volumes': {'ui_data': {}, 'engine_data': {}}}


def evaluate(evidence):
    """Pass requires independent persisted completion, publication and provider evidence."""
    chats = evidence.get('chats', {})
    if set(chats) != set(PROMPTS) or any(not c.get('completed') for c in chats.values()):
        return 'indeterminate', 'missing-completed-native-chats'
    ordinary, source, later = (chats[k] for k in ('ordinary', 'source', 'later'))
    if len({c['chat'] for c in chats.values()}) != 3:
        return 'indeterminate', 'distinct-chats-required'
    if not source.get('published') or not source.get('record') or not later.get('record'):
        return 'indeterminate', 'missing-publication-or-provenance'
    replies = {e.get('promptHash'): e for e in evidence.get('fixture', []) if e.get('kind') == 'reply'}
    observed = [replies.get(hashlib.sha256(PROMPTS[k].encode()).hexdigest()) for k in PROMPTS]
    reflections = [e for e in evidence.get('fixture', []) if e.get('kind') == 'reflection']
    if not all(observed) or not reflections:
        return 'indeterminate', 'missing-provider-evidence'
    if not any(e.get('sourceHash') == source.get('reflectionHash')
               and e.get('messageIds') == ['m1', 'm2']
               and e.get('reasoningOffRequested') is True for e in reflections):
        return 'indeterminate', 'reflection-not-bound-to-saved-source'
    if ordinary.get('folder') or ordinary.get('record') or observed[0].get('hasCompassRecall'):
        return 'incompatible', 'ordinary-chat-isolation'
    if (not source.get('folder') or source['folder'] != later.get('folder')
            or source.get('at') != 1717200000000 or later.get('at') != 1719792000000):
        return 'indeterminate', 'wrong-folder-or-story-dates'
    if source.get('memorySources') or observed[1].get('hasCompassRecall'):
        return 'incompatible', 'unexpected-initial-recall'
    if source['chat'] not in later.get('memorySources', []) or not observed[2].get('hasCompassRecall'):
        return 'incompatible', 'published-memory-not-recalled'
    if not observed[2].get('hasUncertainRecall') or observed[2].get('hasInspectorCopy'):
        return 'incompatible', 'recall-context-contract'
    return 'tested/pass', 'native-synthetic-install-chat-memorize-recall'


class Docker:
    def __init__(self, config, project, manifest):
        # Empty credential store, fixed local context, no inherited DOCKER/COMPOSE settings.
        self.env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(config)}
        self.base = ['docker', '--config', str(config), '--context', 'default']
        self.prefix = ['compose', '--project-name', project, '--file', str(manifest)]

    def run(self, args, data=None, timeout=180):
        process = subprocess.run(self.base + args, input=data, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=self.env, timeout=timeout)
        if process.returncode:
            raise RuntimeError('docker-command-failed')
        return process.stdout

    def compose(self, args, **kwargs):
        return self.run(self.prefix + args, **kwargs)

    def inside(self, mode):
        return json.loads(self.compose(['exec', '-T', 'ui', 'python3', '-B', '/pilot/inside.py', mode]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; refuses overwrite')
    parser.add_argument('--ui-digest', default=UI.split('@', 1)[1])
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', args.ui_digest):
        parser.error('--ui-digest requires a full lowercase sha256 digest')
    ui = UI.split('@', 1)[0] + '@' + args.ui_digest
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    project = 'reverie-pilot-' + uuid.uuid4().hex
    manifest = output/'compose.json'
    manifest.write_text(json.dumps(compose(ui), indent=2) + '\n')
    report = {'status': 'indeterminate', 'check': 'not-started', 'project': project,
              'platform': 'linux/amd64', 'required_version': '0.13.4', 'ui': ui, 'adapter': ADAPTER,
              'upgrade': 'not-tested', 'images': {}, 'cleanup': 'not-needed',
              'flow_sha256': hashlib.sha256((ROOT/'demo/flow.json').read_bytes()).hexdigest()}
    started = False
    step = 'docker-preflight'
    with tempfile.TemporaryDirectory(prefix='reverie-pilot-docker-') as config:
        docker = Docker(Path(config), project, manifest)
        try:
            # Refuse any pre-existing project resources, including stopped containers.
            for kind in ('container', 'volume', 'network'):
                all_containers = ['--all'] if kind == 'container' else []
                if docker.run([kind, 'ls', '--quiet', *all_containers,
                               '--filter', 'label=com.docker.compose.project=' + project]).strip():
                    raise RuntimeError('project-collision')
            step = 'digest-pull-and-inspect'
            for ref in dict.fromkeys((UI, ui, ADAPTER)):
                docker.run(['pull', '--platform', 'linux/amd64', ref], timeout=900)
                metadata = json.loads(docker.run(['image', 'inspect', ref]))[0]
                if metadata['Architecture'] != 'amd64' or metadata['Os'] != 'linux' or ref not in metadata['RepoDigests']:
                    raise RuntimeError('image-identity-mismatch')
                report['images'][ref] = metadata['Id']
            started = True
            step = 'compose-start-and-image-identity'
            docker.compose(['up', '-d', '--pull', 'never'], timeout=300)
            for service, ref in (('ui', ui), ('adapter', ADAPTER), ('fixture', UI)):
                cid = docker.compose(['ps', '-q', service]).decode().strip()
                actual = json.loads(docker.run(['inspect', cid]))[0]
                if actual['Image'] != report['images'][ref]:
                    raise RuntimeError('container-image-mismatch')
            step = 'ui-readiness'
            for attempt in range(120):
                try:
                    docker.compose(['exec', '-T', 'ui', 'python3', '-c',
                        'from urllib.request import urlopen; urlopen("http://127.0.0.1:8080/api/version", timeout=2)'], timeout=5)
                    break
                except (RuntimeError, subprocess.TimeoutExpired):
                    if attempt == 119:
                        raise RuntimeError('ui-readiness-timeout')
                    time.sleep(2)
            step = 'released-package-transfer'
            docker.compose(['exec', '-T', 'ui', 'mkdir', '/tmp/compat-package'])
            package = docker.compose(['exec', '-T', 'adapter', 'tar', '-C', '/package', '-cf', '-', 'install.py', 'functions'])
            docker.compose(['exec', '-T', 'ui', 'tar', '--no-same-owner', '-C', '/tmp/compat-package', '-xf', '-'], data=package)
            report['package_sha256'] = hashlib.sha256(package).hexdigest()
            step = 'synthetic-bootstrap'
            bootstrap = docker.inside('bootstrap')
            report['bootstrap'] = bootstrap
            if bootstrap.get('status') != 'ready':
                report.update(status=bootstrap.get('status', 'indeterminate'), check=bootstrap.get('check', 'bootstrap-failed'))
            else:
                step = 'headless-journey'
                address = docker.compose(['port', 'ui', '8080']).decode().strip()
                if __package__:
                    from .browser import check
                else:
                    from browser import check
                evidence = check('http://' + address, PROMPTS, PASSWORD, lambda: docker.inside('collect'))
                (output/'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
                report['status'], report['check'] = evaluate(evidence)
        except KeyboardInterrupt:
            report.update(status='indeterminate', check='interrupted')
        except Exception:
            # Docker errors can contain registry/proxy information; do not persist their output.
            report.update(status='indeterminate', check=step)
        finally:
            if started:
                try:
                    docker.compose(['down', '--volumes', '--timeout', '15'], timeout=120)
                    report['cleanup'] = 'scoped-compose-down-completed'
                except Exception:
                    report.update(status='indeterminate', cleanup='failed', check='scoped-cleanup-failed')
            (output/'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return {'tested/pass': 0, 'incompatible': 1, 'indeterminate': 2}[report['status']]


if __name__ == '__main__':
    sys.exit(main())
