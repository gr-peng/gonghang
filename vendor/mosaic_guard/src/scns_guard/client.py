"""Small team integration client. Never retries writes or issues credentials."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .gateway import ReviewRequest


class SafetyServiceError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafetyServiceError('safety service redirects are not permitted')


class SafetyClient:
    def __init__(self, base_url: str, credential: str, *, timeout: float = 5):
        url = urlsplit(base_url)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')
                or (url.scheme == 'http' and url.hostname != '127.0.0.1') or not 0 < timeout <= 60):
            raise ValueError('use an HTTPS origin or explicit local loopback, without URL credentials')
        self.base_url, self.credential, self.timeout = base_url.rstrip('/'), credential, timeout
        self._opener = build_opener(_NoRedirect())

    def post(self, path: str, body: dict) -> dict:
        if not path.startswith('/v1/') or any(ch in path for ch in ('?', '#', '\r', '\n')):
            raise ValueError('invalid safety endpoint')
        request = Request(self.base_url + path, data=json.dumps(body, allow_nan=False).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.credential})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(262_145)
                if len(raw) > 262_144:
                    raise SafetyServiceError('safety response exceeds limit')
                return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise SafetyServiceError('request did not produce a verified response; retain handle and reconcile, do not submit a new write') from exc

    def review(self, request: ReviewRequest):
        return self.post('/v1/review', request.model_dump(mode='json'))

    def execute(self, handle: str):
        return self.post('/v1/execute', {'handle': handle})

    def approve(self, handle: str, proof: str):
        return self.post('/v1/approve', {'handle': handle, 'proof': proof})

    def cancel(self, handle: str, proof: str):
        return self.post('/v1/cancel', {'handle': handle, 'proof': proof})
