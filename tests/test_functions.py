"""Synthetic Function contracts; no installed host or engine required."""
import asyncio
import io
import os
import sys
import tempfile
import types
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]

def load(kind='filter'):
    chats = types.SimpleNamespace(get_chat_by_id_and_user_id=AsyncMock())
    folders = types.SimpleNamespace(get_folder_by_id_and_user_id=AsyncMock())
    modules = {'open_webui': types.ModuleType('open_webui'),
               'open_webui.models': types.ModuleType('open_webui.models'),
               'open_webui.models.chats': types.SimpleNamespace(Chats=chats),
               'open_webui.models.folders': types.SimpleNamespace(Folders=folders),
               'open_webui.models.access_grants': types.SimpleNamespace(AccessGrants=types.SimpleNamespace(get_grants_by_resource=AsyncMock(return_value=[])))}
    ns = {}
    with patch.dict(sys.modules, modules):
        exec((ROOT/'functions/common.py').read_text()+'\n'+(ROOT/f'functions/{kind}.py').read_text(), ns)
    return ns, chats, folders

class FunctionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns, self.chats, self.folders = load()
        self.chat = types.SimpleNamespace(id='c', user_id='u', folder_id='f', share_id=None,
            chat={'history':{'messages':{'p':{'id':'p','role':'user','content':'Synthetic harbor','parentId':None},
            'r':{'id':'r','role':'assistant','content':'Synthetic compass','parentId':'p','done':True}}, 'currentId':'r'}})
        self.folder = types.SimpleNamespace(id='f', user_id='u', data={'model_ids':['reverie-companion']})
        self.chats.get_chat_by_id_and_user_id.return_value = self.chat
        self.folders.get_folder_by_id_and_user_id.return_value = self.folder
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ns['DB'] = self.temp.name+'/records.sqlite'

    async def test_ordinary_chat_without_token_is_noop(self):
        self.folder.data = {}
        body = {'messages': [{'role': 'user', 'content': 'ordinary'}]}
        with patch.dict(os.environ, {}, clear=True), patch.dict(self.ns, {'wire': unittest.mock.Mock(side_effect=AssertionError('network'))}):
            self.assertIs(await self.ns['Filter']().inlet(body, {'id':'u'}, 'c', {}, AsyncMock()), body)
        self.assertFalse(Path(self.ns['DB']).exists())

    async def test_scope_requires_owned_private_folder_default(self):
        self.assertIsNotNone(await self.ns['scope']({'id':'u'}, 'c'))
        for data in ({}, {'model_ids':'reverie-companion'}, {'model_ids':['other']}, None):
            self.folder.data = data
            self.assertIsNone(await self.ns['scope']({'id':'u'}, 'c'))
        self.folder.data = {'model_ids':['reverie-companion']}
        for target, field, value in ((self.chat,'user_id','other'),(self.folder,'user_id','other'),(self.chat,'share_id','shared')):
            old = getattr(target, field)
            setattr(target, field, value)
            with self.assertRaises(self.ns['SceneError']):
                await self.ns['scope']({'id':'u'}, 'c')
            setattr(target, field, old)
        self.ns['AccessGrants'].get_grants_by_resource.return_value = [object()]
        with self.assertRaisesRegex(self.ns['SceneError'],'unshared'):
            await self.ns['scope']({'id':'u'}, 'c')

    async def test_date_strict_bounded_and_blank_cancel(self):
        for answers in (['2024-2-01','2024-02-30','20240101'], ['']):
            event = AsyncMock(side_effect=answers)
            with self.assertRaises(self.ns['SceneError']):
                await self.ns['ask_date'](event)
            self.assertEqual(event.await_count, len(answers))
        event = AsyncMock(side_effect=['invalid','2024-02-29'])
        self.assertEqual(await self.ns['ask_date'](event), '2024-02-29')

    async def test_failed_retry_preserves_completed_record(self):
        n = self.ns
        prefix = n['played'](self.chat,'r')
        recall = {'at':0,'memories':[],'text':'','historicalText':''}
        n['stage']('x','c','r',n['fingerprint'](prefix[:-1]),recall)
        n['publish']('x','c','r',prefix)
        n['stage']('x','c','r','failed-retry',{'memories':[{'gist':'unplayed'}]})
        self.assertEqual(n['records']('x','c',prefix)['r'],{'at':0,'memories':[]})

    async def test_cancel_stops_before_retrieve_and_provider(self):
        n = self.ns
        n['verify_published_sources'] = AsyncMock()
        n['rpc'] = AsyncMock(side_effect=n['SceneError']('EPISODE_TIME_REQUIRED','date'))
        event = AsyncMock(return_value='')
        body = {'messages':[{'role':'user','content':'Synthetic harbor'}]}
        with self.assertRaisesRegex(ValueError,'Try Again'):
            await n['Filter']().inlet(body,{'id':'u'},'c',{'user_message_id':'p','message_id':'new'},event)
        self.assertEqual(n['rpc'].await_count,1)
        self.assertEqual(body['messages'],[{'role':'user','content':'Synthetic harbor'}])

    async def test_unavailable_source_blocks_until_original_is_returned_or_folder_is_disabled(self):
        n = self.ns
        user = {'id':'u'}
        old = types.SimpleNamespace(**vars(self.chat))
        old.id = 'archived-scene'
        self.chats.get_chat_by_id_and_user_id.side_effect = lambda cid, uid: (
            self.chat if cid == 'c' else old if cid == 'archived-scene' else None)
        character, _ = await n['scope'](user, 'c')
        prefix = n['played'](old, 'r')
        n['set_anchor'](character, old.id, 'r', prefix)
        n['rpc'] = AsyncMock(side_effect=[{'now': 0}, {'text': '', 'historicalText': ''}])
        filt = n['Filter']()
        body = {'messages':[{'role':'user','content':'Synthetic harbor'}]}
        metadata = {'user_message_id':'p','message_id':'new'}

        old.folder_id = None  # Open WebUI clears folder_id on both archive and unarchive.
        with self.assertRaisesRegex(ValueError, 'unarchive.*original folder'):
            await filt.inlet(body, user, 'c', metadata, AsyncMock())
        n['rpc'].assert_not_awaited()
        self.assertEqual(n['anchor'](character, old.id), ('r', n['fingerprint'](prefix)))

        old.folder_id = self.folder.id
        self.assertIs(await filt.inlet(body, user, 'c', metadata, AsyncMock()), body)
        self.assertEqual(n['rpc'].await_count, 2)
        n['rpc'].reset_mock()

        self.chats.get_chat_by_id_and_user_id.side_effect = lambda cid, uid: self.chat if cid == 'c' else None
        with self.assertRaisesRegex(ValueError, 'If it was deleted, disable Reverie'):
            await filt.inlet(body, user, 'c', metadata, AsyncMock())
        self.folder.data = {'model_ids':['ordinary']}
        self.assertIs(await filt.inlet(body, user, 'c', metadata, AsyncMock()), body)
        n['rpc'].assert_not_awaited()
        self.assertEqual(n['anchor'](character, old.id), ('r', n['fingerprint'](prefix)))
        self.assertEqual(body, {'messages':[{'role':'user','content':'Synthetic harbor'}]})

    async def test_changed_private_source_keeps_re_memoize_recovery(self):
        n = self.ns
        character, _ = await n['scope']({'id':'u'}, 'c')
        n['set_anchor'](character, 'c', 'r', n['played'](self.chat, 'r'))
        self.chat.chat['history']['messages']['r']['content'] = 'Edited reply'
        with self.assertRaisesRegex(n['SceneError'], 'Memorize the selected branch again') as cm:
            await n['verify_published_sources']({'id':'u'}, character)
        self.assertEqual(cm.exception.code, 'STALE_PUBLISHED_SOURCE')

    async def test_unanchored_recall_identifies_only_a_scene_in_this_folder(self):
        n = self.ns
        other = types.SimpleNamespace(**vars(self.chat))
        other.id = '11111111-1111-4111-8111-111111111111'
        other.title = 'Synthetic Compass'
        self.chats.get_chat_by_id_and_user_id.side_effect = lambda cid, uid: (
            self.chat if cid == 'c' else other if cid == other.id else None)
        self.folders.get_folder_by_id_and_user_id.side_effect = lambda fid, uid: (
            self.folder if fid == 'f' else types.SimpleNamespace(id=fid, user_id=uid, data=self.folder.data))
        body = {'messages':[{'role':'user','content':'Synthetic harbor'}]}
        for folder, expected in (('f', f'/c/{other.id}.*Memorize'), ('other', 'no longer available')):
            other.folder_id = folder
            n['rpc'] = AsyncMock(side_effect=[{'now': 0}, {'memories':[{'episodeId':other.id}]}])
            with self.assertRaisesRegex(ValueError, expected) as cm:
                await n['Filter']().inlet(body, {'id':'u'}, 'c',
                    {'user_message_id':'p','message_id':'new'}, AsyncMock())
            self.assertNotIn(other.title, str(cm.exception))
            if folder != 'f':
                self.assertNotIn(other.id, str(cm.exception))
            self.assertEqual(n['rpc'].await_count, 2)
            self.assertEqual(len(body['messages']), 1)

    async def test_job_start_reports_only_known_conflict_as_busy(self):
        n = self.ns
        for path, status, payload, expected in (
            ('/jobs', 409, b'{"error":{"code":"CHARACTER_BUSY"}}', 'CHARACTER_BUSY'),
            ('/jobs', 409, b'{"error":{"code":"OTHER"}}', 'REVERIE_UNAVAILABLE'),
            ('/jobs', 409, b'<not json>', 'REVERIE_UNAVAILABLE'),
            ('/jobs/abc/callback', 409, b'{"error":{"code":"CHARACTER_BUSY"}}', 'REVERIE_UNAVAILABLE'),
            ('/jobs', 401, b'{"error":{"code":"CHARACTER_BUSY"}}', 'REVERIE_UNAVAILABLE'),
        ):
            with self.subTest(path=path, status=status, payload=payload):
                opener = Mock()
                opener.open.side_effect = HTTPError(n['BASE'] + path, status, '', Message(), io.BytesIO(payload))
                with patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN':'x'*32}), \
                        patch.dict(n, {'build_opener':Mock(return_value=opener)}), \
                        self.assertRaises(n['SceneError']) as cm:
                    n['wire'](path, {})
                self.assertEqual(cm.exception.code, expected)

    async def test_unsupported_enabled_request_is_refused_before_engine(self):
        n = self.ns
        n['rpc'] = AsyncMock()
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            await n['Filter']().inlet({'messages':[], 'files':['synthetic']},{'id':'u'},'c',{'user_message_id':'p','message_id':'new'},AsyncMock())
        n['rpc'].assert_not_awaited()

    async def test_story_date_emitted_once_for_verified_completed_reply(self):
        n = self.ns
        n['verify_published_sources'] = AsyncMock()
        n['rpc'] = AsyncMock(side_effect=[{'now': 0}, {'text': '', 'historicalText': ''}])
        event = AsyncMock()
        user = {'id': 'u'}
        filt = n['Filter']()
        await filt.inlet({'messages': [{'role': 'user', 'content': 'Synthetic harbor'}]},
                         user, 'c', {'user_message_id': 'p', 'message_id': 'r'}, AsyncMock(), event)
        outlet = {'chat_id': 'c', 'id': 'r', 'messages': [{'finish_reason': 'length'}]}
        await filt.outlet(outlet, user, event)
        event.assert_not_awaited()
        outlet['messages'][0]['finish_reason'] = 'stop'
        await filt.outlet(outlet, user, event)
        await filt.outlet(outlet, user, event)
        event.assert_awaited_once_with({'type': 'status', 'data': {
            'description': 'Story date: 1970-01-01', 'done': True}})

    async def test_outside_folder_actions_only_show_help(self):
        n = self.ns
        self.folder.data = {}
        event = AsyncMock()
        n['rpc'] = AsyncMock()
        self.assertIsNone(await n['action_scope']({'id':'u'},{'chat_id':'c'},event))
        self.assertIn('No memory was read or saved',event.call_args.args[0]['data']['message'])
        n['rpc'].assert_not_awaited()

    async def test_inspector_uses_structured_fields(self):
        result = self.ns['readable_record']({'at':0,'text':'PRIVATE PROMPT MUST NOT DISPLAY','historicalText':'hidden',
            'memories':[{'gist':'I remember a compass','detail':'Warm brass','feeling':'Safe','episodeId':'e'}]})
        self.assertIn('compass',result)
        self.assertNotIn('PRIVATE PROMPT',result)
        self.assertNotIn('hidden',result)
        self.assertNotEqual(self.ns['readable_record'](None),self.ns['readable_record']({'at':0,'memories':[]}))

if __name__ == '__main__':
    unittest.main()
