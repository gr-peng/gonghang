from __future__ import annotations

import json

from scns_guard.schemas import export_schemas


def test_json_schema_export(tmp_path) -> None:
    paths = export_schemas(tmp_path)
    assert len(paths) >= 8
    assert all(path.exists() and path.stat().st_size > 100 for path in paths)


def test_transfer_schema_exposes_integer_minor_units(tmp_path) -> None:
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "transfer_params.schema.json").read_text())
    amount = schema["properties"]["amount_minor"]

    assert amount["type"] == "integer"
    assert amount["exclusiveMinimum"] == 0
    assert amount["maximum"] == 2**63 - 1
