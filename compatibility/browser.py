"""Headless stock Open WebUI journey for the isolated compatibility fixture."""
import json
import re
import time
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


def check(base, prompts, password, collect, checkpoint):
    if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', base):
        raise ValueError('expected a disposable loopback UI')

    def api(path, data, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        with urlopen(Request(base + path, json.dumps(data).encode(), headers), timeout=10) as response:
            return json.load(response)

    checkpoint('browser-player-signin')
    player = api('/api/v1/auths/signin', {'email': 'player@pilot.invalid', 'password': password})
    token = player['token']
    checkpoint('browser-folder-default')
    folder = api('/api/v1/folders/', {'name': 'Synthetic continuity',
                 'data': {'model_ids': ['reverie-companion']}}, token)
    if folder['data']['model_ids'] != ['reverie-companion']:
        raise RuntimeError('folder-model-not-saved')

    def turn(page, prompt, date=None):
        editor = page.locator('#chat-input')
        expect(editor).to_be_visible(timeout=18000)
        editor.fill(prompt)
        editor.press('Enter')
        if date:
            dialog = page.get_by_role('dialog', name=re.compile('Story date', re.I))
            expect(dialog).to_be_visible(timeout=15000)
            dialog.get_by_role('textbox').fill(date)
            dialog.get_by_role('button', name='Confirm', exact=True).click()
        expect(page.get_by_text(re.compile(r'^Synthetic completed reply:')).filter(visible=True)).to_have_count(1, timeout=16000)
        page.wait_for_url(re.compile(r'/c/[^/]+$'), timeout=15000)

    checkpoint('browser-launch')
    with sync_playwright() as automation:
        browser = automation.chromium.launch()
        try:
            context = browser.new_context(viewport={'width': 1200, 'height': 900}, service_workers='block')
            context.set_default_timeout(8000)
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
            context.add_init_script(f'if (location.origin === {json.dumps(base)}) localStorage.setItem("token", {json.dumps(token)});')
            path = base + '/folders/' + folder['id']
            checkpoint('browser-ordinary-model')
            ordinary = context.new_page()
            ordinary.goto(base + '/?models=synthetic-companion', wait_until='domcontentloaded')
            expect(ordinary.get_by_role('button', name='Selected model: synthetic-companion')).to_be_visible(timeout=18000)
            checkpoint('browser-ordinary-turn')
            turn(ordinary, prompts['ordinary'])
            checkpoint('browser-folder-model')
            source = context.new_page()
            source.goto(path, wait_until='domcontentloaded')
            expect(source.get_by_role('button', name='Selected model: Reverie Companion')).to_be_visible(timeout=18000)
            checkpoint('browser-dated-source-turn')
            turn(source, prompts['source'], '2024-06-01')
            checkpoint('browser-memorize-dialog')
            source.get_by_role('button', name='Memorize through this reply', exact=True).last.press('Enter')
            dialog = source.get_by_role('dialog', name=re.compile('Memorize through this reply', re.I))
            expect(dialog).to_be_visible(timeout=12000)
            dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(dialog).not_to_be_visible()
            checkpoint('browser-published-source')
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if collect().get('chats', {}).get('source', {}).get('published'):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('synthetic-source-not-published')
            checkpoint('browser-recall-turn')
            later = context.new_page()
            later.goto(path, wait_until='domcontentloaded')
            turn(later, prompts['later'], '2024-07-01')
            checkpoint('browser-recall-inspector')
            later.get_by_role('button', name='What reached this reply?', exact=True).last.press('Enter')
            expect(later.get_by_role('dialog', name=re.compile('What reached this reply', re.I))).to_be_visible(timeout=12000)
            checkpoint('saved-reply-evidence')
            evidence = collect()
            return evidence
        finally:
            browser.close()
