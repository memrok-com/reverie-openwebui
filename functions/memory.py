# This Action deliberately does not use db_open, guard, source or reconciliation:
# opening the view must neither create provenance storage nor repair memory.
from pathlib import Path


def memory_metadata(owner, character, chat_id):
    try:
        Path(DB).stat()
    except FileNotFoundError:
        return None
    # mode=ro refuses creation, including an unlink between stat and connect.
    with closing(sqlite3.connect(Path(DB).absolute().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.execute('BEGIN')
        selected = db.execute('SELECT reply,prefix FROM published WHERE character=? AND chat=?',
                              (character, chat_id)).fetchone()
        scenes = tuple(db.execute('SELECT owner,chat,cleaned FROM scenes WHERE character=? ORDER BY chat',
                                  (character,)).fetchall())
        generation_row = db.execute('SELECT value FROM generations WHERE character=?', (character,)).fetchone()
        own = [row for row in scenes if row[1] == chat_id]
        if (any(row[0] != owner or row[2] not in (0, 1) for row in scenes)
                or (selected and own != [(owner, chat_id, 0)]) or (own and own[0][2])):
            raise SceneError('MEMORY_SCOPE', 'Memory state could not be verified.')
        return selected, scenes, generation_row


async def memory_snapshot(user, chat_id, reply):
    scoped = await scope(user, chat_id)
    if not scoped:
        raise SceneError('MEMORY_SCOPE', 'Memory scope could not be verified.')
    character, chat = scoped
    # Stock branch navigation can be local to the browser. The clicked reply
    # identifies its saved path; the database's currentId may still be another
    # path. Never trust the message texts in the Action request.
    branch = played(chat, reply)
    owner = field(user, 'id')
    metadata = memory_metadata(owner, character, chat_id)
    if not await chat_exists(owner, chat_id):
        raise SceneError('SOURCE_DELETED', 'The scene is unavailable.')
    selected = metadata[0] if metadata else None
    if metadata and selected:
        # Only ID/presence metadata from other scenes, never their transcripts,
        # anchors or memories. Deletion hooks retain responsibility for cleanup.
        for _, scene_id, cleaned in metadata[1]:
            if scene_id != chat_id and not cleaned and not await chat_exists(owner, scene_id):
                raise SceneError('PENDING_DELETION', 'Memory cleanup is pending.')
    prefix, relation = None, 'stale'
    if selected:
        try:
            prefix = played(chat, selected[0])
            if fingerprint(prefix) == selected[1]:
                if branch[:len(prefix)] == prefix:
                    relation = 'matched'
                elif prefix[:len(branch)] == branch:
                    # This reply is above the cutoff. The native Action does
                    # not identify which later branch is visible. Only extend
                    # the match if the path to the cutoff is unambiguous.
                    links = {parent['id']: child['id'] for parent, child
                             in zip(prefix[len(branch)-1:], prefix[len(branch):])}
                    saved = chat.chat['history']['messages']
                    forked = any(isinstance(m, dict) and isinstance(m.get('parentId'), str)
                                 and m['parentId'] in links and links[m['parentId']] != msg_id
                                 for msg_id, m in saved.items())
                    relation = 'ambiguous' if forked else 'matched'
        except SceneError:
            pass
    return character, metadata, fingerprint(branch), prefix, relation


def memory_result(result, character, chat_id):
    if (not isinstance(result, dict) or result.get('characterId') != character
            or result.get('episodeId') != chat_id or type(result.get('revision')) is not int
            or result['revision'] < 0
            or result.get('state') not in ('not-memorized', 'invalidated', 'stale', 'published')
            or not isinstance(result.get('memories'), list)):
        raise SceneError('INVALID_RESULT', 'Memory response could not be verified.')
    memories = []
    for item in result['memories']:
        if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in ('gist', 'detail', 'feeling'))
                or type(item.get('confidence')) not in (int, float) or not 0 <= item['confidence'] <= 1):
            raise SceneError('INVALID_RESULT', 'Memory response could not be verified.')
        memories.append({key:item[key] for key in ('gist', 'detail', 'feeling', 'confidence')})
    if result['state'] != 'published' and memories:
        raise SceneError('INVALID_RESULT', 'Unpublished memory response could not be verified.')
    return result['state'], memories


