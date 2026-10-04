from __future__ import annotations
import csv
import json
import os
import re
import uvicorn
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Deque, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from llm_runtime import model_status, api_chat, provider
from research_quality import valid_quote, quote_quality, stock_facts, grounded_report

# Configuration
MODEL_DIR = os.environ.get("QWEN_MODEL_DIR")
DATA_DIR = Path(os.environ.get("AI_BOOKKEEPER_DATA_DIR", Path(__file__).resolve().parent / "data"))
PORTFOLIO_PATH = DATA_DIR / "portfolio_snapshot.json"
A10_KLINE_DIR = DATA_DIR / "Financial" / "A10"
REPORTS_DIR = DATA_DIR / "Financial" / "reports"

# Stock metadata
STOCK_METADATA = {
    "600036": "招商银行",
    "600519": "贵州茅台", 
    "600900": "长江电力",
    "601088": "中国神华",
    "601138": "工业富联",
    "601288": "农业银行",
    "601398": "工商银行",
    "601628": "中国人寿",
    "601857": "中国石油",
    "601988": "中国银行"
}

# Models
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    max_new_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.9
    system_prompt: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str


def _load_portfolio_snapshot() -> dict:
    if not PORTFOLIO_PATH.exists():
        raise HTTPException(status_code=404, detail="Portfolio data not found")
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _format_portfolio_payload(raw: dict) -> dict:
    total_balance = float(raw.get("total_balance", 0))
    day_change_pct = float(raw.get("day_change_pct", 0)) * 100
    ytd_return_pct = float(raw.get("ytd_return_pct", 0)) * 100
    last_update = raw.get("last_update") or raw.get("as_of")
    if last_update and isinstance(last_update, str) and len(last_update) == 10:
        last_update = f"{last_update}T00:00:00Z"
    summary = {
        "total_balance": total_balance,
        "day_change_pct": day_change_pct,
        "ytd_return_pct": ytd_return_pct,
        "last_update": last_update,
        "base_currency": raw.get("base_currency", "CNY"),
    }

    holdings = []
    for item in raw.get("holdings", []):
        weight_pct = float(item.get("weight_pct", 0))
        weight = weight_pct / 100
        market_value = total_balance * weight
        holdings.append({
            "code": item.get("symbol", ""),
            "name": item.get("name", ""),
            "weight": weight,
            "market_value": round(market_value, 2),
            "current_price": item.get("last_price"),
            "pnl_pct": item.get("pnl_pct", 0),
            "concept": item.get("theme", "--"),
        })
    holdings.sort(key=lambda h: h.get("weight", 0), reverse=True)

    allocation = {
        "by_sector": {
            entry.get("sector", "--"): entry.get("weight_pct", 0)
            for entry in raw.get("sector_allocation", [])
        }
    }

    return {
        "summary": summary,
        "holdings": holdings,
        "allocation": allocation,
        "nav_history": raw.get("nav_history", []),
        "insights": raw.get("insights", {}),
    }


def _read_kline_records(csv_path: Path, limit: int) -> List[Dict[str, str]]:
    if limit <= 0:
        return []

    buffer: Deque[Dict[str, str]] = deque(maxlen=limit)
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            normalized = {(key.strip() or "date"): row.get(key) for key in reader.fieldnames}
            if valid_quote(normalized):
                buffer.append(normalized)
    return list(buffer)

# App Setup
app = FastAPI(title="Investment Agent Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Model Variables
tokenizer = None
model = None
device = "not_loaded"

def load_model_if_needed():
    global tokenizer, model, torch, device
    if tokenizer is not None and model is not None:
        return
    
    if not MODEL_DIR:
        raise HTTPException(status_code=503, detail="尚未配置本地模型")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="请安装 requirements-local.txt") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"Loading model from {MODEL_DIR}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        trust_remote_code=True,
        device_map="auto" if torch.cuda.is_available() else None,
    )
    if not torch.cuda.is_available():
        model.to(device)
    model.eval()
    print("Model loaded.")

def _sanitize_reply(text: str) -> str:
    if not text:
        return ""
    # Remove explicit <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)

    block_prefixes = (
        "思考",
        "推理",
        "reasoning",
        "思考过程",
        "推理过程",
        "分析过程",
    )

    blocks = re.split(r"\n\s*\n", text)
    filtered_blocks: List[str] = []
    for block in blocks:
        stripped_block = block.strip()
        if not stripped_block:
            continue
        first_line = stripped_block.splitlines()[0].strip()
        lower_first = first_line.lower()
        if any(
            first_line.startswith(prefix) or lower_first.startswith(prefix)
            for prefix in block_prefixes
        ):
            continue
        filtered_blocks.append(stripped_block)

    cleaned = "\n\n".join(filtered_blocks).strip()

    # Ensure key headings start on a new line to avoid run-on sentences
    headings = ("基本面分析", "社会舆情", "风险提示", "投资意见")
    for heading in headings:
        cleaned = re.sub(rf"(?<!\n){heading}", f"\n{heading}", cleaned)

    cleaned = re.sub(r"\*\*\s+", "**", cleaned)
    cleaned = re.sub(r"(\*\*[^*]+\*\*)(?!\n)", r"\1\n", cleaned)

    return cleaned.strip()

@app.get("/health")
def health():
    return {"status": "ok", "service": "investment-agent", "llm": model_status(MODEL_DIR)}

