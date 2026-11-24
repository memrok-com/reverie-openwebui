"""Offline package and synthetic reflection transport contracts."""
import io
import json
import os
import unittest
from unittest.mock import patch

import install
from demo.fixture import completion, reflection
from test_functions import load

class PackageTests(unittest.TestCase):
    def test_bundle_reproducible_and_native_attachment(self):
        package = install.bundle('synthetic-companion','synthetic-user')
        self.assertEqual(package,install.bundle('synthetic-companion','synthetic-user'))
        self.assertEqual(package['model']['meta']['actionIds'],['reverie_inspect','reverie_memorize'])
        self.assertEqual(package['model']['meta']['capabilities'],{'memory':False,'builtin_tools':False})
        self.assertIn('recall scenes you’ve memorized', package['model']['meta']['description'])
        self.assertIn('folder default', package['model']['meta']['description'])
        for function in package['functions']:
            compile(function['content'], function['id'], 'exec')
            self.assertNotIn('__OPTED__',function['content'])
        self.assertEqual([x['id'] for x in package['functions']],['reverie_filter','reverie_inspect','reverie_memorize','reverie_event'])

    def test_installer_rejects_nonlocal_and_conflicts_before_writes(self):
        for url in ('https://example.invalid','http://localhost.evil','http://127.0.0.1/x','http://user@localhost','http://localhost?x=y'):
            with self.assertRaises(install.InstallError):
                install.Client(url,'synthetic-token')
        class Fake:
            def __init__(self): self.writes = []
            def call(self,path,value=None):
                if value is not None: self.writes.append(path)
                if path=='/api/version': return {'version':'0.13.4'}
                if path=='/api/v1/functions/': return [{'id':'reverie_filter'}]
                if path=='/api/v1/models/all': return []
                if path=='/api/models': return {'data':[{'id':'synthetic-companion'}]}
        client = Fake()
        with self.assertRaises(install.InstallError):
            install.apply(client,install.bundle('synthetic-companion','synthetic-user'))
        self.assertEqual(client.writes,[])

    def test_installer_version_failure_precedes_all_other_calls(self):
        for version in ({'version':'0.13.3'}, {'version':'0.13.4-dev'}, {}, []):
            class Fake:
                def call(self, path, value=None):
                    if path != '/api/version' or value is not None:
                        raise AssertionError('API used before version check')
                    return version
            with self.assertRaises(install.InstallError):
                install.apply(Fake(), install.bundle('synthetic', 'synthetic'))

    def test_adapter_wire_requires_server_token_and_never_copies_body_override(self):
        ns, _, _ = load()
        sent = []
        class Opener:
            def open(self, request, timeout):
                sent.append(request)
                return io.BytesIO(b'{}')
        token = 'synthetic-only-adapter-token-000000000000'
        with patch.dict(ns, {'build_opener': lambda *args: Opener()}):
            for invalid in ('', 'short', 'x' * 32 + ' ', 'é' * 32):
                with patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN':invalid}, clear=True):
                    with self.assertRaises(ns['SceneError']):
                        ns['wire']('/jobs')
            self.assertEqual(sent, [])
            with patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN':token}, clear=True):
                ns['wire']('/jobs', {'REVERIE_ADAPTER_TOKEN':'client-override'})
            self.assertEqual(sent[0].get_header('Authorization'), 'Bearer ' + token)
            self.assertIsNone(ns['NoRedirect']().redirect_request(None, None, 302, '', {}, 'http://example.invalid'))

    def test_reflection_uses_configured_real_transport_and_returns_json_string(self):
        ns,_,_ = load('memorize')
        request = {'system':'Synthetic script contract', 'maxTokens':1000, 'input':{'episode':{'id':'synthetic-episode','messages':[
            {'id':'p','role':'user','text':'Synthetic compass'},
            {'id':'r','role':'assistant','text':'ALT_BRANCH'}]}}}
        sent = []
        class Opener:
            def open(self,req,timeout):
                sent.append((req,timeout))
                body = json.loads(req.data)
                content = completion(body)
                return io.BytesIO(json.dumps({'choices':[{'message':{'content':content}}]}).encode())
        env = {'REVERIE_ADAPTER_TOKEN':'synthetic-only-adapter-token-000000000000', 'REVERIE_REFLECTION_URL':'http://fixture:8000/v1/chat/completions','REVERIE_REFLECTION_MODEL':'synthetic-companion'}
        with patch.dict(os.environ,env,clear=True), patch.dict(ns,{'build_opener':lambda *args:Opener()}):
            result = ns['reflect_request']({**request, 'reasoning_effort': 'high'})
            self.assertNotIn('reasoning_effort', json.loads(sent[-1][0].data))
            os.environ['REVERIE_REFLECTION_REASONING_EFFORT'] = 'none'
            self.assertEqual(ns['reflect_request'](request), result)
            self.assertEqual(json.loads(sent[-1][0].data)['reasoning_effort'], 'none')
            os.environ['REVERIE_REFLECTION_REASONING_EFFORT'] = 'off'
            with self.assertRaisesRegex(ns['SceneError'], 'reasoning'):
                ns['reflect_request'](request)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(req.get_header('Authorization') is None for req, _ in sent))
        self.assertIsInstance(result,str)
        self.assertIn('ALT_BRANCH',json.loads(result)['memories'][0]['gist'])
        self.assertEqual(json.loads(sent[0][0].data)['model'],'synthetic-companion')
        self.assertEqual(json.loads(sent[0][0].data)['messages'][0]['content'],request['system'])
        self.assertEqual(json.loads(sent[0][0].data)['max_tokens'],request['maxTokens'])
        self.assertEqual(sent[0][1],30)
        self.assertEqual(json.loads(result)['memories'][0]['sourceMessageIds'],['p','r'])

    def test_reflection_requires_server_config_and_sanitizes_failure(self):
        ns,_,_ = load()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ns['SceneError']): ns['reflect_request']({})
        class BadOpener:
            def open(self,*args,**kwargs): raise ValueError('DO NOT EXPOSE CONTENT')
        with patch.dict(os.environ,{'REVERIE_REFLECTION_URL':'http://fixture/v1/chat/completions','REVERIE_REFLECTION_MODEL':'synthetic-companion'},clear=True), patch.dict(ns,{'build_opener':lambda *args:BadOpener()}):
            with self.assertRaises(ns['SceneError']) as error: ns['reflect_request']({'system':'fixture','input':{},'maxTokens':10})
            self.assertNotIn('DO NOT EXPOSE',str(error.exception))

    def test_fixture_is_branch_specific_and_can_fail(self):
        request = {'input':{'episode':{'id':'e','messages':[{'id':'p','role':'user','text':'[fail-reflection]'}]}}}
        with self.assertRaises(ValueError): reflection(request)


