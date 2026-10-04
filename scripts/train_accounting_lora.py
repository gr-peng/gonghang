"""Local Qwen3 LoRA SFT with assistant-only loss and held-out evaluation.

Uses installed torch/transformers plus isolated PEFT, and never overwrites base weights.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import random
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'AI_accounting_agent/backend'))
import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup
from peft import LoraConfig, get_peft_model, PeftModel


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


class Samples(Dataset):
    def __init__(self, rows, tokenizer, max_length):
        self.items = []
        for row in rows:
            prompt = tokenizer.apply_chat_template(row["messages"][:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
            head = tokenizer.encode(prompt, add_special_tokens=False)
            tail = tokenizer.encode(row["messages"][-1]["content"] + tokenizer.eos_token, add_special_tokens=False)
            if len(head) + len(tail) > max_length:
                raise ValueError(f"Sample exceeds {max_length} tokens: {len(head) + len(tail)}; do not silently truncate labels")
            self.items.append({"input_ids": head + tail, "labels": [-100] * len(head) + tail})

    def __len__(self): return len(self.items)
    def __getitem__(self, index): return self.items[index]


def collate(items, pad):
    length = max(len(i["input_ids"]) for i in items)
    return {"input_ids": torch.tensor([i["input_ids"] + [pad] * (length-len(i["input_ids"])) for i in items]),
            "labels": torch.tensor([i["labels"] + [-100] * (length-len(i["labels"])) for i in items]),
            "attention_mask": torch.tensor([[1] * len(i["input_ids"]) + [0] * (length-len(i["input_ids"])) for i in items])}


def loss_eval(model, loader):
    model.eval()
    weighted = total = 0
    with torch.inference_mode():
        for batch in loader:
            n = (batch["labels"][:, 1:] != -100).sum().item()
            loss = model(**{k:v.to(model.device) for k,v in batch.items()}).loss.item()
            weighted += loss * n
            total += n
    return weighted / total


def evaluate_generation(model, tokenizer, rows):
    model.eval()
    # One batch per category sample, plus clarification and summaries.
    outputs = []
    tokenizer.padding_side = "left"
    for start in range(0, len(rows), 6):
        subset = rows[start:start+6]
        prompts = [tokenizer.apply_chat_template(r["messages"][:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False) for r in subset]
        inputs = tokenizer(prompts, padding=True, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=256, do_sample=False, pad_token_id=tokenizer.pad_token_id)
        texts = tokenizer.batch_decode(generated[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)
        for r, text in zip(subset, texts):
            item = {"task": r["task"], "input": r["messages"][-2]["content"], "expected": r["messages"][-1]["content"], "actual": text}
            try:
                target, actual = json.loads(item["expected"]), json.loads(text)
                item["valid_json"] = True
                if r["task"] == "extract":
                    fields = ["event_date", "category", "type", "amount", "currency", "payment_method"]
                    item["fields"] = {k: actual.get(k) == target[k] for k in fields}
                    item["correct"] = all(item["fields"].values())
                elif r['task'] == 'bank_intent':
                    from decimal import Decimal
                    aliases = {'朋友':'小林','13800000001':'小林','房租':'房东','13800000002':'房东'}
                    fields = ('intent','recipient','product_id','amount')
                    item['fields'] = {k: actual.get(k) == target.get(k) for k in fields}
                    item['fields']['recipient'] = aliases.get(actual.get('recipient'), actual.get('recipient')) == aliases.get(target.get('recipient'), target.get('recipient'))
                    if target.get('amount') is not None:
                        import re
                        amount=actual.get('amount')
                        item['fields']['amount'] = isinstance(amount,str) and bool(re.fullmatch(r'(0|[1-9]\d{0,7})(\.\d{1,2})?',amount)) and Decimal(amount) == Decimal(target['amount'])
                    item['correct'] = not (set(actual)-set(fields)) and all(item['fields'].values())
                elif r['task'] == 'profile_reason':
                    from finance_schema import validate_plan_selection
                    item['correct'] = bool(validate_plan_selection(actual,json.loads(r['messages'][-2]['content'])))
                else:
                    item["correct"] = actual.get("needs_clarification") is True
            except (ValueError, TypeError, AttributeError, ArithmeticError):
                item["valid_json"] = False
                item["correct"] = False
            if r["task"] == "summary":
                import re
                from decimal import Decimal
                numbers = [Decimal(n) for n in re.findall(r"-?\d+\.\d{2}", item["expected"])]
                actual_numbers = {Decimal(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))}
                item["correct"] = all(n in actual_numbers for n in numbers)
            outputs.append(item)
        print(json.dumps({"stage":"generation_evaluation", "completed":len(outputs), "total":len(rows)}), flush=True)
    tokenizer.padding_side = "right"
    by_task = {}
    for task in {r["task"] for r in rows}:
        subset = [r for r in outputs if r["task"] == task]
        by_task[task] = {"count": len(subset), "correct": sum(r["correct"] for r in subset), "accuracy": sum(r["correct"] for r in subset)/len(subset)}
    return {"tasks": by_task, "outputs": outputs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--accumulation", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=768)
    parser.add_argument('--initial-adapter', type=Path)
    parser.add_argument('--learning-rate', type=float, default=1e-4)
    parser.add_argument('--evaluate-all', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "adapter_model.safetensors").exists(): raise SystemExit("Refusing to overwrite a trained adapter")
    torch.manual_seed(20261003)
    random.seed(20261003)
    torch.set_num_threads(8)
    if not torch.cuda.is_available(): raise SystemExit("CUDA unavailable; training was not started")
    tokenizer = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.base, dtype=torch.bfloat16, device_map={"":0}, attn_implementation="sdpa", local_files_only=True)
    if args.initial_adapter:
        model = PeftModel.from_pretrained(model, args.initial_adapter, is_trainable=True, local_files_only=True)
    train_rows, val_rows, test_rows = (read(args.data / f"{name}.jsonl") for name in ["train", "validation", "test"])
    train = Samples(train_rows, tokenizer, args.max_length)
    validation = Samples(val_rows, tokenizer, args.max_length)
    def batch(items): return collate(items, tokenizer.pad_token_id)
    loader = DataLoader(train, batch_size=args.batch_size, shuffle=True, collate_fn=batch)
    val_loader = DataLoader(validation, batch_size=args.batch_size, collate_fn=batch)
    chosen = []
    counts = {}
    for row in test_rows:
        key = row.get("category", row["task"])
        if counts.get(key, 0) < (2 if row["task"] == "extract" else 8):
            chosen.append(row)
            counts[key] = counts.get(key, 0) + 1
    if args.evaluate_all:
        chosen = test_rows
    baseline_loss = loss_eval(model, val_loader)
    baseline = evaluate_generation(model, tokenizer, chosen)
    write(args.output / "baseline.json", {"validation_loss": baseline_loss, **baseline})
    print(json.dumps({"stage":"baseline", "loss":baseline_loss, "tasks":baseline["tasks"]}), flush=True)
    if not args.initial_adapter:
        model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules=["q_proj", "k_proj", "v_proj", "o_proj"], task_type="CAUSAL_LM", bias="none"))
    model.print_trainable_parameters()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant":False})
    model.config.use_cache = False
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=0.01)
    steps = math.ceil(len(loader)/args.accumulation) * args.epochs
    scheduler = get_cosine_schedule_with_warmup(optimizer, max(4, steps//20), steps)
    step = 0
    started = time.time()
    best = float("inf")
    epochs = []
    with (args.output / "training-log.jsonl").open("w") as log:
        for epoch in range(args.epochs):
            model.train()
            optimizer.zero_grad()
            rolling = 0
            for index, batch_data in enumerate(loader):
                # Correct the final partial accumulation group.
                divisor = min(args.accumulation, len(loader) - index // args.accumulation * args.accumulation)
                loss = model(**{k:v.to(model.device) for k,v in batch_data.items()}).loss
                if not torch.isfinite(loss): raise RuntimeError("Nonfinite loss")
                (loss/divisor).backward()
                rolling += loss.item()
                if (index+1) % args.accumulation == 0 or index+1 == len(loader):
                    torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                    optimizer.step(); scheduler.step(); optimizer.zero_grad()
                    step += 1
                    event = {"epoch":epoch+1, "step":step, "steps":steps, "loss":rolling/divisor, "elapsed_seconds":round(time.time()-started,1)}
                    log.write(json.dumps(event)+"\n"); log.flush()
                    if step % 5 == 0 or step == 1: print(json.dumps(event), flush=True)
                    rolling = 0
            validation_loss = loss_eval(model, val_loader)
            epochs.append({"epoch":epoch+1, "validation_loss":validation_loss})
            print(json.dumps(epochs[-1]), flush=True)
            if validation_loss < best:
                best = validation_loss
                model.save_pretrained(args.output)
                tokenizer.save_pretrained(args.output)
    # Evaluate the selected checkpoint, not an arbitrary last epoch.
    model.load_adapter(args.output, adapter_name="selected")
    model.set_adapter("selected")
    model.gradient_checkpointing_disable()
    model.config.use_cache = True
    final = evaluate_generation(model, tokenizer, chosen)
    write(args.output / "evaluation.json", {"baseline_validation_loss":baseline_loss,"best_validation_loss":best, "epochs":epochs, **final})
    passed = best < baseline_loss and final["tasks"]["extract"]["accuracy"] >= .90 and final["tasks"]["clarify"]["accuracy"] >= .875 and final["tasks"]["summary"]["accuracy"] >= .875
    if 'bank_intent' in final['tasks']:
        # Preserve all already-correct fixed accounting holdouts while improving banking.
        passed = passed and final['tasks']['bank_intent']['accuracy'] >= .95
        passed = passed and all(final['tasks'][task]['correct'] >= baseline['tasks'][task]['correct'] for task in ('extract','clarify','summary'))
    if 'profile_reason' in final['tasks']:
        passed = passed and final['tasks']['profile_reason']['accuracy'] >= .95
    metadata = {"base_model":str(args.base.resolve()), "dataset":str(args.data.resolve()), "dataset_manifest_sha256":hashlib.sha256((args.data/"manifest.json").read_bytes()).hexdigest(),
                "train_rows":len(train), "validation_rows":len(validation), "test_rows_evaluated":len(chosen), "steps":step, "epochs":args.epochs,
                "elapsed_seconds":round(time.time()-started,1), "best_validation_loss":best, "baseline_validation_loss":baseline_loss,
                "passed_release_gate":passed, "evaluation":final["tasks"], "gpu":torch.cuda.get_device_name(0), "torch":torch.__version__}
    metadata.update(initial_adapter=str(args.initial_adapter) if args.initial_adapter else None,
                    learning_rate=args.learning_rate, full_test_passed=bool(passed and args.evaluate_all),
                    full_test_evaluation=final['tasks'] if args.evaluate_all else None)
    write(args.output / "training-metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
    if not passed: raise SystemExit("Adapter saved but release gate failed; do not deploy automatically")


if __name__ == "__main__": main()
