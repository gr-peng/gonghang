from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scns_guard.reproducibility import _hash_tree, _hash_generated_artifacts, verify_manifest
from scns_guard.reproducibility import preserved_model_manifests


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verify_manifest_detects_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    artifact = tmp_path / "artifact.json"
    source.write_text("source\n", encoding="utf-8")
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_file_sha256": {"source.txt": sha256(source)},
                "generated_artifact_sha256": {"artifact.json": sha256(artifact)},
            }
        ),
        encoding="utf-8",
    )

    assert verify_manifest(tmp_path, manifest)["valid"] is True
    artifact.write_text('{"changed": true}\n', encoding="utf-8")
    result = verify_manifest(tmp_path, manifest)
    assert result["valid"] is False
    assert result["mismatches"][0]["path"] == "artifact.json"


def test_verify_manifest_rejects_path_escape(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_file_sha256": {"../outside": "0" * 64},
                "generated_artifact_sha256": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes project root"):
        verify_manifest(tmp_path, manifest)


def test_source_manifest_excludes_editable_install_metadata(tmp_path: Path) -> None:
    package = tmp_path / "src" / "scns_guard"
    package.mkdir(parents=True)
    source = package / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    egg_info = tmp_path / "src" / "source_causal_nesy_guard.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_text("generated metadata\n", encoding="utf-8")

    hashed = _hash_tree(tmp_path)

    assert "src/scns_guard/module.py" in hashed
    assert not any(".egg-info/" in path for path in hashed)


def test_experimental_artifacts_are_included_in_bundle_hashes(tmp_path):
    record = tmp_path / "artifacts/intervention_runs/test-run/outcomes.jsonl"
    record.parent.mkdir(parents=True)
    record.write_text('{}\n')
    hashes = _hash_generated_artifacts(tmp_path)
    relative = str(record.relative_to(tmp_path))
    assert hashes[relative] == sha256(record)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_file_sha256": {}, "generated_artifact_sha256": hashes}))
    record.write_text('{"tampered":true}\n')
    assert not verify_manifest(tmp_path, manifest)["valid"]


def test_saved_run_manifests_are_discovered_without_claiming_rerun(tmp_path):
    path = tmp_path / "artifacts/intervention_runs/new-run/manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"complete"}')
    assert preserved_model_manifests(tmp_path) == [str(path.relative_to(tmp_path))]
