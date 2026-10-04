import argparse
import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie, CookieError
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import mimetypes

parser = argparse.ArgumentParser()
parser.add_argument('--host', default='127.0.0.1')
parser.add_argument('--demo', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1] / 'AI_accounting_agent' / 'frontend' / 'liquid-glass'
project_root = Path(__file__).resolve().parents[1]
materials_root = project_root / 'artifacts' / 'submission-20261003'
MATERIALS = {'Qingcai-presentation.pdf', 'Qingcai-presentation.pptx', 'Qingcai-source.zip',
             'Qingcai-documents.zip', 'FinPilot-presentation.pdf', 'FinPilot-presentation.pptx',
             'FinPilot-source.zip', 'FinPilot-documents.zip', 'VALIDATION.json'}


class Handler(SimpleHTTPRequestHandler):
    def material(self):
        name = self.path.removeprefix('/__materials/')
        if name not in MATERIALS:
            self.send_error(404)
            return
        canonical=name.replace('Qingcai-','FinPilot-',1)
        candidates=[materials_root/canonical,project_root/'materials'/canonical,materials_root/name]
        if name=='VALIDATION.json':candidates.append(project_root/name)
        path=next((candidate for candidate in candidates if candidate.is_file()),None)
        if path is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(path.stat().st_size))
        self.send_header('Content-Disposition',('inline' if name.endswith('.pdf') else 'attachment')+f'; filename="{name}"')
        self.end_headers()
        if self.command!='HEAD':
            with path.open('rb') as file:
                import shutil
                shutil.copyfileobj(file,self.wfile)

    def proxy(self):
        parts = self.path.split('/', 3)
        if len(parts) != 4 or parts[1] != 'api' or parts[2] not in {'book', 'trader'}:
            self.send_error(404)
            return
        port = int(os.getenv('BOOKKEEPER_PORT' if parts[2] == 'book' else 'TRADER_PORT', '8010' if parts[2] == 'book' else '8020'))
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 12 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {key: self.headers[key] for key in ('Content-Type', 'Accept', 'Origin', 'X-Qingcai-CSRF') if key in self.headers}
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            if 'qingcai_finance' in cookies:
                headers['Cookie'] = cookies['qingcai_finance'].OutputString(attrs=[])
        except CookieError:
            pass
        req = Request(f'http://127.0.0.1:{port}/' + parts[3], data=body, headers=headers, method=self.command)
        try:
            response = urlopen(req, timeout=180)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError):
            content = json.dumps({'detail':'后端暂时不可用，请稍后重试。'}, ensure_ascii=False).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        with response:
            content = response.read()
            self.send_response(response.status if hasattr(response, 'status') else response.code)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            for cookie in response.headers.get_all('Set-Cookie', []):
                if cookie.startswith('qingcai_finance='):
                    self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(content)

    def do_GET(self):
        if self.path.startswith('/__materials/'):
            return self.material()
        if self.path.startswith('/api/'):
            return self.proxy()
        if self.path == '/runtime-config.js':
            ports = {'book': int(os.getenv('BOOKKEEPER_PORT', '8010')), 'trader': int(os.getenv('TRADER_PORT', '8020'))}
            content = f'window.FINTECH_SAME_ORIGIN = true; window.FINTECH_DEMO = {str(args.demo).lower()}; window.FINTECH_PORTS = {json.dumps(ports)};'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path.startswith('/__materials/'):
            return self.material()
        super().do_HEAD()

    def do_POST(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def do_PUT(self):
        self.proxy()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


ThreadingHTTPServer((args.host, int(os.getenv('FRONTEND_PORT', '5500'))), partial(Handler, directory=str(root))).serve_forever()
