#!/usr/bin/env bash
set -euo pipefail
run_id=${1:?run ID required}
if [[ ! "$run_id" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "invalid run ID" >&2
  exit 2
fi
project_root=$(cd "$(dirname "$0")/.." && pwd)
log_dir="$project_root/tmp/$run_id"
mkdir -p "$log_dir"
if [[ -e "$log_dir/started" ]]; then
  echo "refusing to reuse run ID" >&2
  exit 2
fi
date -u +%FT%TZ > "$log_dir/started"
finish() { exit_code=$?; printf '%s\n' "$exit_code" > "$log_dir/exit_code"; }
trap finish EXIT
cd "$project_root"
PYTHONPATH=src /home/gaojincheng/anaconda3/envs/HKAN/bin/python \
  scripts/run_multi_intervention_pilot.py --run-id "$run_id" \
  --base-url http://127.0.0.1:30000/v1 \
  > "$log_dir/stdout.log" 2> "$log_dir/stderr.log"
