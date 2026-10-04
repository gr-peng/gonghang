from __future__ import annotations

import json
from pathlib import Path

from scns_guard.reproducibility import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "artifacts" / "reproducibility_manifest.json"

result = verify_manifest(ROOT, MANIFEST)
print(json.dumps(result, indent=2, ensure_ascii=False))
raise SystemExit(0 if result["valid"] else 1)
