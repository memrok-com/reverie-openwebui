"""Read-only chat memory contracts with synthetic saved branches and engine replies."""
import copy
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import test_functions

load = test_functions.load


class ChatMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_functions.FunctionTests.asyncSetUp(self)
        self.ns, self.chats, self.folders = load('memory')
        self.ns['DB'] = self.temp.name + '/records.sqlite'
        self.ns['chat_exists'] = AsyncMock(return_value=True)
        self.chats.get_chat_by_id_and_user_id.return_value = self.chat
        self.folders.get_folder_by_id_and_user_id.return_value = self.folder
        self.character, _ = await self.ns['scope']({'id':'u'}, 'c')
        self.event, self.notice = AsyncMock(), AsyncMock()
        self.result = dict(characterId=self.character, episodeId='c', revision=1,
                           state='not-memorized', memories=[])
        self.ns['rpc'] = AsyncMock(side_effect=self.engine)
        for name in ('guard', 'reconcile', 'verify_published_sources', 'source', 'source_callback',
                     'reflect_request', 'register_scene', 'records', 'activations'):
            self.ns[name] = Mock(side_effect=AssertionError('forbidden ' + name))

    async def engine(self, character, method, params, callback):
        self.assertEqual((character, method, params), (self.character, 'inspectEpisode', {'episodeId':'c'}))
        if self.result['state'] == 'published':
            await callback(dict(method='source.read', params={'characterId':character, 'episodeId':'c'}), '')
        return copy.deepcopy(self.result)

    def anchor(self):
        self.original_register('u', self.character, 'c')
        self.ns['set_anchor'](self.character, 'c', 'r', self.ns['played'](self.chat, 'r'))
        self.ns['register_scene'] = Mock(side_effect=AssertionError('register'))
        self.result.update(state='published', memories=[dict(gist='Compass', detail='Brass', feeling='Safe', confidence=.7)])

    async def run_action(self, reply='r'):
        self.event.reset_mock()
        with patch.dict(self.ns, {'db_open':Mock(side_effect=AssertionError('DDL/write opening'))}):
            await self.ns['Action']().action({'chat_id':'c', 'id':reply}, {'id':'u'}, self.event, self.notice)
        return self.event.call_args.args[0]['data']['message'] if self.event.await_count else ''

    async def test_unknown_is_read_only_and_distinct_from_empty(self):
        text = await self.run_action()
        self.assertIn('Not memorized', text)
        self.assertFalse(Path(self.ns['DB']).exists())
        self.anchor()
        self.result['memories'] = []
        before = Path(self.ns['DB']).read_bytes()
        self.assertIn('No recollections', await self.run_action())
        self.assertEqual(Path(self.ns['DB']).read_bytes(), before)

    async def test_registered_without_anchor_never_supplies_a_source(self):
        self.original_register('u', self.character, 'c')
        before = Path(self.ns['DB']).read_bytes()
        self.assertIn('Not memorized', await self.run_action())
        self.ns['rpc'].assert_not_awaited()
        self.assertEqual(Path(self.ns['DB']).read_bytes(), before)

    async def test_current_selected_branch_and_boundary_from_older_button(self):
        self.anchor()
        messages = self.chat.chat['history']['messages']
        messages['p2'] = dict(id='p2', role='user', content='Later prompt', parentId='r')
        messages['r2'] = dict(id='r2', role='assistant', content='Later reply', parentId='p2', done=True)
        self.chat.chat['history']['currentId'] = 'r2'
        text = await self.run_action()
        self.assertIn('assistant reply 1', text)
        self.assertIn('Synthetic compass', text)
        self.assertIn('Later messages are not included', text)
        self.assertIn('Compass', text)
        self.assertEqual(await self.run_action('r2'), text)
        async def bounded_engine(character, method, params, callback):
            self.answer = await callback(dict(method='source.read', params={'characterId':character, 'episodeId':'c'}), '')
            return self.result
        self.ns['rpc'].side_effect = bounded_engine
        self.assertEqual(await self.run_action(), text)
        self.assertEqual([m['id'] for m in self.answer['result']['messages']], ['p', 'r'])
        messages['alt'] = dict(messages['r'], id='alt', content='Alternate')
        # Stock branch navigation may not update saved currentId.
        self.assertEqual(self.chat.chat['history']['currentId'], 'r2')
        self.assertIn('Needs re-memorizing', await self.run_action('alt'))
        self.assertNotIn('Compass', self.event.call_args.args[0]['data']['message'])

    async def test_reply_above_cutoff_uses_unambiguous_path_only(self):
        messages = self.chat.chat['history']['messages']
        messages['p2'] = dict(id='p2', role='user', content='Later prompt', parentId='r')
        messages['r2'] = dict(id='r2', role='assistant', content='Cutoff', parentId='p2', done=True)
        self.anchor()
        self.ns['set_anchor'](self.character, 'c', 'r2', self.ns['played'](self.chat, 'r2'))
        self.assertIn('assistant reply 2', await self.run_action())
        messages['other'] = dict(messages['r2'], id='other', content='Other branch')
        self.ns['rpc'].reset_mock()
        self.assertIn('under the latest reply', await self.run_action())
        self.ns['rpc'].assert_not_awaited()
        self.assertIn('Needs re-memorizing', await self.run_action('other'))

    async def test_edited_invalidated_and_stale_hide_text(self):
        self.anchor()
        for state in ('invalidated', 'stale'):
            self.result.update(state=state, memories=[])
            self.assertIn('Needs re-memorizing', await self.run_action())
        self.result.update(state='published', memories=[dict(gist='DO NOT SHOW', detail='', feeling='', confidence=1)])
        self.chat.chat['history']['messages']['r']['content'] = 'Changed'
        self.assertIn('Needs re-memorizing', await self.run_action())
        self.assertNotIn('DO NOT SHOW', self.event.call_args.args[0]['data']['message'])

    async def test_callback_exact_episode_once_fresh_source_without_recall(self):
        self.anchor()
        for bad in (dict(method='model.reflect', params={}),
                    dict(method='source.read', params={'characterId':self.character, 'episodeId':'other'}),
                    dict(method='source.read', params={'characterId':'other', 'episodeId':'c'})):
            answers = []
            async def engine(character, method, params, callback):
                answers.append(await callback(bad, 'unused'))
                frame = dict(method='source.read', params={'characterId':character, 'episodeId':'c'})
                answers.append(await callback(frame, 'unused'))
                return self.result
            self.ns['rpc'].side_effect = engine
            self.assertEqual(await self.run_action(), '')
            self.assertEqual(len(answers), 2)
            self.assertTrue(all('error' in answer for answer in answers))
        self.notice.assert_awaited()

    async def test_second_source_callback_poisons_result(self):
        self.anchor()
        answers = []
        async def engine(character, method, params, callback):
            frame = dict(method='source.read', params={'characterId':character, 'episodeId':'c'})
            answers.append(await callback(frame, ''))
            answers.append(await callback(frame, ''))
            return self.result
        self.ns['rpc'].side_effect = engine
        self.assertEqual(await self.run_action(), '')
        self.assertIn('result', answers[0])
        self.assertIn('error', answers[1])

    async def test_fresh_callback_refuses_removed_or_changed_source_identity(self):
        self.anchor()
        original = copy.deepcopy(self.chat.chat)
        for change in ('missing', 'role', 'identity', 'text'):
            answers = []
            async def engine(character, method, params, callback):
                message = self.chat.chat['history']['messages']['r']
                if change == 'missing': del self.chat.chat['history']['messages']['r']
                if change == 'role': message['role'] = 'user'
                if change == 'identity': message['id'] = 'different'
                if change == 'text': message['content'] = 'Changed'
                try:
                    answers.append(await callback(dict(method='source.read', params={'characterId':character, 'episodeId':'c'}), ''))
                except self.ns['SceneError']:
                    answers.append({'error':{}})
                return self.result
            self.ns['rpc'].side_effect = engine
            self.assertEqual(await self.run_action(), '')
            self.assertEqual(len(answers), 1)
            self.assertIn('error', answers[0])
            self.chat.chat = copy.deepcopy(original)

    async def test_valid_callback_and_scope_reads_only_current_chat(self):
        self.anchor()
        async def engine(character, method, params, callback):
            answer = await callback(dict(method='source.read', params={'characterId':character, 'episodeId':'c'}), '')
            self.assertEqual(set(answer['result']), {'foundation', 'messages'})
            return self.result
        self.ns['rpc'].side_effect = engine
        self.assertIn('Compass', await self.run_action())
        self.assertTrue(all(call.args == ('c', 'u') for call in self.chats.get_chat_by_id_and_user_id.call_args_list))

    async def test_owner_scope_and_other_episode_reply_refused(self):
        for change in ('owner', 'shared', 'folder', 'reply'):
            with self.subTest(change=change):
                self.event.reset_mock()
                if change == 'owner': self.chat.user_id = 'other'
                if change == 'shared': self.chat.share_id = 'shared'
                if change == 'folder': self.folder.data = {}
                await self.run_action('other-chat-reply' if change == 'reply' else 'r')
                self.ns['rpc'].assert_not_awaited()
                self.chat.user_id, self.chat.share_id = 'u', None
                self.folder.data = {'model_ids':['reverie-companion']}

    async def test_after_await_races_never_open_memory(self):
        self.anchor()
        for change in ('scope', 'anchor', 'branch', 'edit', 'generation', 'deleted'):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.chat)
                answers = []
                async def engine(character, method, params, callback):
                    answers.append(await callback(dict(method='source.read', params={'characterId':character, 'episodeId':'c'}), ''))
                    if change == 'scope': self.chat.share_id = 'shared'
                    if change == 'anchor':
                        with sqlite3.connect(self.ns['DB']) as db: db.execute('DELETE FROM published')
                    if change == 'branch': self.chat.chat['history']['messages']['r']['parentId'] = None
                    if change == 'edit': self.chat.chat['history']['messages']['r']['content'] = 'Changed'
                    if change == 'generation':
                        with sqlite3.connect(self.ns['DB']) as db: db.execute('INSERT OR REPLACE INTO generations VALUES (?,3)', (self.character,))
                    if change == 'deleted': self.ns['chat_exists'].return_value = False
                    return self.result
                self.ns['rpc'].side_effect = engine
                self.event.reset_mock()
                self.assertEqual(await self.run_action(), '')
                self.assertEqual(len(answers), 1)
                self.assertIn('result', answers[0])
                self.chat = saved
                self.chats.get_chat_by_id_and_user_id.return_value = self.chat
                self.ns['chat_exists'].return_value = True
                self.anchor()

    async def test_pending_native_deletion_only_checks_metadata_and_never_forgets(self):
        self.anchor()
        with sqlite3.connect(self.ns['DB']) as db:
            db.execute('INSERT INTO scenes VALUES (?,?,?,0)', ('u', self.character, 'deleted-other'))
        self.ns['chat_exists'].side_effect = lambda owner, cid: cid != 'deleted-other'
        self.assertEqual(await self.run_action(), '')
        self.ns['rpc'].assert_not_awaited()
        self.assertTrue(all(call.args == ('c', 'u') for call in self.chats.get_chat_by_id_and_user_id.call_args_list))

    async def test_corrupt_database_is_not_absence_and_errors_are_sanitized(self):
        Path(self.ns['DB']).write_bytes(b'corrupt')
        self.assertEqual(await self.run_action(), '')
        self.ns['rpc'].assert_not_awaited()
        self.assertNotIn('corrupt', str(self.notice.call_args))
        Path(self.ns['DB']).unlink()
        with sqlite3.connect(self.ns['DB']) as db: db.execute('CREATE TABLE unrelated (id TEXT)')
        self.assertEqual(await self.run_action(), '')
        self.ns['rpc'].assert_not_awaited()
        Path(self.ns['DB']).unlink()
        self.anchor()
        self.ns['rpc'].side_effect = self.ns['SceneError']('SECRET', 'PRIVATE ENGINE CONTENT')
        self.assertEqual(await self.run_action(), '')
        self.assertNotIn('PRIVATE ENGINE CONTENT', str(self.notice.call_args))

    async def test_response_validation_and_output_allowlist(self):
        self.anchor()
        for key, value in (('characterId','other'), ('episodeId','other'), ('revision', True),
                           ('state','unknown'), ('memories',[{'gist':'bad'}])):
            old = self.result[key]
            self.result[key] = value
            self.event.reset_mock()
            self.assertEqual(await self.run_action(), '')
            self.result[key] = old
        self.result.update(impressions=['SECRET'], transcript='SECRET', recall='SECRET')
        self.result['memories'][0].update(sourceMessageIds=['SECRET'], cues=['SECRET'])
        self.assertNotIn('SECRET', await self.run_action())

    @property
    def original_register(self):
        # Fresh namespace shares only the database, not patched write traps.
        n, _, _ = load()
        n['DB'] = self.ns['DB']
        return n['register_scene']
