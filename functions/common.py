# Shared stdlib Function source, inlined by install.py.
import asyncio
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from http.client import HTTPException
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.models.access_grants import AccessGrants

PRESET_ID = 'reverie-companion'
DB = os.environ.get('REVERIE_RECORDS', '/app/backend/data/reverie.sqlite')
BASE = os.environ.get('REVERIE_ADAPTER_URL', 'http://reverie:8766').rstrip('/')
MODEL_KEY = 'reverie-reflection'
FOUNDATION = 'Play the fictional companion in this private continuity.'

class SceneError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)

async def scope(user, chat_id):
    uid = field(user, 'id')
    if not isinstance(uid, str) or not isinstance(chat_id, str) or not chat_id:
        return None
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, uid)
    if not chat or not field(chat, 'folder_id'):
        return None
    folder = await Folders.get_folder_by_id_and_user_id(field(chat, 'folder_id'), uid)
    data = field(folder, 'data')
    models = data.get('model_ids') if isinstance(data, dict) else None
    if not isinstance(models, list) or not models or models[0] != PRESET_ID:
        return None
    if not all(isinstance(x, str) for x in models):
        raise SceneError('INVALID_SCOPE', 'The folder default model settings could not be verified.')
    # Only this folder's own default opts in; never consult ancestors or chat models.
    if (field(chat, 'user_id') != uid or field(folder, 'user_id') != uid
            or field(chat, 'id') != chat_id or field(folder, 'id') != field(chat, 'folder_id')
            or field(chat, 'share_id') or field(folder, 'parent_id') or data.get('share_mode')
            or await AccessGrants.get_grants_by_resource('folder', folder.id)):
        raise SceneError('PRIVATE_SCOPE_REQUIRED', 'Reverie needs a private chat in your own unshared top-level folder.')
    if not isinstance(field(chat, 'chat'), dict):
        raise SceneError('INVALID_CHAT', 'The saved chat could not be verified.')
    character = 'r' + hashlib.sha256(f'{uid}:{chat.folder_id}'.encode()).hexdigest()[:63]
    return character, chat

def chain(chat, tail):
    saved = field(chat, 'chat')
    history = saved.get('history') if isinstance(saved, dict) else None
    history = history.get('messages') if isinstance(history, dict) else None
    if not isinstance(history, dict):
        raise SceneError('INVALID_BRANCH', 'The saved branch could not be verified.')
    result, seen = [], set()
    while tail:
        if not isinstance(tail, str) or tail in seen or tail not in history or len(seen) >= 200:
            raise SceneError('INVALID_BRANCH', 'The selected saved branch could not be resolved.')
        seen.add(tail)
        m = history[tail]
        if not isinstance(m, dict) or m.get('id') != tail or m.get('role') not in ('user', 'assistant'):
            raise SceneError('INVALID_BRANCH', 'The selected saved branch contains an unsupported message.')
        if m.get('role') == 'assistant' and not (m.get('done') is True and m.get('content') and not m.get('error')):
            raise SceneError('UNPLAYED_BRANCH', 'The selected saved reply did not complete.')
        if not isinstance(m.get('content'), str) or m.get('files') or m.get('tool_calls'):
            raise SceneError('INVALID_BRANCH', 'Reverie supports saved plain text only.')
        result.append({'id':tail, 'role':m['role'], 'text':m['content']})
        tail = m.get('parentId')
    return list(reversed(result))

