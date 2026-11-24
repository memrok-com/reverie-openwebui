class Filter:
    async def inlet(self, body, __user__, __chat_id__, __metadata__, __event_call__, __event_emitter__=None):
        try:
            scoped = await scope(__user__, __chat_id__)
            if not scoped:
                return body
            character, chat = scoped
            metadata = __metadata__ if isinstance(__metadata__, dict) else {}
            prompt_id = metadata.get('user_message_id')
            reply = metadata.get('message_id')
            if (not isinstance(prompt_id, str) or not isinstance(reply, str)
                    or metadata.get('assistant_message_id') or body.get('tools') or body.get('files')):
                raise SceneError('UNSUPPORTED_REQUEST', 'Reverie requires a saved plain-text prompt and a new reply. Continue, tools and attachments are unsupported.')
            prefix = chain(chat, prompt_id)
            if not prefix or prefix[-1]['role'] != 'user':
                raise SceneError('INVALID_BRANCH', 'The saved player prompt could not be identified.')
            messages = body.get('messages')
            if (not isinstance(messages, list) or not messages
                    or any(not isinstance(m, dict) or m.get('role') not in ('system','user','assistant')
                           or not isinstance(m.get('content'), str) or m.get('tool_calls') or m.get('files') for m in messages)
                    or [{'role':m['role'],'text':m['content']} for m in messages if m['role'] != 'system']
                       != [{'role':m['role'],'text':m['text']} for m in prefix]):
                raise SceneError('UNSUPPORTED_REQUEST', 'Reverie could not verify the plain-text saved branch. No reply was sent.')
            await verify_published_sources(__user__, character)
            try:
                episode = await rpc(character, 'when', {'episodeId':chat.id})
            except SceneError as error:
                if error.code != 'EPISODE_TIME_REQUIRED':
                    raise
                position = await ask_date(__event_call__)
                episode = await rpc(character, 'when', {'episodeId':chat.id,'position':position})
            prior = records(character, chat.id, prefix)
            recall = await rpc(character, 'retrieve', {'episodeId':chat.id,'at':episode['now'],
                'cue':prefix[-1]['text'],'recallHistory':activations(prefix,prior)})
            recall['at'] = episode['now']
            recall['sources'] = {}
            source_ids = [m.get('episodeId') for key in ('memories','historicalContext') for m in recall.get(key, [])]
            source_ids += recall.get('historySources', [])
            if recall.get('impressionEpisodeId'):
                source_ids.append(recall['impressionEpisodeId'])
            for source_id in source_ids:
                if not isinstance(source_id, str):
                    raise SceneError('INVALID_RECALL', 'The recalled source could not be verified.')
                if source_id in recall['sources']:
                    continue
                origin = await scope(__user__, source_id)
                if not origin or origin[0] != character or not anchor(character, source_id):
                    raise SceneError('INVALID_RECALL', 'The recalled source is no longer available in this private continuity.')
                dated = await rpc(character, 'when', {'episodeId':source_id})
                if dated['now'] >= episode['now']:
                    raise SceneError('INVALID_RECALL', 'A recalled source is not before this scene’s date.')
                title = field(origin[1], 'title') or field(origin[1], 'chat', {}).get('title') or 'Untitled scene'
                recall['sources'][source_id] = {'title':str(title),'at':dated['now']}
            stage(character, chat.id, reply, fingerprint(prefix), recall)
            text = '\n'.join(t for t in (recall['text'], recall['historicalText']) if t)
            if text:
                # Copy before modifying so a rejected request cannot mutate shared input.
                body['messages'] = [dict(m) for m in messages]
                first = body['messages'][0]
                if first['role'] == 'system':
                    first['content'] += '\n\n' + text
                else:
                    body['messages'].insert(0, {'role':'system','content':text})
            return body
        except SceneError as error:
            raise ValueError(str(error)) from None
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError('Reverie could not verify this request. No reply was sent; use a saved plain-text prompt.') from None

    async def outlet(self, body, __user__, __event_emitter__=None):
        try:
            scoped = await scope(__user__, body.get('chat_id'))
            if not scoped or not body.get('messages') or body['messages'][-1].get('finish_reason') != 'stop':
                return body
            character, chat = scoped
            prefix = played(chat, body['id'])
            if publish(character, chat.id, body['id'], prefix) and __event_emitter__:
                record = records(character, chat.id, prefix).get(body['id'])
                await __event_emitter__({'type':'status','data':{'description':'Story date: '+scene_day(record['at']),'done':True}})
        except SceneError:
            pass  # Never publish provenance for an unverifiable or incomplete reply.
        return body
