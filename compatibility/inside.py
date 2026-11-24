"""Container-side synthetic bootstrap and read-only evidence collection (stdlib)."""
import json
import hashlib
import sqlite3
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PASSWORD = 'Synthetic-Pilot-Only-9384!'
PROMPTS = {
    'ordinary': 'SYNTH_PILOT_ORDINARY compass harbor',
    'source': 'SYNTH_PILOT_SOURCE I received a brass compass at the harbor.',
    'later': 'SYNTH_PILOT_LATER What compass did I keep at the harbor?',
}


def api(path, value=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request('http://127.0.0.1:8080' + path,
                      data=None if value is None else json.dumps(value).encode(), headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def bootstrap():
    # Import only the package extracted from the released sidecar.
    sys.path.insert(0, '/tmp/compat-package')
    import install
    version = api('/api/version').get('version')
    if version != '0.13.4':
        return {'status': 'incompatible', 'check': 'unsupported-version', 'version': version}
    steps = (
        ('first-admin-signup', '/api/v1/auths/signup', {'name': 'Synthetic admin', 'email': 'admin@pilot.invalid', 'password': PASSWORD}),
        ('admin-signin', '/api/v1/auths/signin', {'email': 'admin@pilot.invalid', 'password': PASSWORD}),
        ('admin-add-player', '/api/v1/auths/add', {'name': 'Synthetic player', 'role': 'user', 'email': 'player@pilot.invalid', 'password': PASSWORD}),
        ('player-signin', '/api/v1/auths/signin', {'email': 'player@pilot.invalid', 'password': PASSWORD}),
    )
    responses = {}
    for name, path, data in steps:
        try:
            # Stock signup closes after the first admin; use authenticated add for
            # the ordinary account, rather than enabling public signup again.
            token = responses['admin-signin']['token'] if name == 'admin-add-player' else None
            responses[name] = api(path, data, token)
        except HTTPError as error:
            return {'status': 'indeterminate', 'check': name, 'http_status': error.code}
    admin, player = responses['admin-signin'], responses['player-signin']
    if admin.get('role') != 'admin' or player.get('role') != 'user':
        return {'status': 'indeterminate', 'check': 'synthetic-account-roles'}
    try:
        install.apply(install.Client('http://127.0.0.1:8080', admin['token']),
                      install.bundle('synthetic-companion', player['id']))
    except install.InstallError as error:
        # Only explicit successful-readback contract mismatches establish incompatibility.
        mismatch = str(error).startswith(('Function readback differed', 'Preset readback differed',
                                          'Preset was not present'))
        return {'status': 'incompatible' if mismatch else 'indeterminate',
                'check': 'released-installer-readback' if mismatch else 'released-installer-failed'}
    return {'status': 'ready', 'version': version, 'login': True, 'install': True}


def collect():
    """Read native saved replies and adapter provenance; emit only IDs and booleans."""
    result = {'chats': {}}
    with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True) as db:
        owner = db.execute('SELECT id FROM user WHERE email=?', ('player@pilot.invalid',)).fetchone()[0]
        for chat_id, folder_id, raw in db.execute('SELECT id,folder_id,chat FROM chat WHERE user_id=?', (owner,)):
            messages = json.loads(raw).get('history', {}).get('messages', {})
            for key, prompt in PROMPTS.items():
                for message in messages.values():
                    if message.get('role') != 'user' or message.get('content') != prompt:
                        continue
                    replies = [m for m in messages.values() if m.get('parentId') == message.get('id')
                               and m.get('role') == 'assistant' and m.get('done') is True
                               and not m.get('error') and m.get('content')]
                    if len(replies) != 1 or key in result['chats']:
                        raise ValueError('ambiguous synthetic branch')
                    result['chats'][key] = {'chat': chat_id, 'reply': replies[0]['id'],
                        'folder': folder_id, 'completed': True}
                    if key == 'source':
                        # The released formation protocol supplies model-local aliases.
                        reflected = [{'id': 'm1', 'role': 'user', 'text': prompt, 'speaker': 'player'},
                                     {'id': 'm2', 'role': 'assistant', 'text': replies[0]['content'],
                                      'speaker': 'companion'}]
                        result['chats'][key]['reflectionHash'] = hashlib.sha256(
                            json.dumps(reflected, sort_keys=True).encode()).hexdigest()
    # A missing provenance database is missing evidence, never an empty success.
    with sqlite3.connect('file:/app/backend/data/reverie.sqlite?mode=ro', uri=True) as db:
        records = {(c, r): json.loads(v) for c, r, v in db.execute(
            'SELECT chat,reply,value FROM records WHERE live=1')}
        published = {(c, r) for c, r in db.execute('SELECT chat,reply FROM published')}
    for item in result['chats'].values():
        pair = (item['chat'], item['reply'])
        record = records.get(pair)
        item['record'] = record is not None
        item['published'] = pair in published
        if record is not None:
            item['at'] = record.get('at')
            item['memorySources'] = [m.get('episodeId') for m in record.get('memories', [])]
    with urlopen('http://fixture:8000/evidence', timeout=10) as response:
        result['fixture'] = json.load(response)
    return result


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'bootstrap':
            result = bootstrap()
        else:
            sys.path.insert(0, '/tmp/compat-package')
            result = collect()
        print(json.dumps(result))
    except Exception:
        # Never print HTTP responses, tokens, database content or tracebacks.
        print(json.dumps({'status': 'indeterminate', 'check': 'container-probe-failed'}))
        sys.exit(2)
