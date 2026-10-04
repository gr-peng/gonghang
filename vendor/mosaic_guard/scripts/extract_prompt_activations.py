from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scns_guard.activations import validate_source_token_spans
from scns_guard.adapters.openai_compatible import GenerationTrace


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture per-layer prompt-source activations for one recorded model call"
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-call", type=Path, required=True)
    parser.add_argument("--call-index", type=int, default=0)
    parser.add_argument("--all-calls", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--max-memory-gib", type=int, default=28)
    parser.add_argument("--attn-implementation", default="sdpa")
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def load_calls(path: Path) -> tuple[GenerationTrace, ...]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return tuple(GenerationTrace.model_validate_json(row) for row in rows)


def main() -> int:
    args = parse_args()
    if args.output.exists() or args.manifest.exists():
        raise SystemExit("refusing to overwrite an activation artifact or manifest")
    all_calls = load_calls(args.model_call)
    if not all_calls:
        raise SystemExit("model call file is empty")
    if args.all_calls:
        selected_calls = tuple(enumerate(all_calls))
    else:
        if not 0 <= args.call_index < len(all_calls):
            raise SystemExit(
                f"call index {args.call_index} is outside 0..{len(all_calls) - 1}"
            )
        selected_calls = ((args.call_index, all_calls[args.call_index]),)
    try:
        import torch
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit("torch and transformers are required for activation capture") from exc

    tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
    prepared = []
    for call_index, call in selected_calls:
        chat_kwargs = call.decoding.get("extra_body", {}).get("chat_template_kwargs", {})
        rendered_prompt = tokenizer.apply_chat_template(
            list(call.prompt_messages),
            tokenize=False,
            add_generation_prompt=True,
            **chat_kwargs,
        )
        encoded = tokenizer(
            rendered_prompt,
            add_special_tokens=False,
            return_tensors="pt",
        )
        input_ids = encoded["input_ids"]
        flat_token_ids = tuple(int(token_id) for token_id in input_ids[0].tolist())
        expected_token_hash = call.tokenization.get("full_prompt_token_ids_sha256")
        if not isinstance(expected_token_hash, str):
            raise SystemExit(
                f"model call {call_index} does not contain a server tokenization hash"
            )
        spans = validate_source_token_spans(
            call.source_spans,
            token_ids=flat_token_ids,
            expected_token_ids_sha256=expected_token_hash,
        )
        prepared.append(
            (call_index, call, input_ids, flat_token_ids, expected_token_hash, spans)
        )
    reference_call = prepared[0][1]
    for _, call, *_ in prepared[1:]:
        if (
            call.model,
            call.model_revision,
            call.tokenizer_revision,
        ) != (
            reference_call.model,
            reference_call.model_revision,
            reference_call.tokenizer_revision,
        ):
            raise SystemExit("selected calls do not share one frozen model/tokenizer")
    if args.verify_only:
        print(
            json.dumps(
                {
                    "status": "verified",
                    "calls": [
                        {
                            "call_index": call_index,
                            "prompt_tokens": len(flat_token_ids),
                            "source_spans": [
                                span.model_dump(mode="json") for span in spans
                            ],
                        }
                        for call_index, _, _, flat_token_ids, _, spans in prepared
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0

    config = AutoConfig.from_pretrained(args.model_path, trust_remote_code=True)
    visible_gpu_count = torch.cuda.device_count()
    if visible_gpu_count < 1:
        raise SystemExit("no CUDA device is visible")
    max_memory = {
        index: f"{args.max_memory_gib}GiB" for index in range(visible_gpu_count)
    }
    started_at = datetime.now(timezone.utc)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_path,
        dtype=torch.bfloat16,
        device_map="balanced",
        max_memory=max_memory,
        low_cpu_mem_usage=True,
        trust_remote_code=True,
        attn_implementation=args.attn_implementation,
    )
    model.eval()
    embedding_device = model.get_input_embeddings().weight.device

    try:
        import numpy as np
    except ImportError as exc:
        raise SystemExit("numpy is required to save activation arrays") from exc

    arrays: dict[str, object] = {}
    call_entries: list[dict[str, object]] = []
    hidden_state_count: int | None = None
    for call_index, call, input_ids, flat_token_ids, expected_token_hash, spans in prepared:
        with torch.inference_mode():
            outputs = model(
                input_ids=input_ids.to(embedding_device),
                use_cache=False,
                output_hidden_states=True,
                return_dict=True,
            )
        hidden_states = outputs.hidden_states
        if hidden_states is None:
            raise RuntimeError(f"model did not return hidden states for call {call_index}")
        if hidden_state_count is None:
            hidden_state_count = len(hidden_states)
        elif hidden_state_count != len(hidden_states):
            raise RuntimeError("hidden-state count changed across recorded calls")
        source_entries: list[dict[str, object]] = []
        for source_index, span in enumerate(spans):
            assert span.token_start is not None and span.token_end is not None
            means = []
            first_tokens = []
            last_tokens = []
            for layer_state in hidden_states:
                selected = layer_state[0, span.token_start : span.token_end].float()
                means.append(selected.mean(dim=0).cpu().numpy())
                first_tokens.append(selected[0].cpu().numpy())
                last_tokens.append(selected[-1].cpu().numpy())
            prefix = f"call_{call_index:04d}_source_{source_index:03d}"
            arrays[f"{prefix}_mean"] = np.stack(means)
            arrays[f"{prefix}_first"] = np.stack(first_tokens)
            arrays[f"{prefix}_last"] = np.stack(last_tokens)
            source_entries.append(
                {
                    "array_prefix": prefix,
                    "source_id": span.source_id,
                    "token_start": span.token_start,
                    "token_end": span.token_end,
                    "pooling_views": ["mean", "first", "last"],
                }
            )
        call_entries.append(
            {
                "call_index": call_index,
                "request_digest": call.request_digest,
                "response_digest": call.response_digest,
                "prompt_token_count": len(flat_token_ids),
                "prompt_token_ids_sha256": expected_token_hash,
                "source_spans": source_entries,
            }
        )
        del outputs, hidden_states

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, **arrays)
    completed_at = datetime.now(timezone.utc)
    text_config = getattr(config, "text_config", config)
    manifest = {
        "evidence_boundary": "prompt_activation_capture_not_security_evaluation",
        "model": prepared[0][1].model,
        "model_revision": prepared[0][1].model_revision,
        "tokenizer_revision": prepared[0][1].tokenizer_revision,
        "inference_engine_for_generation": prepared[0][1].inference_engine,
        "engine_revision_for_generation": prepared[0][1].engine_revision,
        "activation_runtime": {
            "transformers": __import__("transformers").__version__,
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "visible_gpu_count": visible_gpu_count,
            "dtype": "float32_saved_from_bfloat16_forward",
            "device_map": "balanced",
            "max_memory_gib_per_visible_gpu": args.max_memory_gib,
            "attention_implementation": args.attn_implementation,
        },
        "call_count": len(call_entries),
        "calls": call_entries,
        "hidden_state_count": hidden_state_count,
        "configured_hidden_layers": getattr(text_config, "num_hidden_layers", None),
        "hidden_size": getattr(text_config, "hidden_size", None),
        "capture": "all hidden states; source-span mean, first token, and last token",
        "generated_token_activations": "not_captured",
        "model_call_path": str(args.model_call.resolve()),
        "model_call_sha256": file_sha256(args.model_call),
        "model_call_indices": [entry[0] for entry in prepared],
        "activation_file": str(args.output.resolve()),
        "activation_file_sha256": file_sha256(args.output),
        "runner_sha256": file_sha256(Path(__file__)),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
    }
    args.manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
