class Event:
    async def event(self, event):
        # Dispatch is best effort in the host. Reconciliation also runs at use.
        if not isinstance(event, dict):
            return
        kind = event.get('event')
        subject, data = event.get('subject'), event.get('data')
        if not isinstance(subject, dict) or not isinstance(data, dict):
            return
        owner = None
        chat_id = None
        if kind == 'chat.deleted' and subject.get('type') == 'chat':
            owner, chat_id = data.get('owner_id'), subject.get('id')
            if not isinstance(owner, str) or not owner or not isinstance(chat_id, str) or not chat_id:
                return
        elif kind in ('chat.deleted_all', 'user.deleted') and subject.get('type') == 'user':
            owner = subject.get('id')
            if not isinstance(owner, str) or not owner:
                return
        elif kind == 'folder.deleted' and data.get('delete_contents') is True:
            # Cascades may span folders. Check only registered IDs, each bound
            # to its recorded owner; the actor need not own any of these chats.
            pass
        else:
            return
        try:
            with closing(db_open()) as db:
                rows = db.execute('SELECT DISTINCT owner,character FROM scenes WHERE cleaned=0'
                    + (' AND owner=? AND chat=?' if chat_id else ' AND owner=?' if owner else ''),
                    (owner,chat_id) if chat_id else (owner,) if owner else ()).fetchall()
            for registered_owner, character in rows:
                try:
                    await reconcile(registered_owner, character)
                except (SceneError, sqlite3.Error):
                    pass  # Durable registry retries at use; no private logging.
        except sqlite3.Error:
            pass