@app.get("/trader/portfolio")
def get_portfolio():
    raw = _load_portfolio_snapshot()
    return _format_portfolio_payload(raw)

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    system_prompt = req.system_prompt or "You are a helpful financial investment assistant."
    system_prompt += (f"\n当前日期为 {datetime.now(ZoneInfo('Asia/Shanghai')).date()}（Asia/Shanghai）。"
                      "只依据提供的数据回答，注明历史数据日期，不能将历史快照说成实时行情；不得臆测另一个今天的日期。")
    
    messages = [{"role": "system", "content": system_prompt}]
    for m in req.messages:
        messages.append({"role": m.role, "content": m.content})
        
    if provider(MODEL_DIR) != "local":
        return ChatResponse(reply=_sanitize_reply(api_chat(messages, req.max_new_tokens, req.temperature, req.top_p)))
    load_model_if_needed()

    input_ids = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt"
    ).to(model.device)
    
    with torch.no_grad():
        generated_ids = model.generate(
            input_ids=input_ids,
            max_new_tokens=req.max_new_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            do_sample=req.temperature > 0
        )
        
    new_tokens = generated_ids[0, input_ids.shape[-1]:]
    reply = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return ChatResponse(reply=_sanitize_reply(reply))

def _grounded(sections):
    def generate(catalog):
        try:
            return api_chat([{'role': 'system', 'content': '从提供的事实索引中为每章选择1至3个ID。只返回JSON：章名到ID数组。不得写新事实。'},
                             {'role': 'user', 'content': json.dumps(catalog, ensure_ascii=False)}], 384, 0, 1)
        except HTTPException as exc:
            raise RuntimeError('Model unavailable') from exc
    return grounded_report(sections, generate)


@app.post("/trader/analyst/report")
def generate_analyst_report():
    p = _format_portfolio_payload(_load_portfolio_snapshot())
    s = p['summary']
    return _grounded({
        '历史组合': [f"快照日期：{s['last_update']}；组合市值 {s['total_balance']:,.2f} 元。",
                     f"快照记录的当日变动 {s['day_change_pct']:+.2f}%，年初至今变动 {s['ytd_return_pct']:+.2f}%。"],
        '主要持仓': [f"{h['name']}：配置占比 {h['weight']*100:.2f}%，快照盈亏 {h['pnl_pct']:+.2f}%。" for h in p['holdings'][:3]] or ['无持仓记录。'],
        '数据边界': ['历史组合不属于当前模拟账户，也不计入账本可用资金。',
                    '缺少实时价格和完整绩效基准，不能据此推断当前收益或未来表现。']})


@app.get("/trader/stock/{stock_code}/quality")
def get_stock_quality(stock_code: str):
    if stock_code not in STOCK_METADATA:
        raise HTTPException(status_code=404, detail="此股票尚未接入历史行情")
    path = A10_KLINE_DIR / f"{stock_code}.csv"
    rows = []
    if path.exists():
        with path.open(encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = [{(k.strip() or 'date'): v for k, v in row.items()} for row in reader]
    return quote_quality(rows)


@app.get("/trader/watchlist")
def get_watchlist():
    """Return A10 stock watchlist with metadata"""
    return [
        {"code": code, "name": name, "quality": get_stock_quality(code)} 
        for code, name in STOCK_METADATA.items()
    ]


@app.get("/trader/stock/{stock_code}/kline")
def get_stock_kline(stock_code: str, days: int = Query(10, ge=1, le=365)):
    """Get recent K-line data for a stock"""
    if stock_code not in STOCK_METADATA:
        raise HTTPException(status_code=404, detail="此股票尚未接入历史行情")
    csv_path = A10_KLINE_DIR / f"{stock_code}.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail=f"K-line data not found for {stock_code}")

    records = _read_kline_records(csv_path, max(days, 1))
    return records


@app.get("/trader/stock/{stock_code}/news")
def get_stock_news(stock_code: str):
    """Get news and research reports for a stock"""
    if stock_code not in STOCK_METADATA:
        raise HTTPException(status_code=404, detail="此股票尚未接入资讯")
    report_path = REPORTS_DIR / f"{stock_code}_news_report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"News/report data not found for {stock_code}")
    
    with open(report_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Extract recent news and reports
    news_items = data.get("news", {}).get("data", [])[:5]  # Top 5 news
    reports = data.get("research_reports", {}).get("data", [])[:3]  # Top 3 reports
    
    return {
        "news": news_items,
        "reports": reports
    }


@app.post("/trader/stock/{stock_code}/daily_report")
def generate_stock_daily_report(stock_code: str):
    """Generate daily analysis report for a specific stock"""
    
    if stock_code not in STOCK_METADATA:
        raise HTTPException(status_code=404, detail="此股票尚未接入研究数据")

    # Load K-line data
    try:
        kline_data = get_stock_kline(stock_code, days=10)
    except HTTPException:
        kline_data = []
    
    # Load news and reports
    try:
        news_data = get_stock_news(stock_code)
    except HTTPException:
        news_data = {"news": [], "reports": []}
    
    quality = get_stock_quality(stock_code)
    result = _grounded(stock_facts(STOCK_METADATA[stock_code], kline_data,
                                  news_data['news'], news_data['reports'], quality))
    result['quality'] = quality
    return result


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8020"))
    uvicorn.run("investment_app:app", host="0.0.0.0", port=port, reload=False)

