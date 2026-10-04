from pathlib import Path

from scns_guard.reproducibility import write_manifest

ROOT = Path(__file__).resolve().parents[1]
print(write_manifest(ROOT, ROOT / "artifacts" / "reproducibility_manifest.json"))
