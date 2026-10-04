from __future__ import annotations

import argparse
import json
from pathlib import Path

from scns_guard.run_audit import audit_intervention_run


parser = argparse.ArgumentParser()
parser.add_argument("run_dir", type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
result = audit_intervention_run(args.run_dir)
text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
print(text, end="")
