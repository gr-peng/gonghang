from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .canonical import canonical_json, sha256_hex
from .models import DecisionReceipt, FormalReceiptLayer, InternalEvidenceLayer


class ReceiptIntegrityError(ValueError):
    pass


class ReceiptLedger:
    """Append-only hash chain for dual-layer decision receipts."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._receipts: list[DecisionReceipt] = []
        if self.path is not None and self.path.exists():
            self._receipts = self.load_jsonl(self.path)
            self.verify_chain(self._receipts)

    @property
    def receipts(self) -> tuple[DecisionReceipt, ...]:
        return tuple(self._receipts)

    def append(
        self,
        formal: FormalReceiptLayer,
        internal: InternalEvidenceLayer,
    ) -> DecisionReceipt:
        previous = self._receipts[-1].receipt_hash if self._receipts else None
        receipt = DecisionReceipt(
            sequence=len(self._receipts) + 1,
            previous_receipt_hash=previous,
            formal=formal,
            internal=internal,
        )
        sealed = receipt.model_copy(update={"receipt_hash": sha256_hex(receipt.unsigned_payload())})
        self._receipts.append(sealed)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(sealed))
                handle.write("\n")
        return sealed

    @staticmethod
    def load_jsonl(path: str | Path) -> list[DecisionReceipt]:
        receipts: list[DecisionReceipt] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    receipts.append(DecisionReceipt.model_validate_json(line))
                except Exception as exc:  # pragma: no cover - pydantic detail varies
                    raise ReceiptIntegrityError(
                        f"invalid receipt JSON at line {line_number}: {exc}"
                    ) from exc
        return receipts

    @staticmethod
    def verify_chain(receipts: Iterable[DecisionReceipt]) -> None:
        previous: str | None = None
        expected_sequence = 1
        for receipt in receipts:
            if receipt.sequence != expected_sequence:
                raise ReceiptIntegrityError(
                    f"sequence mismatch: expected {expected_sequence}, got {receipt.sequence}"
                )
            if receipt.previous_receipt_hash != previous:
                raise ReceiptIntegrityError(
                    f"previous hash mismatch at sequence {receipt.sequence}"
                )
            expected_hash = sha256_hex(receipt.unsigned_payload())
            if receipt.receipt_hash != expected_hash:
                raise ReceiptIntegrityError(
                    f"receipt hash mismatch at sequence {receipt.sequence}"
                )
            previous = receipt.receipt_hash
            expected_sequence += 1


def dump_receipts(path: str | Path, receipts: Iterable[DecisionReceipt]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for receipt in receipts:
            handle.write(canonical_json(receipt))
            handle.write("\n")
