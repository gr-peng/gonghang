from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .policy import PolicyEngine
from .version import __version__


SOURCE_ROOTS = (
    "src",
    "configs",
    "scripts",
    "tests",
    "docs",
    "schemas",
    "examples",
    "data",
    ".github",
)
TOP_LEVEL_FILES = (
    ".gitignore",
    "README.md",
    "SECURITY.md",
    "AGENTS.md",
    "pyproject.toml",
    "Makefile",
    "CHANGELOG.md",
    "CITATION.cff",
    "LICENSE",
    "artifacts/README.md",
)
# These artifacts are produced before the manifest. The manifest and summary are
# intentionally excluded to avoid self-referential hashes.
GENERATED_ARTIFACTS = (
    "artifacts/compile_report.txt",
    "artifacts/test_report.txt",
    "artifacts/coverage_test_report.txt",
    "artifacts/coverage_report.txt",
    "artifacts/demo_receipts.jsonl",
    "artifacts/demo_output.json",
    "artifacts/planning_demo.json",
    "artifacts/planning_demo_receipts.jsonl",
    "artifacts/runtime_safety_demo.json",
    "artifacts/runtime_safety_receipts.jsonl",
    "artifacts/execution_once_demo.json",
    "artifacts/execution_once_receipts.jsonl",
    "artifacts/engineering_demo.json",
    "artifacts/engineering_demo_receipts.jsonl",
    "artifacts/engineering-acceptance-20260904/sandbox_acceptance.json",
    "artifacts/engineering-acceptance-20260904/MOSAIC_Guard_安全自评报告.docx",
    "artifacts/engineering-acceptance-20260904/official-self-assessment-template.docx",
    "artifacts/engineering-acceptance-20260904/official-domestic-requirements.js",
    "artifacts/synthetic_results.json",
    "artifacts/synthetic_summary.json",
    "artifacts/receipt_verification.json",
)
EXPERIMENT_ARTIFACT_ROOTS = (
    "artifacts/model_smoke", "artifacts/pilot_runs", "artifacts/intervention_runs",
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _hash_tree(project_root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for relative_root in SOURCE_ROOTS:
        directory = project_root / relative_root
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            relative_parts = path.relative_to(project_root).parts
            if any(part.endswith(".egg-info") for part in relative_parts):
                continue
            files[str(path.relative_to(project_root))] = _hash_file(path)
    for filename in TOP_LEVEL_FILES:
        path = project_root / filename
        if path.exists():
            files[filename] = _hash_file(path)
    return dict(sorted(files.items()))


def _hash_generated_artifacts(project_root: Path) -> dict[str, str]:
    hashes = {
        relative: _hash_file(project_root / relative)
        for relative in GENERATED_ARTIFACTS
        if (project_root / relative).is_file()
    }
    for relative_root in (*EXPERIMENT_ARTIFACT_ROOTS, "artifacts/security-review-20260906"):
        for path in sorted((project_root / relative_root).rglob("*")):
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__"
                                          for part in path.relative_to(project_root).parts):
                hashes[str(path.relative_to(project_root))] = _hash_file(path)
    return dict(sorted(hashes.items()))


def preserved_model_manifests(project_root: Path) -> list[str]:
    names = {"manifest.json", "run_manifest.json", "activation_manifest.json"}
    return sorted(str(path.relative_to(project_root))
                  for relative in EXPERIMENT_ARTIFACT_ROOTS
                  for path in (project_root / relative).rglob("*.json")
                  if path.is_file() and path.name in names)


def build_manifest(root: str | Path) -> dict[str, Any]:
    project_root = Path(root).resolve()
    policy = PolicyEngine.from_yaml(project_root / "configs" / "transfer_policy.yaml")
    artifact_hashes = _hash_generated_artifacts(project_root)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            "pydantic": _package_version("pydantic"),
            "PyYAML": _package_version("PyYAML"),
            "pytest": _package_version("pytest"),
            "coverage": _package_version("coverage"),
            "numpy": _package_version("numpy"),
            "scikit-learn": _package_version("scikit-learn"),
            "z3-solver": _package_version("z3-solver"),
        },
        "git_commit": _git_commit(project_root),
        "policy": {
            "policy_id": policy.spec.policy_id,
            "version": policy.spec.version,
            "digest": policy.spec.digest,
        },
        "claim_status": {
            "synthetic_results": "deterministic_code_path_smoke_test_only",
            "real_llm_results": (
                "saved_exploratory_artifacts_present_not_reexecuted_by_make"
                if any(path.startswith(EXPERIMENT_ARTIFACT_ROOTS) for path in artifact_hashes)
                else "not_run"
            ),
            "paper_novelty": "not_established",
            "production_readiness": "not_established",
        },
        "source_file_sha256": _hash_tree(project_root),
        "generated_artifact_sha256": artifact_hashes,
    }


def verify_manifest(root: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Verify every file hash declared in a previously generated manifest.

    The manifest is an integrity aid for a delivered research bundle. It is not
    a signature and does not establish who created or approved the archive.
    """

    project_root = Path(root).resolve()
    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = project_root / manifest_file
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))

    declared: dict[str, str] = {}
    for section in ("source_file_sha256", "generated_artifact_sha256"):
        values = payload.get(section, {})
        if not isinstance(values, dict):
            raise ValueError(f"manifest section {section!r} must be an object")
        for relative, expected in values.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ValueError(f"invalid hash entry in manifest section {section!r}")
            declared[relative] = expected

    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    verified = 0
    for relative, expected in sorted(declared.items()):
        candidate = (project_root / relative).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(f"manifest path escapes project root: {relative!r}") from exc
        if not candidate.is_file():
            missing.append(relative)
            continue
        actual = _hash_file(candidate)
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
        else:
            verified += 1

    return {
        "valid": not missing and not mismatches,
        "verified_count": verified,
        "declared_count": len(declared),
        "missing": missing,
        "mismatches": mismatches,
        "limitation": "hash verification is not publisher authentication or a digital signature",
    }


def write_manifest(root: str | Path, output: str | Path) -> Path:
    manifest = build_manifest(root)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