class EventInstallationTests(unittest.TestCase):
    def test_clean_install_activates_event_without_global_flag(self):
        package = install.bundle('synthetic', 'owner')
        class Fake:
            def __init__(self):
                self.functions = {}
                self.model = None
                self.writes = []
            def call(self, path, value=None):
                if value is not None:
                    self.writes.append(path)
                if path == '/api/version': return {'version':'0.13.4'}
                if path == '/api/v1/functions/': return []
                if path == '/api/v1/models/all': return []
                if path == '/api/models':
                    return {'data':[{'id':'synthetic'}] + ([self.model] if self.model else [])}
                if path == '/api/v1/functions/create':
                    assert isinstance(value, dict)
                    kind = 'event' if value['id']=='reverie_event' else 'filter' if value['id']=='reverie_filter' else 'action'
                    self.functions[value['id']] = {**value, 'type':kind, 'is_active':False, 'is_global':False}
                elif path.startswith('/api/v1/functions/id/'):
                    parts = path.split('/')
                    function = self.functions[parts[5]]
                    if path.endswith('/global'): function['is_global'] = True
                    elif path.endswith('/toggle'): function['is_active'] = True
                    return function
                elif path == '/api/v1/models/create': self.model = value
                elif path.startswith('/api/v1/models/model?'): return self.model
        client = Fake()
        install.apply(client, package)
        event = client.functions['reverie_event']
        self.assertTrue(event['is_active'])
        self.assertFalse(event['is_global'])
        self.assertTrue(client.functions['reverie_filter']['is_global'])
        ns, _, _ = load('event')
        self.assertTrue(callable(ns['Event']().event))

if __name__=='__main__': unittest.main()
