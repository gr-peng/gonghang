"""Deterministic data minimization, not a universal semantic-harm classifier."""
from __future__ import annotations

import re
import unicodedata


_SECRETS = (
    re.compile(r'(?i)\b(?:bearer\s+)[A-Za-z0-9_.~+/=-]{8,}'),
    re.compile(r'(?i)(?:password|passwd|api[_ -]?key|access[_ -]?token|密码|验证码|动态口令)\s*[:：=]\s*\S+'),
    re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    re.compile(r'(?<!\d)\d{17}[0-9Xx](?!\d)'),
    re.compile(r'(?<!\d)\d{16,19}(?!\d)'),
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
)


def screen_text(text: str) -> dict:
    if not isinstance(text, str) or len(text) > 65_536:
        raise ValueError('text exceeds screening limit')
    clean = unicodedata.normalize('NFKC', text)
    removed_controls = any(unicodedata.category(ch) == 'Cf' for ch in clean)
    clean = ''.join(ch for ch in clean if unicodedata.category(ch) != 'Cf')
    count = 0
    for pattern in _SECRETS:
        clean, replaced = pattern.subn('[REDACTED]', clean)
        count += replaced
    return {'safe_text': clean, 'redactions': count, 'removed_format_controls': removed_controls,
            'policy': 'data-minimization-v1'}


def mask_account(value: str) -> str:
    # Fixed prefix and at most four trailing characters; short aliases fully hidden.
    return '****' + (value[-4:] if len(value) > 8 else '')


def public_params(params: dict) -> dict:
    return {key: mask_account(value) if key in ('from_account', 'to_account', 'account_id') else value
            for key, value in params.items() if key in ('from_account', 'to_account', 'account_id',
                                                       'amount_minor', 'balance_minor', 'status', 'action_digest')}


def validate_action_text(params: dict) -> None:
    for field, value in params.items():
        if not isinstance(value, str):
            continue
        if len(value) > (500 if field == 'memo' else 128):
            raise ValueError('action text exceeds limit')
        if any(unicodedata.category(ch).startswith('C') for ch in value):
            raise ValueError('control characters in action text')
        if field == 'memo' and screen_text(value)['redactions']:
            raise ValueError('sensitive data in action memo; ask user to remove it')
