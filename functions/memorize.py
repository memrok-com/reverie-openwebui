class Action:
    async def action(self, body, __user__, __event_call__, __event_emitter__):
        try:
            scoped = await action_scope(__user__, body, __event_call__)
            if not scoped:
                return
            character, chat = scoped
            reply = body.get('id')
            prefix = played(chat, reply)
            if not await popup(__event_call__, 'Memorize through this reply?',
                    'Save fallible character memory from the selected branch through this completed reply, including earlier played messages. Later replies and other branches are excluded. Wait for the save status. Cancelling here saves nothing.'):
                await tell(__event_emitter__, 'info', 'Memorizing cancelled. Existing memory is unchanged.')
                return
            owner = field(__user__, 'id')
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
            await tell(__event_emitter__, 'success', 'Already memorized through this reply.' if result['status']=='current' else 'Memory saved through this reply.')
        except sqlite3.Error:
            await tell(__event_emitter__, 'error', 'Memory state is unavailable. Retry before using memory.')
        except SceneError as error:
            await tell(__event_emitter__, 'error', str(error))
