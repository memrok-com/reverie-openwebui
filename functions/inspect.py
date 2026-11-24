class Action:
    async def action(self, body, __user__, __event_call__, __event_emitter__):
        try:
            scoped = await action_scope(__user__, body, __event_call__)
            if not scoped:
                return
            character, chat = scoped
            prefix = played(chat, body.get('id'))
            await verify_published_sources(__user__, character)
            token = generation(character)
            record = records(character, chat.id, prefix).get(body.get('id'))
            await guard(field(__user__, 'id'), character, token, chat.id)
            await popup(__event_call__, 'What reached this reply?', readable_record(record))
        except sqlite3.Error:
            await tell(__event_emitter__, 'error', 'Memory state is unavailable. Retry before using memory.')
        except SceneError as error:
            await tell(__event_emitter__, 'error', str(error))
