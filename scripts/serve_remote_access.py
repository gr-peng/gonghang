"""Private-network entry to the existing loopback website; no third-party tunnel.

Access requires both the configured client address and the private opening link.
The ledger/model services remain on loopback. Runtime credentials are not tracked.
"""
import argparse
import hashlib
import hmac
import http.client
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.runtime' / 'remote-access.json'
COOKIE = 'qingcai_access'
MAX_BODY = 12 * 1024 * 1024


def validate_config(config):
    # Deliberately refuse wildcard/public listeners, even if misconfigured.
    address = ipaddress.ip_address(config['host'])
    private_ranges = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    if not any(address in network for network in private_ranges):
        raise ValueError('The entry must bind to one private IPv4 address.')
    for key in ('port', 'upstream_port'):
        if not 1024 <= config[key] <= 65535:
            raise ValueError('Invalid port.')
    if config['port'] == config['upstream_port']:
        raise ValueError('Use a separate entry port.')
    if len(config['token']) < 40 or not config['clients']:
        raise ValueError('A private link and explicit client list are required.')
    for client in config['clients']:
        ipaddress.ip_address(client)
    return config


def opening_url(config):
    return f"http://{config['host']}:{config['port']}/__open/{config['token']}"


def make_handler(config):
    validate_config(config)
    authority = f"{config['host']}:{config['port']}"
    origin = 'http://' + authority
    allowed = set(config['clients']) | {config['host']}
    session = hmac.new(config['token'].encode(), b'qingcai-browser-session', hashlib.sha256).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Qingcai'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(190)

        def log_message(self, fmt, *args):
            # Never write the private opening token or query/body into logs.
            path = urlsplit(self.path).path
            if path.startswith('/__open/'):
                path = '/__open/[redacted]'
            print(json.dumps({'client': self.client_address[0], 'method': self.command,
                              'path': path, 'status': getattr(self, '_status', None)},
                             ensure_ascii=False), flush=True)

        def send_response(self, code, message=None):
            self._status = code
            super().send_response(code, message)

        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            super().end_headers()

        def message(self, code, text):
            content = json.dumps({'detail': text}, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(content)

        def handle_request(self):
            if self.client_address[0] not in allowed:
                return self.message(403, '此访问入口仅限已配置的设备。')
            if self.headers.get('Host') != authority:
                return self.message(403, '访问地址不匹配。')
            if not self.path.startswith('/') or self.path.startswith('//'):
                return self.message(400, '无效地址。')
            if self.path.startswith('/__open/'):
                supplied = self.path[len('/__open/'):]
                if self.command != 'GET' or not hmac.compare_digest(supplied.encode(), config['token'].encode()):
                    return self.message(403, '请使用本次提供的打开网站链接。')
                self.send_response(303)
                self.send_header('Set-Cookie', f'{COOKIE}={session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000')
                self.send_header('Location', '/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                supplied = cookies[COOKIE].value if COOKIE in cookies else ''
            except CookieError:
                supplied = ''
            if not hmac.compare_digest(supplied.encode(), session.encode()):
                return self.message(401, '请使用本次提供的打开网站链接。')
            # SameSite does not separate different ports on one host; check the
            # exact browser Origin on every mutation as well.
            if self.command not in {'GET', 'HEAD'} and self.headers.get('Origin') != origin:
                return self.message(403, '请在网站内执行此操作。')
            if self.headers.get('Transfer-Encoding'):
                return self.message(400, '不支持此请求格式。')
            lengths = self.headers.get_all('Content-Length', [])
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                return self.message(400, '无效的请求长度。')
            if len(lengths) > 1 or length < 0:
                return self.message(400, '无效的请求长度。')
            if length > MAX_BODY:
                return self.message(413, '文件过大。')
            body = self.rfile.read(length) if length else None
            if body is not None and len(body) != length:
                return self.message(400, '请求未完整发送。')
            headers = {key: self.headers[key] for key in ('Content-Type', 'Accept', 'Origin', 'X-Qingcai-CSRF') if key in self.headers}
            if 'qingcai_finance' in cookies:
                headers['Cookie'] = cookies['qingcai_finance'].OutputString(attrs=[])
            # Fixed upstream, no proxy environment variables, no redirects to
            # arbitrary hosts, and no forwarding of gateway cookies.
            connection = http.client.HTTPConnection('127.0.0.1', config['upstream_port'], timeout=180)
            try:
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                content = response.read()
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
                self.send_header('Content-Length', response.getheader('Content-Length', '0') if self.command == 'HEAD' else str(len(content)))
                if self.path.startswith('/api/'):
                    self.send_header('Cache-Control', 'no-store')
                for key, value in response.getheaders():
                    if key.lower() == 'set-cookie' and value.startswith('qingcai_finance='):
                        self.send_header('Set-Cookie', value)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, http.client.HTTPException):
                self.message(502, '网站正在恢复连接，请稍后刷新。')
            finally:
                connection.close()

        do_GET = handle_request
        do_HEAD = handle_request
        do_POST = handle_request
        do_DELETE = handle_request
        do_PUT = handle_request
        do_PATCH = handle_request

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-from-ssh', action='store_true')
    parser.add_argument('--show-url', action='store_true')
    args = parser.parse_args()
    if args.init_from_ssh and not CONFIG.exists():
        connection = os.environ.get('SSH_CONNECTION', '').split()
        if len(connection) != 4:
            raise SystemExit('SSH_CONNECTION is required for initialization.')
        config = validate_config({'host': connection[2], 'port': 25502,
                                  'upstream_port': 25500, 'clients': [connection[0]],
                                  'token': secrets.token_urlsafe(32)})
        CONFIG.parent.mkdir(exist_ok=True)
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(config, file, indent=2)
    config = validate_config(json.loads(CONFIG.read_text()))
    if args.show_url:
        print(opening_url(config))
        return
    server = ThreadingHTTPServer((config['host'], config['port']), make_handler(config))
    (CONFIG.parent/'remote-access.pid').write_text(str(os.getpid())+'\n')
    print(f"Private website entry listening on {config['host']}:{config['port']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
