"""Demo-only, model-less OpenAI server. Synthetic data only; no content logging."""
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ('synthetic-companion', 'synthetic-other')
COMPASS = 'SYNTH_RECALLED_COMPASS'
EVIDENCE = []
LOCK = threading.Lock()

def reflection(request):
    episode = request['input']['episode']
    messages = episode['messages']
    if any('[fail-reflection]' in m['text'] for m in messages):
        raise ValueError('synthetic failure')
    marker = 'ALT_BRANCH' if any('ALT_BRANCH' in m['text'] for m in messages) else 'MAIN_BRANCH'
    return json.dumps({'memories':[{'gist':f'I think I received a compass. {COMPASS} {marker}',
        'detail':'The brass felt warm.', 'feeling':'I felt sheltered.', 'cues':['compass','harbor'],
        'salience':0.85, 'confidence':0.46,
        'sourceMessageIds':[messages[0]['id'],messages[-1]['id']]}],
        'impressions':[{'text':'I felt I could ask for help.', 'sourceEpisodeIds':[episode['id']]}]})

def completion(body):
    if body.get('model') not in MODELS:
        raise ValueError('unknown model')
    messages = body['messages']
    try:
        request = json.loads(messages[-1]['content'])
    except (ValueError, TypeError):
        request = None
    if isinstance(request,dict) and isinstance(request.get('episode'),dict):
        content = reflection({'input':request})
        episode = request['episode']
        evidence = {'kind':'reflection','episode':episode['id'],
                    'messageIds':[m['id'] for m in episode['messages']],
                    'sourceHash':hashlib.sha256(json.dumps(episode['messages'],sort_keys=True).encode()).hexdigest(),
                    'reasoningOffRequested':body.get('reasoning_effort') == 'none'}
    else:
        text = messages[-1]['content']
        marker = 'ALT_BRANCH' if 'ALT_BRANCH' in text else 'MAIN_BRANCH'
        content = 'Synthetic completed reply: '+marker+'. The compass rests beside the harbor map.'
        system = '\n'.join(x['content'] for x in messages if x['role'] == 'system')
        evidence = {'kind':'reply','model':body['model'],'branch':marker,
                    'promptHash':hashlib.sha256(text.encode()).hexdigest(),
                    'hasCompassRecall':COMPASS in system,
                    'hasUncertainRecall':'Uncertain recollection' in system,
                    'hasInspectorCopy':'What reached this reply?' in system}
    with LOCK:
        EVIDENCE.append(evidence)
        del EVIDENCE[:-200]
    return content

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/v1/models':
            self.respond(200, {'object':'list','data':[{'id':x,'object':'model','owned_by':'synthetic'} for x in MODELS]})
        elif self.path == '/evidence':
            with LOCK:
                snapshot = list(EVIDENCE)
            self.respond(200, snapshot)
        else:
            self.respond(404, {'error':{'message':'Unknown demo route'}})

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length','0'))
            if self.path != '/v1/chat/completions' or not 0 < size <= 1024*1024:
                raise ValueError('request')
            body = json.loads(self.rfile.read(size))
            content = completion(body)
        except (ValueError, KeyError, TypeError, IndexError):
            self.respond(400, {'error':{'message':'Synthetic fixture refused the request'}})
            return
        if body.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Connection','close')
            self.end_headers()
            for delta, finish in (({'role':'assistant','content':content},None), ({},'stop')):
                frame = {'id':'synthetic-completion','object':'chat.completion.chunk','created':int(time.time()),
                         'model':body['model'],'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                self.wfile.write(('data: '+json.dumps(frame)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
        else:
            self.respond(200, {'id':'synthetic-completion','object':'chat.completion','created':int(time.time()),
                'model':body['model'],'choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
