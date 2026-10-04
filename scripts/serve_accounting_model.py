"""One local GPU model shared by the existing two FastAPI services."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading
import time

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("ACCOUNTING_BASE_MODEL", ROOT / "Qwen3-4B"))
ADAPTER = Path(os.environ.get("ACCOUNTING_ADAPTER", ROOT / "artifacts/accounting-v2/adapter"))
NAME = os.getenv('ACCOUNTING_MODEL_NAME', 'qingcai-qwen3-4b-accounting-v2')
model = tokenizer = None
lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    global model, tokenizer
    metadata = json.loads((ADAPTER / "training-metadata.json").read_text())
    if metadata.get("passed_release_gate") is not True or metadata.get("full_test_passed") is not True:
        raise RuntimeError("Adapter has not passed evaluation; refusing to serve it")
    record_path=os.getenv('ACCOUNTING_RELEASE_RECORD')
    if record_path:
        from scripts.model_release import verify_record
        record=verify_record(json.loads(Path(record_path).read_text()))
        if (record['version']!=NAME or Path(record['adapter']).resolve()!=ADAPTER.resolve()
                or Path(record['base']).resolve()!=BASE.resolve()):
            raise RuntimeError('Model release record does not match serving configuration')
    torch.set_num_threads(8)
    tokenizer = AutoTokenizer.from_pretrained(BASE, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16, device_map={"":0}, attn_implementation="sdpa", local_files_only=True)
    # Keep the evaluated adapter active. BF16 merging can round small LoRA
    # deltas differently; do not silently change the evaluated model at release.
    model = PeftModel.from_pretrained(base, ADAPTER, local_files_only=True).eval()
    yield


app = FastAPI(title="Qingcai fine-tuned local model", lifespan=lifespan)


class Message(BaseModel):
    role: str
    content: str = Field(max_length=60000)


class Completion(BaseModel):
    model: str = NAME
    messages: list[Message] = Field(min_length=1, max_length=32)
    max_tokens: int = Field(default=1024, ge=1, le=4096)
    temperature: float = Field(default=0.3, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    stream: bool = False


@app.get("/health")
def health():
    return {"status":"ok", "loaded":model is not None, "model":NAME, "fine_tuned":True}


@app.get("/v1/models")
def models(): return {"object":"list", "data":[{"id":NAME,"object":"model","owned_by":"local"}]}


@app.post("/v1/chat/completions")
def completion(req: Completion):
    if req.model != NAME: raise HTTPException(404, "Unknown model")
    if req.stream: raise HTTPException(400, "Streaming is not supported")
    if any(m.role not in {"system","user","assistant"} for m in req.messages): raise HTTPException(422, "Invalid role")
    prompt = tokenizer.apply_chat_template([m.model_dump() for m in req.messages], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    if inputs.input_ids.shape[1] + req.max_tokens > 16384:
        raise HTTPException(413, "Conversation too long; please start a new chat")
    if not lock.acquire(timeout=30): raise HTTPException(503, "Model busy; retry shortly")
    try:
        kwargs = {"do_sample": req.temperature > 0}
        if req.temperature > 0: kwargs.update(temperature=req.temperature, top_p=req.top_p)
        with torch.inference_mode():
            result = model.generate(**inputs, max_new_tokens=req.max_tokens, pad_token_id=tokenizer.eos_token_id, **kwargs)
        ids = result[0, inputs.input_ids.shape[1]:]
        text = tokenizer.decode(ids, skip_special_tokens=True).strip()
        return {"id":f"local-{time.time_ns()}","object":"chat.completion","model":NAME,
                "choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":"stop" if ids[-1].item()==tokenizer.eos_token_id else "length"}],
                "usage":{"prompt_tokens":inputs.input_ids.shape[1],"completion_tokens":len(ids),"total_tokens":inputs.input_ids.shape[1]+len(ids)}}
    finally:
        lock.release()
