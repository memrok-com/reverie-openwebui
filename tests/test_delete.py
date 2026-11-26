"""Fabricated native deletion and restart contracts; no host database access."""
import tempfile
import unittest
from contextlib import closing
from unittest.mock import AsyncMock
from test_functions import load


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.n, _, _ = load('event')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.n['DB'] = self.tmp.name + '/records.sqlite'
        self.present = {'scene', 'survivor'}
        self.n['chat_exists'] = AsyncMock(side_effect=lambda owner, cid: cid in self.present)
        self.n['rpc'] = AsyncMock(return_value={'episodeId':'scene', 'invalidatedEpisodes':2})
        self.n['register_scene']('owner', 'character', 'scene')

    async def test_native_owner_not_actor_and_retry_after_restart(self):
        n = self.n
        self.present.remove('scene')
        n['rpc'].side_effect = n['SceneError']('OFFLINE', 'offline')
        await n['Event']().event({'event':'chat.deleted', 'actor':{'id':'admin'},
            'subject':{'type':'chat','id':'scene'}, 'data':{'owner_id':'owner'}})
        n['rpc'].assert_awaited_once_with('character', 'forget', {'episodeId':'scene'})
        n['rpc'].side_effect = None
        await n['reconcile']('owner', 'character')
        self.assertEqual(n['rpc'].await_count, 2)
        await n['reconcile']('owner', 'character')
        self.assertEqual(n['rpc'].await_count, 2)

    async def test_crash_after_forget_before_local_cleanup_retries_idempotently(self):
        import sqlite3
        n = self.n
        self.present.remove('scene')
        original_open = n['db_open']
        def unavailable():
            raise sqlite3.OperationalError('fabricated interrupted local cleanup')
        async def forgotten(*args):
            n['db_open'] = unavailable
            return {'episodeId':'scene','invalidatedEpisodes':0}
        n['rpc'].side_effect = forgotten
        with self.assertRaises(sqlite3.Error):
            await n['reconcile']('owner', 'character')
        n['db_open'] = original_open
        n['rpc'].side_effect = None
        await n['reconcile']('owner', 'character')
        self.assertEqual(n['rpc'].await_count, 2)
        with closing(n['db_open']()) as db:
            self.assertEqual(db.execute('SELECT cleaned FROM scenes WHERE chat=?', ('scene',)).fetchone(), (1,))

    async def test_ordinary_irrelevant_and_reversible_events_do_not_forget(self):
        for event in (
            {'event':'chat.deleted','subject':{'type':'chat','id':'ordinary'},'data':{'owner_id':'owner'}},
            {'event':'chat.deleted','subject':{'type':'chat','id':'scene'},'data':{'owner_id':'admin'}},
            {'event':'chat.updated'},
            {'event':'folder.deleted','data':{'delete_contents':False,'folder_ids':['folder']}},
            {'event':'chat.deleted','subject':{'type':'chat','id':'scene'},'data':{'owner_id':'owner'}},
        ):
            await self.n['Event']().event(event)
        self.n['rpc'].assert_not_awaited()

    async def test_database_error_is_not_absence(self):
        n = self.n
        n['chat_exists'].side_effect = n['SceneError']('SOURCE_STATE', 'unavailable')
        with self.assertRaises(n['SceneError']):
            await n['reconcile']('owner', 'character')
        n['rpc'].assert_not_awaited()

    async def test_cleanup_fences_staged_and_anchor_writes_from_another_worker(self):
        n = self.n
        n['register_scene']('owner', 'character', 'survivor')
        token = n['generation']('character')
        self.present.remove('scene')
        await n['reconcile']('owner', 'character')
        for write, args in (
            ('stage', ('character', 'survivor', 'reply', 'prefix', {'at': 0})),
            ('publish', ('character', 'survivor', 'reply', [])),
            ('set_anchor', ('character', 'survivor', 'reply', [])),
        ):
            with self.subTest(write=write), self.assertRaisesRegex(n['SceneError'], 'Memory changed'):
                n[write](*args, expected=token)
        with closing(n['db_open']()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
        self.assertIsNone(n['anchor']('character', 'survivor'))

    async def test_failed_forget_blocks_and_success_clears_derived_cache_preserves_anchors(self):
        n = self.n
        n['register_scene']('owner', 'character', 'survivor')
        n['set_anchor']('character', 'survivor', 'reply', [])
        n['stage']('character', 'survivor', 'reply', 'prefix', {'at':0,'memories':[{'gist':'removed'}]})
        self.present.remove('scene')
        token = n['generation']('character')
        for episode, count in (('wrong', 0), ('scene', []), ('scene', True), ('scene', -1)):
            n['rpc'].return_value = {'episodeId':episode, 'invalidatedEpisodes':count}
            with self.assertRaisesRegex(n['SceneError'], 'cleanup was not verified'):
                await n['guard']('owner', 'character', token)
        n['rpc'].return_value = {'episodeId':'scene','invalidatedEpisodes':2}
        with self.assertRaises(n['SceneError']):
            await n['guard']('owner', 'character', token)
        await n['guard']('owner', 'character')
        self.assertIsNotNone(n['anchor']('character', 'survivor'))
        with closing(n['db_open']()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)

    async def test_registration_rechecks_presence_and_cross_character_ownership(self):
        n = self.n
        with self.assertRaises(n['SceneError']):
            n['register_scene']('owner', 'another', 'scene')
        self.present.remove('scene')
        await n['guard']('owner', 'character')
        n['rpc'].assert_awaited_once()

    async def test_missed_hook_restart_finds_unanchored_scene(self):
        self.present.remove('scene')
        restarted, _, _ = load('event')
        restarted['DB'] = self.n['DB']
        restarted['chat_exists'] = self.n['chat_exists']
        restarted['rpc'] = self.n['rpc']
        await restarted['guard']('owner', 'character')
        restarted['rpc'].assert_awaited_once_with('character', 'forget', {'episodeId':'scene'})

    async def test_bulk_and_cascaded_folder_events_check_presence_only(self):
        self.present.remove('scene')
        await self.n['Event']().event({'event':'folder.deleted', 'subject':{'type':'folder','id':'outer'},
            'data':{'folder_ids':['outer','inner'], 'delete_contents':True}})
        self.n['rpc'].assert_awaited_once()
        self.n['register_scene']('owner', 'character', 'survivor')
        self.present.remove('survivor')
        self.n['rpc'].return_value = {'episodeId':'survivor','invalidatedEpisodes':0}
        await self.n['Event']().event({'event':'chat.deleted_all','subject':{'type':'user','id':'owner'},'data':{}})
        self.assertEqual(self.n['rpc'].await_count, 2)

    async def test_deleted_account_cleans_subject_across_continuities_not_actor(self):
        n = self.n
        n['register_scene']('owner', 'second', 'second-scene')
        n['register_scene']('admin', 'admin-character', 'admin-scene')
        for character, chat in (('character', 'scene'), ('second', 'second-scene'),
                                ('admin-character', 'admin-scene')):
            n['set_anchor'](character, chat, 'reply', [])
        self.present.clear()
        n['rpc'].side_effect = lambda _, method, params: {
            'episodeId':params['episodeId'], 'invalidatedEpisodes':1}
        event = {'event':'user.deleted', 'actor':{'id':'admin'},
                 'subject':{'type':'user','id':'owner'}, 'data':{}}
        for invalid in ({'type':'chat','id':'owner'}, {'type':'user','id':''}):
            await n['Event']().event({**event, 'subject':invalid})
        n['chat_exists'].assert_not_awaited()
        await n['Event']().event(event)
        await n['Event']().event(event)
        self.assertCountEqual([call.args for call in n['rpc'].await_args_list], [
            ('character', 'forget', {'episodeId':'scene'}),
            ('second', 'forget', {'episodeId':'second-scene'}),
        ])
        self.assertCountEqual([call.args for call in n['chat_exists'].await_args_list], [
            ('owner', 'scene'), ('owner', 'second-scene'),
        ])
        self.assertIsNone(n['anchor']('character', 'scene'))
        self.assertIsNone(n['anchor']('second', 'second-scene'))
        self.assertIsNotNone(n['anchor']('admin-character', 'admin-scene'))

    async def test_legacy_anchor_is_not_silently_trusted(self):
        self.n['set_anchor']('character', 'legacy', 'reply', [])
        with self.assertRaisesRegex(self.n['SceneError'], 'clean-install'):
            await self.n['guard']('owner', 'character')
        self.n['rpc'].assert_not_awaited()


class UseRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import types
        self.n, _, _ = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        n = self.n
        n['DB'] = self.tmp.name + '/records.sqlite'
        self.present = {'scene', 'survivor'}
        n['chat_exists'] = AsyncMock(side_effect=lambda owner, cid: cid in self.present)
        self.messages = [{'id':'p','role':'user','content':'Synthetic harbor','parentId':None},
                         {'id':'r','role':'assistant','content':'Synthetic reply','parentId':'p','done':True}]
        self.chat = types.SimpleNamespace(id='survivor', chat={'history':{'messages':{m['id']:m for m in self.messages}}})
        n['scope'] = AsyncMock(return_value=('character', self.chat))
        n['register_scene']('owner', 'character', 'scene')
        n['register_scene']('owner', 'character', 'survivor')
        n['set_anchor']('character', 'survivor', 'r', n['played'](self.chat, 'r'))
        self.body = {'messages':[{'role':'user','content':'Synthetic harbor'}]}
        self.metadata = {'user_message_id':'p','message_id':'r'}

    async def inlet(self):
        return await self.n['Filter']().inlet(self.body, {'id':'owner'}, 'survivor', self.metadata, AsyncMock())

    async def test_forget_failure_blocks_retrieve_then_independent_recall_resumes(self):
        n = self.n
        self.present.remove('scene')
        n['rpc'] = AsyncMock(side_effect=n['SceneError']('OFFLINE', 'cleanup unavailable'))
        with self.assertRaisesRegex(ValueError, 'cleanup unavailable'):
            await self.inlet()
        self.assertEqual([c.args[1] for c in n['rpc'].await_args_list], ['forget'])
        n['rpc'] = AsyncMock(side_effect=[{'episodeId':'scene','invalidatedEpisodes':2},
            {'now':0}, {'text':'Independent earlier memory','historicalText':'','memories':[]}])
        await self.inlet()
        self.assertIn('Independent earlier memory', str(self.body))
        self.assertEqual([c.args[1] for c in n['rpc'].await_args_list], ['forget','when','retrieve'])

    async def test_deletion_during_retrieve_never_injects_or_publishes_old_context(self):
        n = self.n
        async def rpc(character, method, params):
            if method == 'when':
                return {'now':0}
            if method == 'retrieve':
                self.present.remove('scene')
                return {'text':'DELETED DERIVATIVE','historicalText':'','memories':[]}
            return {'episodeId':'scene','invalidatedEpisodes':2}
        n['rpc'] = AsyncMock(side_effect=rpc)
        with self.assertRaisesRegex(ValueError, 'Memory changed'):
            await self.inlet()
        self.assertNotIn('DELETED DERIVATIVE', str(self.body))
        self.assertFalse(n['publish']('character','survivor','r',n['played'](self.chat,'r')))

    async def test_outlet_after_cleanup_does_not_display_cleaned_provenance(self):
        n = self.n
        awaitable = AsyncMock(side_effect=[{'now':0},{'text':'','historicalText':'','memories':[]}])
        n['rpc'] = awaitable
        await self.inlet()
        self.present.remove('scene')
        n['rpc'] = AsyncMock(return_value={'episodeId':'scene','invalidatedEpisodes':0})
        event = AsyncMock()
        await n['Filter']().outlet({'chat_id':'survivor','id':'r','messages':[{'finish_reason':'stop'}]}, {'id':'owner'}, event)
        event.assert_not_awaited()
        self.assertEqual(n['records']('character','survivor',n['played'](self.chat,'r')), {})

    async def test_reflection_result_after_deletion_is_not_returned_or_anchored(self):
        n = self.n
        exec((__import__('pathlib').Path(__file__).resolve().parents[1]/'functions/memorize.py').read_text(), n)
        n['action_scope'] = AsyncMock(return_value=('character', self.chat))
        n['source'] = AsyncMock(return_value={'messages':n['played'](self.chat,'r')})
        def provider(request):
            self.present.remove('survivor')
            return '{"memories":[]}'
        n['reflect_request'] = provider
        # Execute the fabricated provider deterministically without a worker thread.
        import types
        async def in_thread(fn, *args):
            return fn(*args)
        n['asyncio'] = types.SimpleNamespace(to_thread=in_thread)
        async def rpc(character, method, params, callback=None):
            if method == 'forget':
                return {'episodeId':params['episodeId'],'invalidatedEpisodes':0}
            messages = [{'id':f'm{i}', 'role':m['role'], 'text':m['text'],
                         'speaker':'player' if m['role']=='user' else 'companion'}
                        for i,m in enumerate(n['played'](self.chat,'r'),1)]
            response = await callback({'method':'model.reflect','params':{'episodeId':'survivor',
                'characterId':'character','modelKey':n['MODEL_KEY'],'request':{'input':{'episode':{'messages':messages}}}}}, '/unused')
            self.assertIn('error', response)
            return {'status':'published'}
        n['rpc'] = AsyncMock(side_effect=rpc)
        event = AsyncMock()
        await n['Action']().action({'id':'r'}, {'id':'owner'}, AsyncMock(return_value=True), event)
        self.assertIsNone(n['anchor']('character','survivor'))
        status = [c.args[0]['data'] for c in event.await_args_list if c.args[0]['type'] == 'status']
        self.assertEqual([item['done'] for item in status], [False, True])
        self.assertIn('not verified', status[-1]['description'])

    async def test_inspector_rechecks_before_displaying_cached_record(self):
        from pathlib import Path
        n = self.n
        exec((Path(__file__).resolve().parents[1]/'functions/inspect.py').read_text(), n)
        n['action_scope'] = AsyncMock(return_value=('character', self.chat))
        real_records = n['records']
        def read_then_delete(*args):
            self.present.remove('scene')
            return real_records(*args)
        n['records'] = read_then_delete
        n['rpc'] = AsyncMock(return_value={'episodeId':'scene','invalidatedEpisodes':0})
        popup = AsyncMock()
        emitter = AsyncMock()
        await n['Action']().action({'id':'r'}, {'id':'owner'}, popup, emitter)
        popup.assert_not_awaited()
        self.assertIn('Memory changed', emitter.await_args.args[0]['data']['content'])

    async def test_registration_precedes_first_date_rpc_and_rechecks_deletion(self):
        n = self.n
        with closing(n['db_open']()) as db, db:
            db.execute('DELETE FROM scenes')
            db.execute('DELETE FROM published')
        async def exists(owner, cid):
            with closing(n['db_open']()) as db:
                self.assertEqual(db.execute('SELECT owner,character FROM scenes WHERE chat=?', ('survivor',)).fetchone(), ('owner','character'))
            return False
        n['chat_exists'] = exists
        n['rpc'] = AsyncMock(return_value={'episodeId':'survivor','invalidatedEpisodes':0})
        with self.assertRaisesRegex(ValueError, 'scene was deleted'):
            await self.inlet()
        self.assertTrue(all(c.args[1] == 'forget' for c in n['rpc'].await_args_list))


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimal_owner_bound_select_and_database_error(self):
        import sys
        import types
        from contextlib import asynccontextmanager
        from unittest.mock import Mock, patch
        n, _, _ = load()
        column_id, column_owner = Mock(), Mock()
        column_id.__eq__ = Mock(return_value='id-bound')
        column_owner.__eq__ = Mock(return_value='owner-bound')
        query = Mock()
        select = Mock(return_value=query)
        result = Mock()
        db = types.SimpleNamespace(execute=AsyncMock(return_value=result))
        @asynccontextmanager
        async def context():
            yield db
        modules = {'sqlalchemy':types.SimpleNamespace(select=select),
            'open_webui':types.ModuleType('open_webui'),
            'open_webui.internal':types.ModuleType('open_webui.internal'),
            'open_webui.internal.db':types.SimpleNamespace(get_async_db_context=context),
            'open_webui.models':types.ModuleType('open_webui.models'),
            'open_webui.models.chats':types.SimpleNamespace(Chat=types.SimpleNamespace(id=column_id,user_id=column_owner))}
        with patch.dict(sys.modules, modules):
            for value in ('scene', None):
                result.scalar_one_or_none.return_value = value
                self.assertEqual(await n['chat_exists']('owner','scene'), value is not None)
            select.assert_called_with(column_id)
            column_id.__eq__.assert_called_with('scene')
            column_owner.__eq__.assert_called_with('owner')
            query.where.assert_called_with('id-bound','owner-bound')
            db.execute.side_effect = RuntimeError('private database detail')
            with self.assertRaises(n['SceneError']) as error:
                await n['chat_exists']('owner','scene')
            self.assertNotIn('private database detail', str(error.exception))

    async def test_legacy_current_anchor_cannot_be_adopted_by_registration(self):
        import tempfile
        n, _, _ = load()
        with tempfile.TemporaryDirectory() as data:
            n['DB'] = data + '/records.sqlite'
            n['set_anchor']('character', 'scene', 'reply', [])
            with self.assertRaises(n['SceneError']):
                n['register_scene']('owner', 'character', 'scene')
