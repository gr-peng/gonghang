"""Evaluate every held-out example with the saved adapter before deployment."""
import argparse
import json
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from train_accounting_lora import evaluate_generation, read, write
from model_validation import release_gate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',type=Path,required=True)
    args=parser.parse_args()
    adapter=args.artifact/'adapter'
    meta=json.loads((adapter/'training-metadata.json').read_text())
    torch.set_num_threads(8)
    tokenizer=AutoTokenizer.from_pretrained(meta['base_model'],local_files_only=True)
    tokenizer.pad_token=tokenizer.eos_token
    base=AutoModelForCausalLM.from_pretrained(meta['base_model'],dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa',local_files_only=True)
    model=PeftModel.from_pretrained(base,adapter,local_files_only=True).eval()
    report=evaluate_generation(model,tokenizer,read(args.artifact/'test.jsonl'))
    # Preserve the original candidate decision: a failed training run cannot
    # become approved merely by invoking this evaluator again.
    baseline=json.loads((adapter/'baseline.json').read_text())['tasks']
    passed=release_gate({**meta,'full_test_passed':True},report['tasks'],baseline)
    report['passed']=passed
    write(adapter/'full-test-evaluation.json',report)
    meta['full_test_evaluation']=report['tasks']
    meta['full_test_passed']=passed
    meta['passed_release_gate']=passed
    write(adapter/'training-metadata.json',meta)
    print(json.dumps({'passed':passed,'tasks':report['tasks']},ensure_ascii=False,indent=2))
    if not passed: raise SystemExit('Full held-out evaluation failed')


if __name__=='__main__':main()
