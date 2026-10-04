"""Exercise the private entry using real HTTP sockets and an isolated upstream."""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.serve_remote_access import make_handler, validate_config


class RemoteAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                payload = {'method': self.command, 'path': self.path, 'body': body.decode(),
                           'cookie': self.headers.get('Cookie'), 'csrf': self.headers.get('X-Qingcai-CSRF'),
                           'origin': self.headers.get('Origin')}
                cls.calls.append(payload)
                content = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                if self.path == '/api/book/finance/session':
                    self.send_header('Set-Cookie', 'qingcai_finance=test-finance; Path=/; HttpOnly; SameSite=Strict')
                    self.send_header('Set-Cookie', 'unrelated_cookie=not-forwarded')
                self.end_headers()
                self.wfile.write(content)

            do_POST = do_GET
            do_DELETE = do_GET
            do_PUT = do_GET

        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.config = {'host': '10.20.30.40', 'port': 25502, 'clients': ['127.0.0.1'],
                      'upstream_port': cls.upstream.server_port, 'token': 'A' * 43}
        cls.entry = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cls.config))
        cls.threads = []
        for server in (cls.upstream, cls.entry):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.entry, cls.upstream):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()

    def request(self, method='GET', path='/', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.entry.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers={'Host': '10.20.30.40:25502', **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def cookie(self):
        status, headers, _ = self.request(path='/__open/' + self.config['token'])
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';', 1)[0]

    def test_all_pages_and_apis_require_access(self):
        before = len(self.calls)
        for path in ('/', '/app.js', '/api/book/health', '/api/trader/health'):
            self.assertEqual(self.request(path=path)[0], 401)
        self.assertEqual(self.request(path='/__open/incorrect')[0], 403)
        self.assertEqual(len(self.calls), before)

    def test_opening_link_and_proxy_preserve_requests_without_leaking_cookie(self):
        cookie = self.cookie()
        for method, path, body in [('GET', '/api/book/bills?limit=10', None),
                                   ('POST', '/api/book/parse', '{"text":"午饭25元"}'),
                                   ('DELETE', '/api/book/bills/123', None),
                                   ('PUT', '/api/book/bills/123', '{"amount":38.6}')]:
            status, headers, content = self.request(method, path, body.encode() if body else None,
                {'Cookie': cookie, 'Origin': 'http://10.20.30.40:25502', 'Content-Type': 'application/json'})
            self.assertEqual(status, 200)
            payload = json.loads(content)
            self.assertEqual(payload['method'], method)
            self.assertEqual(payload['path'], path)
            self.assertEqual(payload['body'], body or '')
            self.assertIsNone(payload['cookie'])
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')

    def test_untrusted_origin_cannot_change_ledger(self):
        cookie = self.cookie()
        before = len(self.calls)
        for origin in ('https://example.com', 'http://10.20.30.40:25500', ''):
            self.assertEqual(self.request('DELETE', '/api/book/bills/123', headers={'Cookie': cookie, 'Origin': origin})[0], 403)
        self.assertEqual(len(self.calls), before)

    def test_finance_session_and_csrf_cross_gateway_without_entry_credentials(self):
        entry_cookie = self.cookie()
        status, headers, content = self.request(path='/api/book/finance/session', headers={'Cookie': entry_cookie})
        self.assertEqual(status, 200)
        self.assertTrue(headers['Set-Cookie'].startswith('qingcai_finance='))
        self.assertNotIn('unrelated_cookie', headers['Set-Cookie'])
        status, _, content = self.request('POST', '/api/book/finance/operations/review', '{}',
            {'Cookie': entry_cookie + '; qingcai_finance=test-finance; unrelated_cookie=ignored',
             'Origin': 'http://10.20.30.40:25502', 'X-Qingcai-CSRF': 'csrf-fixture'})
        data = json.loads(content)
        self.assertEqual(status, 200)
        self.assertEqual(data['cookie'], 'qingcai_finance=test-finance')
        self.assertEqual(data['csrf'], 'csrf-fixture')
        self.assertEqual(data['origin'], 'http://10.20.30.40:25502')

    def test_host_spoofing_and_absolute_proxy_urls_rejected(self):
        cookie = self.cookie()
        self.assertEqual(self.request(headers={'Cookie': cookie, 'Host': 'example.com'})[0], 403)
        self.assertEqual(self.request(path='http://example.com/', headers={'Cookie': cookie})[0], 400)

    def test_oversized_and_ambiguous_bodies_rejected(self):
        headers = {'Cookie': self.cookie(), 'Origin': 'http://10.20.30.40:25502'}
        self.assertEqual(self.request('POST', headers={**headers, 'Content-Length': str(13 * 1024 * 1024)})[0], 413)
        self.assertEqual(self.request('POST', headers={**headers, 'Transfer-Encoding': 'chunked'})[0], 400)

    def test_ip_allowlist_rejects_other_clients(self):
        entry = ThreadingHTTPServer(('127.0.0.1', 0), make_handler({**self.config, 'clients': ['10.1.1.1']}))
        thread = threading.Thread(target=entry.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', entry.server_port, timeout=3)
        try:
            connection.request('GET', '/__open/' + self.config['token'], headers={'Host': '10.20.30.40:25502'})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()
            entry.shutdown()
            entry.server_close()
            thread.join()

    def test_token_is_redacted_from_logs(self):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.cookie()
        self.assertNotIn(self.config['token'], captured.getvalue())
        self.assertIn('[redacted]', captured.getvalue())

    def test_public_wildcard_and_loopback_listeners_refused(self):
        for host in ('0.0.0.0', '8.8.8.8', '127.0.0.1', '::'):
            with self.assertRaises(ValueError):
                validate_config({**self.config, 'host': host})


if __name__ == '__main__':
    unittest.main()