def memory_text(value):
    # Confirmation surfaces can render Markdown. Treat all saved/provider text
    # as text, including links and HTML, rather than popup instructions.
    special = "\\`*_{}[]()#+.!|<>~-=:'\"&@"
    return ''.join(f'&#{ord(c)};' if c in special else c for c in ' '.join(value.split()))


def readable_chat_memory(snapshot, state, memories):
    _, metadata, _, prefix, relation = snapshot
    selected = metadata[0] if metadata else None
    if relation == 'ambiguous':
        return 'Open Memory for this chat under the latest reply to check which branch was memorized.'
    if state in ('invalidated', 'stale') or (selected and state == 'not-memorized'):
        return 'Needs re-memorizing. Memorize the desired completed reply on the selected saved branch again.'
    if state == 'not-memorized':
        return 'Not memorized. This chat has no saved recollections.'
    if not selected or relation != 'matched':
        return 'Needs re-memorizing. The saved memory boundary could not be verified.'
    number = sum(m['role'] == 'assistant' for m in prefix)
    excerpt = ' '.join(prefix[-1]['text'].split())[:64]
    lines = [f'Memorized through assistant reply {number}: “{memory_text(excerpt)}”',
             'Later messages are not included.']
    if not memories:
        return '\n'.join(lines + ['', 'Memorized. No recollections were formed from this chat.'])
    lines += ['', 'Recollections from this chat']
    for item in memories:
        lines += ['', '- ' + ('Uncertain: ' if item['confidence'] < .5 else '') + memory_text(item['gist'])]
        for key in ('detail', 'feeling'):
            if item[key]:
                lines[-1] += '  \n  ' + key.capitalize() + ': ' + memory_text(item[key])
    return '\n'.join(lines)


class Action:
    async def action(self, body, __user__, __event_call__, __event_emitter__):
        try:
            chat_id, reply = body.get('chat_id'), body.get('id')
            snapshot = await memory_snapshot(__user__, chat_id, reply)
            character, metadata, _, _, relation = snapshot
            selected = metadata[0] if metadata else None
            called, refused = False, False

            async def callback(frame, path):
                nonlocal called, refused
                params = frame.get('params') if isinstance(frame, dict) else None
                if (refused or called or not selected or relation != 'matched' or not isinstance(params, dict)
                        or frame.get('method') != 'source.read'
                        or params != {'characterId':character, 'episodeId':chat_id}):
                    refused = True
                    return {'error':{'code':'INVALID_CALLBACK', 'message':'Unexpected memory source request.'}}
                called = True
                fresh = await memory_snapshot(__user__, chat_id, reply)
                if fresh != snapshot:
                    refused = True
                    return {'error':{'code':'STALE_SOURCE', 'message':'Memory source changed.'}}
                # Engine reflection::observe hashes messages and foundation only.
                # recallHistory is unnecessary for inspectEpisode and omitted.
                return {'result':{'foundation':FOUNDATION, 'messages':fresh[3]}}

            if not selected:
                # Without a verified cutoff no engine result could be shown.
                # In particular, do not create an engine DB by viewing a new chat.
                state, memories = 'not-memorized', []
            elif relation != 'matched':
                state, memories = 'stale', []
            else:
                result = await rpc(character, 'inspectEpisode', {'episodeId':chat_id}, callback)
                state, memories = memory_result(result, character, chat_id)
                if refused or (state == 'published' and not called):
                    raise SceneError('INVALID_RESULT', 'Memory source was not verified.')
            fresh = await memory_snapshot(__user__, chat_id, reply)
            if fresh != snapshot:
                raise SceneError('MEMORY_CHANGED', 'Memory scope or branch changed.')
            await popup(__event_call__, 'Memory for this chat', readable_chat_memory(fresh, state, memories))
        except Exception:
            # Never surface engine/transport messages, identifiers or private text.
            await tell(__event_emitter__, 'error',
                       'Memory for this chat could not be verified. Retry after checking the saved branch and memory cleanup.')