def fingerprint(messages):
    return hashlib.sha256(json.dumps([FOUNDATION, messages], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def db_open():
    db = sqlite3.connect(DB, timeout=3)
    db.execute('CREATE TABLE IF NOT EXISTS records (character TEXT, chat TEXT, reply TEXT, prefix TEXT, live INTEGER, value TEXT, PRIMARY KEY(character,chat,reply))')
    db.execute('CREATE TABLE IF NOT EXISTS pending (character TEXT, chat TEXT, reply TEXT, prefix TEXT, value TEXT, PRIMARY KEY(character,chat,reply))')
    db.execute('CREATE TABLE IF NOT EXISTS published (character TEXT, chat TEXT, reply TEXT, prefix TEXT, PRIMARY KEY(character,chat))')
    db.execute('CREATE TABLE IF NOT EXISTS scenes (owner TEXT, character TEXT, chat TEXT PRIMARY KEY, cleaned INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS generations (character TEXT PRIMARY KEY, value INTEGER NOT NULL)')
    return db

async def chat_exists(owner, chat_id):
    # Native getters swallow database errors. Only a successful minimal SELECT
    # can establish absence; never read chat bodies or enumerate an owner's chats.
    try:
        from sqlalchemy import select
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.chats import Chat
        async with get_async_db_context() as db:
            result = await db.execute(select(Chat.id).where(Chat.id == chat_id, Chat.user_id == owner))
            return result.scalar_one_or_none() is not None
    except Exception:
        raise SceneError('SOURCE_STATE', 'Scene presence could not be verified. Retry when storage is available.') from None

def register_scene(owner, character, chat_id):
    with closing(db_open()) as db, db:
        row = db.execute('SELECT owner,character,cleaned FROM scenes WHERE chat=?', (chat_id,)).fetchone()
        if not row and db.execute('SELECT 1 FROM published WHERE chat=?', (chat_id,)).fetchone():
            raise SceneError('LEGACY_SOURCE', 'Untracked source anchors require a clean installation; preview migration is unsupported.')
        db.execute('INSERT OR IGNORE INTO scenes(owner,character,chat) VALUES (?,?,?)', (owner,character,chat_id))
        row = db.execute('SELECT owner,character,cleaned FROM scenes WHERE chat=?', (chat_id,)).fetchone()
        if row != (owner, character, 0):
            raise SceneError('SCENE_OWNERSHIP', 'This scene cannot be registered in this continuity.')

def generation(character):
    with closing(db_open()) as db:
        row = db.execute('SELECT value FROM generations WHERE character=?', (character,)).fetchone()
        return row[0] if row else 0

async def reconcile(owner, character):
    with closing(db_open()) as db:
        scenes = db.execute('SELECT chat,cleaned FROM scenes WHERE owner=? AND character=?', (owner,character)).fetchall()
        unknown = db.execute('SELECT 1 FROM published p WHERE character=? AND NOT EXISTS (SELECT 1 FROM scenes s WHERE s.chat=p.chat AND s.owner=? AND s.character=p.character AND s.cleaned=0)', (character,owner)).fetchone()
    if unknown:
        raise SceneError('LEGACY_SOURCE', 'Untracked memory sources cannot be verified. This clean-install release does not migrate earlier preview data.')
    for chat_id, cleaned in scenes:
        if cleaned or await chat_exists(owner, chat_id):
            continue
        # Durable invalidation precedes transport. A crash or busy engine leaves
        # this scene retryable; forget is idempotent, including after lost replies.
        with closing(db_open()) as db, db:
            db.execute('INSERT INTO generations VALUES (?,1) ON CONFLICT(character) DO UPDATE SET value=value+1', (character,))
        result = await rpc(character, 'forget', {'episodeId':chat_id})
        count = field(result, 'invalidatedEpisodes')
        if not isinstance(result, dict) or result.get('episodeId') != chat_id or type(count) is not int or count < 0:
            raise SceneError('FORGET_UNVERIFIED', 'Memory cleanup was not verified. Retry before using memory.')
        with closing(db_open()) as db, db:
            if not db.execute('UPDATE scenes SET cleaned=1 WHERE owner=? AND character=? AND chat=? AND cleaned=0',
                              (owner,character,chat_id)).rowcount:
                continue  # A concurrent retry already completed the local cleanup.
            # Context can include broader impressions without per-item lineage.
            # Conservatively discard all cached provenance in this character.
            db.execute('DELETE FROM records WHERE character=?', (character,))
            db.execute('DELETE FROM pending WHERE character=?', (character,))
            db.execute('DELETE FROM published WHERE character=? AND chat=?', (character,chat_id))

            # Also fence readers that began after invalidation but before cleanup.
            db.execute('UPDATE generations SET value=value+1 WHERE character=?', (character,))

async def guard(owner, character, expected=None, current=None):
    missing = current is not None and not await chat_exists(owner, current)
    if missing:
        # An operation may have started just after an earlier forget completed.
        # Rearm cleanup so its late publication cannot escape the journal.
        with closing(db_open()) as db, db:
            db.execute('UPDATE scenes SET cleaned=0 WHERE owner=? AND character=? AND chat=?', (owner,character,current))
    await reconcile(owner, character)
    if missing:
        raise SceneError('SOURCE_DELETED', 'The scene was deleted. Retry in a surviving scene.')
    now = generation(character)
    if expected is not None and now != expected:
        raise SceneError('MEMORY_CHANGED', 'Memory changed during this operation. Retry in a surviving scene.')
    return now


def anchor(character, chat):
    with closing(db_open()) as db:
        return db.execute('SELECT reply,prefix FROM published WHERE character=? AND chat=?',(character,chat)).fetchone()

def bind_generation(db, character, expected):
    if expected is None:
        return
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT value FROM generations WHERE character=?', (character,)).fetchone()
    if (row[0] if row else 0) != expected:
        raise SceneError('MEMORY_CHANGED', 'Memory changed during this operation. Retry in a surviving scene.')

def set_anchor(character, chat, reply, prefix, expected=None):
    with closing(db_open()) as db, db:
        bind_generation(db, character, expected)
        db.execute('INSERT OR REPLACE INTO published VALUES (?,?,?,?)',(character,chat,reply,fingerprint(prefix)))

async def source(user, character, episode_id, current_chat, current_reply):
    scoped = await scope(user, episode_id)
    if not scoped or scoped[0] != character:
        raise SceneError('SOURCE_UNAVAILABLE', 'The episode no longer belongs to this continuity.')
    _, chat = scoped
    selected = anchor(character, episode_id)
    tail = current_reply if episode_id == current_chat else (selected[0] if selected else chat.chat.get('history', {}).get('currentId'))
    prefix = played(chat, tail)
    if episode_id != current_chat and (not selected or fingerprint(prefix) != selected[1]):
        raise SceneError('STALE_SOURCE', 'The memorized source branch changed or is unavailable.')
    return {'foundation':FOUNDATION, 'messages':prefix,'recallHistory':activations(prefix, records(character, chat.id, prefix))}

async def source_callback(user, character, chat_id, reply, frame):
    params = frame.get('params', {})
    if params.get('characterId') != character or frame['method'] != 'source.read':
        return {'error': {'code':'INVALID_CALLBACK', 'message':'Unexpected source callback.'}}
    try:
        return {'result':await source(user, character, params.get('episodeId'), chat_id, reply)}
    except SceneError as error:
        return {'error':{'code':error.code,'message':str(error)}}

async def verify_published_sources(user, character, selecting=None):
    await reconcile(field(user, 'id'), character)
    # Alpha fail-closed scan of published sources; bounded datasets only.
    with closing(db_open()) as db:
        anchors = db.execute('SELECT chat,reply,prefix FROM published WHERE character=?',(character,)).fetchall()
    for chat_id, reply, expected in anchors:
        if chat_id == selecting:
            continue
        try:
            scoped = await scope(user, chat_id)
        except SceneError:
            scoped = None
        if not scoped or scoped[0] != character:
            raise SceneError('STALE_PUBLISHED_SOURCE',
                'A memorized chat is no longer available in this private folder. If it still exists, unarchive it if needed, '
                'return it to the original folder and turn off sharing. Retry if storage is unavailable.')
        try:
            if fingerprint(played(scoped[1], reply)) == expected:
                continue
        except SceneError:
            pass
        raise SceneError('STALE_PUBLISHED_SOURCE', 'A memorized scene changed. Memorize the selected branch again before playing further.')

def stage(character, chat, reply, prefix, recall, expected=None):
    # Persist structured provenance only, never the assembled model context.
    recall = {key:recall[key] for key in ('at','memories','sources','historicalContext','impressions','sharedHistory') if key in recall}
    with closing(db_open()) as db, db:
        bind_generation(db, character, expected)
        db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?)',
                   (character, chat, reply, prefix, json.dumps(recall)))

