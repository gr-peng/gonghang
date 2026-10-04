from pathlib import Path

from scns_guard.schemas import export_schemas

ROOT = Path(__file__).resolve().parents[1]
for path in export_schemas(ROOT / "schemas"):
    print(path)
