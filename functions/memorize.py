_active_memorize = set()
_unverified = 'Memory save not verified. Check Memory for this chat before retrying.'
_save_errors = {
    'INVALID_BRANCH': 'Select a completed character reply on the branch you want to memorize.',
    'UNPLAYED_BRANCH': 'Select a completed character reply on the branch you want to memorize.',
    'PRIVATE_SCOPE_REQUIRED': 'Reverie needs a private chat in your own unshared top-level folder.',
    'ADAPTER_CONFIG': 'Memory save not verified. Ask your administrator to check the Reverie adapter configuration.',
    'REFLECTION_CONFIG': 'Memory save not verified. Ask your administrator to check the reflection model configuration.',
    'REFLECTION_FAILED': 'Memory save not verified: reflection failed. Check Memory for this chat before retrying.',
    'EPISODE_TIME_REQUIRED': 'Memory save not verified: this scene has no story date. Set a story date on a character turn first.',
    'CHARACTER_BUSY': 'Reverie is busy with another operation. Wait for it to finish, then check Memory for this chat.',
}


async def _feedback(emit, text, *, done=None, kind='info'):
    # Socket delivery is best effort. In particular, an anchored save must not
    # be reported as failed just because its display could not be delivered.
    try:
        if done is None:
            await tell(emit, kind, text)
        else:
            await emit({'type':'status', 'data':{
                'action':'reverie_memorize', 'description':text, 'done':done}})
    except Exception:
        pass


class Action:
    async def action(self, body, __user__, __event_call__, __event_emitter__):
        lease = None
        running = False
        result_text = _unverified
        try:
            scoped = await action_scope(__user__, body, __event_call__)
            if not scoped:
                return
            character, chat = scoped
            owner = field(__user__, 'id')
            key = (owner, character)
            # This check and acquisition have no intervening await. The host's
            # function module guards the confirmation and save in this worker;
            # the sidecar still excludes overlapping character jobs across workers.
            if key in _active_memorize:
                await _feedback(__event_emitter__, 'A Memorize request is already open or running in this continuity.')
                return
            _active_memorize.add(key)
            lease = key
            reply = body.get('id')
            prefix = played(chat, reply)
            if not await popup(__event_call__, 'Memorize through this reply?',
                    'Save fallible character memory from the selected branch through this completed reply, including earlier played messages. Later replies and other branches are excluded. The save result appears above this reply. Cancelling here saves nothing.'):
                await _feedback(__event_emitter__, 'Memorizing cancelled. Existing memory is unchanged.')
                return
            running = True
            await _feedback(__event_emitter__, 'Memorizing…', done=False)
            register_scene(owner, character, chat.id)
            token = await guard(owner, character, current=chat.id)
            await verify_published_sources(__user__, character, selecting=chat.id)
            async def callback(frame, path):
                await guard(owner, character, token, chat.id)
                params = frame.get('params', {})
                if frame.get('method') != 'model.reflect':
                    response = await source_callback(__user__, character, chat.id, reply, frame)
                    if params.get('episodeId') == chat.id and 'result' in response and fingerprint(response['result']['messages']) != fingerprint(prefix):
                        return {'error':{'code':'STALE_SELECTION','message':'Selected messages changed during reflection.'}}
                    await guard(owner, character, token, chat.id)
                    return response
                target = params.get('episodeId')
                if (params.get('characterId') != character or params.get('modelKey') != MODEL_KEY
                        or not isinstance(target, str) or not isinstance(params.get('request'), dict)):
                    return {'error':{'code':'INVALID_CALLBACK','message':'Unexpected reflection route.'}}
                # Re-authorize the source immediately before passing it to the configured provider.
                observed = await source(__user__, character, target, chat.id, reply)
                if target == chat.id and fingerprint(observed['messages']) != fingerprint(prefix):
                    return {'error':{'code':'STALE_SELECTION','message':'The selected branch changed.'}}
                request_episode = params['request'].get('input', {}).get('episode', {})
                expected = [{'id':f'm{i}', 'role':m['role'], 'text':m['text'],
                             'speaker':'player' if m['role']=='user' else 'companion'}
                            for i,m in enumerate(observed['messages'],1)]
                # The protocol envelope names the real episode. The reflection payload uses model-local aliases.
                if request_episode.get('messages') != expected:
                    return {'error':{'code':'INVALID_CALLBACK','message':'Reflection source did not match the authorized branch.'}}
                try:
                    await guard(owner, character, token, chat.id)
                    result = await asyncio.to_thread(reflect_request, params['request'])
                    await guard(owner, character, token, chat.id)
                    return {'result':result}
                except SceneError as error:
                    return {'error':{'code':error.code,'message':str(error)}}
            await guard(owner, character, token, chat.id)
            result = await rpc(character, 'memorize', {'episodeId':chat.id,'modelKey':MODEL_KEY}, callback)
            await guard(owner, character, token, chat.id)
            observed = await source(__user__, character, chat.id, chat.id, reply)
            if fingerprint(observed['messages']) != fingerprint(prefix):
                raise SceneError('STALE_SELECTION', 'The selected branch changed while forming memory. Save was not verified; inspect before retrying.')
            # Only a confirmed published engine contribution establishes a source anchor.
            if result.get('status') not in ('published', 'current'):
                raise SceneError('SAVE_UNVERIFIED', 'Memory save was not verified. Inspect before retrying.')
            await guard(owner, character, token, chat.id)
            set_anchor(character, chat.id, reply, prefix, expected=token)
            result_text = 'Already memorized through this reply.' if result['status']=='current' else 'Memory saved through this reply.'
        except Exception as error:
            if isinstance(error, sqlite3.Error):
                result_text = 'Memory save not verified: storage is unavailable. Check Memory for this chat before retrying.'
            elif isinstance(error, SceneError) and isinstance(error.code, str):
                result_text = _save_errors.get(error.code, _unverified)
            if not running:
                await _feedback(__event_emitter__, result_text, kind='error')
        finally:
            try:
                if running:
                    await _feedback(__event_emitter__, result_text, done=True)
            finally:
                if lease is not None:
                    _active_memorize.remove(lease)
