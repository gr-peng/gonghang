"""Portable signed prefix checkpoints; the verifier/sink must be independent."""
import hashlib
import hmac
from datetime import datetime, timezone

from .canonical import canonical_json, sha256_hex
from .receipts import ReceiptLedger


def sign_checkpoint(chain_id, receipts, events, secret: bytes) -> dict:
    if len(secret) < 32:
        raise ValueError('audit signing key must be at least 256 bits')
    ReceiptLedger.verify_chain(receipts)
    payload = {'version': 1, 'chain_id': chain_id, 'receipt_count': len(receipts),
               'receipt_head': receipts[-1].receipt_hash if receipts else None,
               'event_count': len(events), 'event_digest': sha256_hex(events),
               'created_at': datetime.now(timezone.utc).isoformat()}
    return {**payload, 'signature': hmac.new(secret, canonical_json(payload).encode(), hashlib.sha256).hexdigest()}


def verify_checkpoint(checkpoint: dict, receipts, events, secret: bytes) -> bool:
    try:
        unsigned = {key: value for key, value in checkpoint.items() if key != 'signature'}
        expected = hmac.new(secret, canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, checkpoint['signature']) or checkpoint['version'] != 1:
            return False
        n, m = checkpoint['receipt_count'], checkpoint['event_count']
        if type(n) is not int or type(m) is not int or not 0 <= n <= len(receipts) or not 0 <= m <= len(events):
            return False
        ReceiptLedger.verify_chain(receipts)
        head = receipts[n - 1].receipt_hash if n else None
        return head == checkpoint['receipt_head'] and sha256_hex(events[:m]) == checkpoint['event_digest']
    except (ValueError, KeyError, TypeError):
        return False
