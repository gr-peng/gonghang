from __future__ import annotations

from pathlib import Path

import pytest

from scns_guard.experiment import default_components


@pytest.fixture
def root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def components(root: Path):
    return default_components(root / "configs" / "transfer_policy.yaml")
