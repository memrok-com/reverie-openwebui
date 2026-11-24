"""Local HTTP authentication regressions; synthetic credentials and job state only."""
import http.client
import os
import threading
import unittest
from unittest.mock import patch

import adapter

TOKEN = 'synthetic-only-adapter-token-000000000000'


class AuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.bridge = adapter.Bridge()
        self.job = adapter.Job('synthetic', 'memorize', {})
        self.job.frame({'callId': 'source', 'method': 'source.read', 'params': {}})
        self.bridge.jobs[self.job.id] = self.job
        with patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN': TOKEN}):
            self.server = adapter.Server(('127.0.0.1', 0), self.bridge)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, token=None, body=b'{invalid'):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            headers = {} if token is None else {'Authorization': 'Bearer ' + token}
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_missing_wrong_auth_precedes_parse_lookup_and_mutation(self):
        before = self.job.snapshot()
        outgoing = list(self.job.outgoing)
        with patch.object(self.bridge, 'start', wraps=self.bridge.start) as start, patch.object(self.bridge, 'get', wraps=self.bridge.get) as get:
            for token in (None, 'wrong-token'):
                for method, path in [('POST', '/jobs'), ('GET', '/jobs/' + self.job.id),
                                     ('POST', '/jobs/' + self.job.id + '/callback'),
                                     ('POST', '/jobs/' + self.job.id + '/cancel')]:
                    self.assertEqual(self.request(method, path, token)[0], 401)
            start.assert_not_called()
            get.assert_not_called()
        self.assertEqual(list(self.bridge.jobs), [self.job.id])
        self.assertEqual(self.job.snapshot(), before)
        self.assertEqual(list(self.job.outgoing), outgoing)
        self.assertFalse(self.job.cancelling)
        self.assertEqual(self.request('GET', '/health')[0], 200)
        self.assertEqual(self.request('GET', '/jobs/' + self.job.id, TOKEN)[0], 200)



class AuthConfigTests(unittest.TestCase):
    def test_server_rejects_invalid_config_without_value(self):
        for token in ('', 'short', 'x' * 31, 'x' * 32 + '\n', 'é' * 32, 'x' * 32 + ' '):
            with self.subTest(token_length=len(token)), patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN': token}):
                with self.assertRaisesRegex(ValueError, '^REVERIE_ADAPTER_TOKEN must be') as error:
                    adapter.Server(('127.0.0.1', 0), adapter.Bridge())
                self.assertNotIn(repr(token), str(error.exception))


class RedirectHTTPTests(unittest.TestCase):
    def test_adapter_token_is_not_forwarded_on_redirect(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from test_functions import load
        received = []

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append((self.path, self.headers.get('Authorization')))
                self.send_response(302)
                self.send_header('Location', '/sink')
                self.send_header('Content-Length', '0')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            ns, _, _ = load()
            ns['BASE'] = 'http://127.0.0.1:' + str(server.server_port)
            with patch.dict(os.environ, {'REVERIE_ADAPTER_TOKEN': TOKEN}, clear=True):
                with self.assertRaises(ns['SceneError']):
                    ns['wire']('/jobs')
            self.assertEqual(received, [('/jobs', 'Bearer ' + TOKEN)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