def publish(character, chat, reply, prefix, expected=None):
    with closing(db_open()) as db, db:
        bind_generation(db, character, expected)
        found = db.execute('SELECT value FROM pending WHERE character=? AND chat=? AND reply=? AND prefix=?',
                           (character, chat, reply, fingerprint(prefix[:-1]))).fetchone()
        if found:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,1,?)',
                       (character, chat, reply, fingerprint(prefix), found[0]))
            db.execute('DELETE FROM pending WHERE character=? AND chat=? AND reply=?', (character, chat, reply))
        return bool(found)

def records(character, chat, prefix):
    with closing(db_open()) as db:
        found = {rid:(stamp,json.loads(data)) for rid,stamp,data in db.execute('SELECT reply,prefix,value FROM records WHERE character=? AND chat=? AND live=1', (character,chat))}
    return {m['id']:found[m['id']][1] for i,m in enumerate(prefix) if m['role']=='assistant' and m['id'] in found and found[m['id']][0]==fingerprint(prefix[:i+1])}

def activations(prefix, by_reply):
    result = []
    for i, m in enumerate(prefix):
        recall = by_reply.get(m['id'])
        if recall and recall.get('memories') and i and prefix[i-1]['role']=='user':
            result.append({'replyId':m['id'],'promptId':prefix[i-1]['id'],'at':recall['at'], 'memories':[
                {'episodeId':x['episodeId'],'contributionHash':x['contributionHash'], 'memoryIndex':x['memoryIndex'], 'detailSupplied':bool(x['detail'])}
                for x in recall['memories']]})
    return result

