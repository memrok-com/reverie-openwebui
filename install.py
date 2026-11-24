"""Reproducible v0.1 bundle and explicit local-only clean installation; stdlib only."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
UI_IMAGE = 'ghcr.io/open-webui/open-webui@sha256:0c4439e78c24c85e3e7a41936771d3b02f0df38daf28a086cd83ca74804f7676'
ENGINE_REVISION = 'd679b6fc8cdcd329946f5dce74dc0610319b7f0f'

class InstallError(Exception):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def bundle(base_model, user_id):
    common = (ROOT/'functions/common.py').read_text()
    functions = []
    for kind, name in (('filter','Reverie continuity'), ('inspect','What reached this reply?'), ('memorize','Memorize through this reply'), ('event','Reverie native deletion')):
        content = ('"""\ntitle: '+name+'\nversion: 0.1.0\n"""\n'+common+'\n'+(ROOT/f'functions/{kind}.py').read_text())
        functions.append({'id':'reverie_'+kind, 'name':name, 'content':content, 'meta':{}})
    model = {'id':'reverie-companion', 'name':'Reverie Companion', 'base_model_id':base_model,
        'meta':{'description':'Lets your character recall scenes you’ve memorized in this folder. Set as the folder default; choosing it for one chat doesn’t turn memory on.',
                'capabilities':{'memory':False,'builtin_tools':False},
                'actionIds':['reverie_inspect','reverie_memorize']},
        'params':{}, 'access_grants':[{'principal_type':'user','principal_id':user_id,'permission':'read'}]}
    return {'format':1, 'open_webui':'0.13.4', 'ui_image':UI_IMAGE,
            'reverie':'0.3.0', 'engine_revision':ENGINE_REVISION, 'functions':functions,'model':model}

class Client:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost','127.0.0.1','::1')
                or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise InstallError('Only an explicit loopback HTTP URL is supported. Run inside the UI container against loopback.')
        if not token or '\n' in token or '\r' in token:
            raise InstallError('Provide an authorized administrator token via stdin.')
        self.url, self.token = url.rstrip('/'), token

    def call(self, path, value=None):
        request = Request(self.url+path, data=json.dumps(value).encode() if value is not None else None,
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},
                          method='POST' if value is not None else 'GET')
        try:
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            raise InstallError('Local API request failed. No credentials or response bodies are logged. Check state before retrying.') from None

def items(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) or not isinstance(x.get('id'),str) for x in value):
        raise InstallError('Unexpected list response; no further writes are safe.')
    return value

def verify_function(value, expected, global_filter=False):
    if (not isinstance(value,dict) or any(value.get(k) != expected[k] for k in ('id','name','content'))
            or value.get('is_active') is not True or value.get('is_global') is not global_filter
            or (expected['id'] == 'reverie_event' and value.get('type') != 'event')):
        raise InstallError('Function readback differed from the bundle; stop and inspect the installation.')


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(matches(actual.get(k), v) for k,v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(matches(a,b) for a,b in zip(actual,expected))
    return actual == expected

def apply(client, package):
    version = client.call('/api/version')
    if not isinstance(version, dict) or version.get('version') != '0.13.4':
        raise InstallError('Only exact Open WebUI 0.13.4 is supported; nothing was written.')
    # Complete all conflict and base-model checks before the first write.
    existing_functions = items(client.call('/api/v1/functions/'))
    existing_models = items(client.call('/api/v1/models/all'))
    available = items(client.call('/api/models').get('data'))
    if package['model']['base_model_id'] not in {x['id'] for x in available}:
        raise InstallError('The requested base model is not available; nothing was written.')
    ids = {x['id'] for x in package['functions']} | {package['model']['id']}
    if ids & {x['id'] for x in existing_functions+existing_models+available}:
        raise InstallError('Reverie IDs already exist. Overwrites and toggle retries are refused; inspect the existing installation.')
    for function in package['functions']:
        client.call('/api/v1/functions/create', function)
        route = '/api/v1/functions/id/'+function['id']
        client.call(route+'/toggle', {})
        if function['id'] == 'reverie_filter':
            client.call(route+'/toggle/global', {})
        verify_function(client.call(route), function, function['id']=='reverie_filter')
    client.call('/api/v1/models/create', package['model'])
    saved = client.call('/api/v1/models/model?id='+package['model']['id'])
    if not matches(saved, package['model']):
        raise InstallError('Preset readback differed from the bundle; inspect the installation.')
    visible = items(client.call('/api/models').get('data'))
    if package['model']['id'] not in {x['id'] for x in visible}:
        raise InstallError('Preset was not present in model discovery after creation.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('bundle','apply'))
    parser.add_argument('--base-model', required=True)
    parser.add_argument('--user-id', required=True, help='Ordinary user to receive preset read access')
    parser.add_argument('--output', help='New bundle file (refuses overwrite); otherwise stdout')
    parser.add_argument('--url', help='Explicit loopback URL, required for apply')
    parser.add_argument('--token-stdin', action='store_true')
    args = parser.parse_args()
    try:
        package = bundle(args.base_model,args.user_id)
        if args.command == 'bundle':
            raw = json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2)+'\n'
            if args.output:
                with open(args.output,'x') as output:
                    output.write(raw)
            else:
                sys.stdout.write(raw)
        else:
            if not args.url or args.output:
                raise InstallError('Apply requires --url and does not accept --output.')
            if not args.token_stdin:
                raise InstallError('Apply requires --token-stdin.')
            token = sys.stdin.readline().rstrip('\r\n')
            apply(Client(args.url,token), package)
            print('Created and verified four Functions, active Event, global Filter and Reverie Companion preset.')
    except (InstallError, OSError) as error:
        parser.exit(1, str(error)+'\n')

if __name__ == '__main__':
    main()
