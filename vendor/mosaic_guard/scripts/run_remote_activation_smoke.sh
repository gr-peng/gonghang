#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 RUN_ID" >&2
  exit 2
fi

run_id=$1
project_root=$(cd "$(dirname "$0")/.." && pwd)
run_dir="$project_root/tmp/$run_id"
model_call="$project_root/artifacts/model_smoke/$run_id/model_calls.jsonl"
python_bin=/home/gaojincheng/anaconda3/envs/HKAN/bin/python
model_path=/data/LLMzoo/Qwen3.8-27B

mkdir -p "$run_dir"
if [[ -e "$run_dir/exit_code" ]]; then
  echo "refusing to overwrite completed run metadata: $run_dir" >&2
  exit 2
fi

finish() {
  exit_code=$?
  printf '%s\n' "$exit_code" > "$run_dir/exit_code"
}
trap finish EXIT

cd "$project_root"
CUDA_VISIBLE_DEVICES=0,1,2,3 PYTHONPATH=src "$python_bin" \
  scripts/extract_prompt_activations.py \
  --model-path "$model_path" \
  --model-call "$model_call" \
  --output "$run_dir/activations.npz" \
  --manifest "$run_dir/activation_manifest.json" \
  > "$run_dir/stdout.log" \
  2> "$run_dir/stderr.log"