def wire(path, data=None):
    token = os.environ.get('REVERIE_ADAPTER_TOKEN', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,}', token, re.ASCII):
        raise SceneError('ADAPTER_CONFIG', 'The administrator must configure a strong REVERIE_ADAPTER_TOKEN in both services.')
    parsed = urlsplit(BASE)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise SceneError('ADAPTER_CONFIG', 'The administrator must configure an adapter origin URL.')
    request = Request(BASE + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type':'application/json', 'Authorization':'Bearer ' + token}, method='POST' if data is not None else 'GET')
    try:
        with build_opener(NoRedirect).open(request, timeout=2) as r:
            return json.load(r)
    except (URLError, TimeoutError, ValueError) as e:
        if isinstance(e, HTTPError):
            with closing(e):
                if e.code == 409 and path == '/jobs' and data is not None:
                    try:
                        raw = e.read(1025)
                        problem = field(json.loads(raw), 'error') if len(raw) <= 1024 else None
                    except (HTTPException, OSError, ValueError):
                        problem = None
                    if field(problem, 'code') == 'CHARACTER_BUSY':
                        raise SceneError('CHARACTER_BUSY', 'Reverie is busy with another operation. Wait for it to finish and retry.') from None
        raise SceneError('REVERIE_UNAVAILABLE', 'Reverie is not available; no memory-free reply was sent. Retry when it is back.') from None

async def rpc(character, method, params=None, callback=None):
    start = await asyncio.to_thread(wire, '/jobs', {'character':character,'method':method,'params':params or {}})
    path = '/jobs/' + start['job']
    finished = False
    try:
        for _ in range(250):
            result = await asyncio.to_thread(wire, path)
            if result['state'] == 'done':
                finished = True
                if error := result.get('error'):
                    raise SceneError(error['code'], error['message'])
                return result['result']
            if result['state'] == 'callback':
                frame = result['callback']
                if not callback:
                    raise SceneError('UNEXPECTED_CALLBACK', 'Reverie could not verify this operation.')
                reply = await callback(frame, path)
                if reply is not None:
                    await asyncio.to_thread(wire, path+'/callback', {'callId':frame['callId'], **reply})
            await asyncio.sleep(.04)
        raise SceneError('REVERIE_TIMEOUT', 'Reverie did not complete within the wait limit. Save was not verified.')
    finally:
        if not finished:
            try:
                await asyncio.to_thread(wire, path+'/cancel', {})
            except SceneError:
                pass

def played(chat, reply_id):
    prefix = chain(chat, reply_id)
    if not prefix or prefix[-1]['role']!='assistant':
        raise SceneError('INVALID_BRANCH','Select a completed character reply.')
    return prefix

async def tell(event, kind, content):
    await event({'type':'notification','data':{'type':kind,'content':content}})

async def popup(event, title, text):
    return await event({'type':'confirmation','data':{'title':title,'message':text}})


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

async def ask_date(event):
    for attempt in range(3):
        answer = await event({'type':'input', 'data':{'title':'Reverie story date',
            'message':('Enter the scene’s story date as YYYY-MM-DD. Blank or Cancel stops this reply; your saved prompt remains for Try Again.'
                       if not attempt else 'That date was invalid. Use a real date with exactly YYYY-MM-DD, or Cancel.'),
            'placeholder':'YYYY-MM-DD'}})
        if not isinstance(answer, str) or not answer.strip():
            raise SceneError('DATE_REQUIRED', 'Story date not set. Use Try Again on the saved prompt to continue.')
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', answer):
            try:
                return date.fromisoformat(answer).isoformat()
            except ValueError:
                pass
    raise SceneError('INVALID_DATE', 'Story date not set after three attempts. Use Try Again and enter a real YYYY-MM-DD date.')

def scene_day(at):
    return datetime.fromtimestamp(at / 1000, timezone.utc).date().isoformat()

def readable_record(record):
    if record is None:
        return 'No verified record for this reply. It may not have completed, or its branch may have changed.'
    heading = 'Reverie was active. Story date: ' + scene_day(record['at'])
    memories = record.get('memories', [])
    earlier = record.get('historicalContext', [])
    impressions = record.get('impressions', [])
    connection = record.get('sharedHistory', '')
    if not (memories or earlier or impressions or connection):
        return heading + '\nNo memories reached this completed reply.'
    lines = [heading]
    for label, group in [('Recollections supplied to this reply', memories), ('Recollections carried over from earlier replies', earlier)]:
        if not group:
            continue
        lines += ['', label]
        for memory in group:
            prefix = 'Uncertain: ' if memory.get('confidence', 1) < .5 else ''
            lines.append('• ' + prefix + str(memory.get('gist') or 'A remembered moment'))
            for key, name in (('detail','Detail'), ('feeling','Feeling')):
                if isinstance(memory.get(key), str) and memory[key]:
                    lines.append(name + ': ' + memory[key])
            origin = record.get('sources', {}).get(memory.get('episodeId'))
            if origin:
                lines.append('From '+origin['title']+' · '+scene_day(origin['at']))
    feelings = [item['text'] for item in impressions if item.get('text')]
    if feelings:
        lines += ['', 'Broader impressions', *feelings]
    if connection:
        lines += ['', connection]
    return '\n'.join(lines)

async def action_scope(user, body, event):
    scoped = await scope(user, body.get('chat_id'))
    if not scoped:
        await popup(event, 'Enable Reverie in this folder',
            'Open your private folder’s settings and choose Reverie Companion in its default model selector. Choosing it only for a chat does not enable memory. No memory was read or saved.')
    return scoped

def reflect_request(request):
    # Configuration comes only from the trusted host environment, never from a body or valve.
    endpoint = os.environ.get('REVERIE_REFLECTION_URL', '')
    model = os.environ.get('REVERIE_REFLECTION_MODEL', '')
    reasoning = os.environ.get('REVERIE_REFLECTION_REASONING_EFFORT', '')
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.query or not model):
        raise SceneError('REFLECTION_CONFIG', 'The administrator must configure the reflection endpoint and model before memorizing.')
    if reasoning not in ('', 'none'):
        raise SceneError('REFLECTION_CONFIG', 'Set reflection reasoning effort to none for a compatible endpoint, or leave it unset.')
    headers = {'Content-Type':'application/json'}
    token = os.environ.get('REVERIE_REFLECTION_API_KEY')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        payload = {'model':model, 'stream':False, 'max_tokens':request['maxTokens'],
            'messages':[{'role':'system','content':request['system']},
                        {'role':'user','content':json.dumps(request['input'], ensure_ascii=False)}]}
        if reasoning:
            payload['reasoning_effort'] = reasoning
        req = Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with build_opener(NoRedirect).open(req, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('size')
        content = json.loads(raw)['choices'][0]['message']['content']
        if not isinstance(content, str) or not isinstance(json.loads(content), dict):
            raise ValueError('shape')
        return content  # Engine model.reflect expects a JSON string, not a decoded object.
    except (URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, OSError) as error:
        raise SceneError('REFLECTION_FAILED', 'Memory was not confirmed saved. Reflection failed; inspect before retrying.') from error
