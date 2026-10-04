from __future__ import annotations
import json
import hashlib
import os
import sqlite3
import statistics
import uvicorn
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from llm_runtime import model_status, api_chat, provider
import math

from prompts import DEFAULT_SYSTEM_PROMPT
from accounting_schema import EXPENSE_CATEGORIES, INCOME_CATEGORIES, extraction_prompt, explicit_payment, multiple_bill_reason


MODEL_DIR = os.environ.get("QWEN_MODEL_DIR")
DATA_DIR = Path(
    os.environ.get(
        "AI_BOOKKEEPER_DATA_DIR",
        Path(__file__).resolve().parent / "data",
    )
)
DB_PATH = DATA_DIR / "bills.db"
JSONL_PATH = DATA_DIR / "synthetic_bank_bills.jsonl"

DISPLAY_CATEGORIES = EXPENSE_CATEGORIES
CATEGORY_ALIASES = {
    "餐": "餐饮",
    "饭": "餐饮",
    "外卖": "餐饮",
    "餐饮": "餐饮",
    "出行": "出行",
    "交通": "出行",
    "打车": "出行",
    "购物": "购物",
    "数码": "购物",
    "生活缴费": "生活缴费",
    "缴费": "生活缴费",
    "话费": "生活缴费",
    "水电": "生活缴费",
    "娱乐": "娱乐",
    "影视": "娱乐",
    "游戏": "娱乐",
}
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MONTH_LABELS = [f"{i}月" for i in range(1, 13)]

# 默认是否启用全库检索（RAG）
DEFAULT_RETRIEVAL = os.environ.get("AI_BOOKKEEPER_DEFAULT_RETRIEVAL", "true").lower() in {"1", "true", "yes", "on"}
GEN_DEFAULT_MAX_NEW_TOKENS = int(os.environ.get("AI_BOOKKEEPER_DEFAULT_MAX_NEW_TOKENS", "1024"))
GEN_MAX_NEW_TOKENS_CAP = int(os.environ.get("AI_BOOKKEEPER_MAX_NEW_TOKENS_CAP", "4096"))


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class LedgerRange(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode='after')
    def ordered(self):
        if self.end_date < self.start_date:
            raise ValueError('结束日期不能早于开始日期')
        return self


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    max_new_tokens: int = 3000
    min_new_tokens: Optional[int] = None
    temperature: float = 0.7
    top_p: float = 0.9
    repetition_penalty: float = 1.05
    system_prompt: Optional[str] = None
    include_bill_context: bool = True
    # RAG：是否构建全库检索上下文（聚合 + 相关片段），None 表示走默认开关
    retrieval: Optional[bool] = None
    # RAG：限制聚合时间窗口的月数（例如 12 表示过去 12 个月；None 表示不限制）
    retrieval_months: Optional[int] = 12
    ledger_range: Optional[LedgerRange] = None


class ChatResponse(BaseModel):
    reply: str


class BillBase(BaseModel):
    event_date: date = Field(..., description="发生日期")
    category: str = Field(..., min_length=1, max_length=64)
    type: str = Field("expense", description="expense 或 income")
    amount: float = Field(..., gt=0, allow_inf_nan=False, description="正数金额")
    currency: str = Field("CNY", min_length=1, max_length=8)
    description: str = Field(..., min_length=1, max_length=512)
    source: Optional[str] = Field(None, description="数据来源，如OCR/手动")
    metadata: Optional[dict] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if value.strip().upper() in {"CNY", "RMB", "元", "人民币"}:
            return "CNY"
        raise ValueError("当前账本仅汇总人民币，请先确认 CNY 金额")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        value = value.lower()
        if value not in {"expense", "income"}:
            raise ValueError("type 必须为 expense 或 income")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("amount 必须为正数")
        rounded = round(float(value), 2)
        if rounded <= 0:
            raise ValueError("金额至少为 0.01")
        return rounded


class BillCreate(BillBase):
    @model_validator(mode="after")
    def category_matches_type(self):
        # Older clients used a shared "其他" category for both cash-flow directions.
        if self.category == "其他" and self.type == "income":
            self.category = "其他收入"
        if (self.category in INCOME_CATEGORIES and self.type != "income") or (self.category in EXPENSE_CATEGORIES and self.type != "expense"):
            raise ValueError("分类与收入/支出类型不一致")
        return self


class Bill(BillBase):
    id: int
    created_at: datetime
    version: str = ''

    model_config = {
        "json_encoders": {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat(),
        }
    }


class BillUpdate(BillCreate):
    expected_version: str = Field(min_length=64, max_length=64)
    payment_method: str = Field(default='', max_length=40)


app = FastAPI(title="Local Qwen3-4B Chat Server")

DATA_DIR.mkdir(parents=True, exist_ok=True)
_db_lock = Lock()
_db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_db_conn.row_factory = sqlite3.Row

with _db_lock:
    _db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'expense',
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            description TEXT NOT NULL,
            source TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    _db_conn.execute('CREATE TABLE IF NOT EXISTS bill_revisions (id INTEGER PRIMARY KEY, bill_id INTEGER NOT NULL, prior TEXT NOT NULL, changed_at TEXT NOT NULL)')
    _db_conn.commit()

# 从 JSONL 导入账单至 SQLite（仅当数据库为空时触发）
def _import_jsonl_if_needed() -> None:
    if os.getenv("AI_BOOKKEEPER_IMPORT_JSONL", "true").lower() in {"0", "false", "no", "off"}:
        return
    p = Path(os.environ.get("AI_BOOKKEEPER_JSONL_PATH") or JSONL_PATH)
    if not p.exists():
        return
    with _db_lock:
        _db_conn.execute("CREATE TABLE IF NOT EXISTS data_imports (name TEXT PRIMARY KEY, source_path TEXT NOT NULL, record_count INTEGER NOT NULL, imported_at TEXT NOT NULL)")
        _db_conn.commit()
        if _db_conn.execute("SELECT 1 FROM data_imports WHERE name = 'initial_jsonl'").fetchone():
            return
        count = _db_conn.execute("SELECT COUNT(*) AS c FROM bills").fetchone()["c"]
    if count and count > 0:
        return
    inserted = 0
    now = datetime.utcnow().isoformat()
    with p.open("r", encoding="utf-8") as f, _db_lock:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            # 兼容 synthetic 数据格式：{"messages":[{"role":"assistant","content":"{...}"}]}
            payload = None
            if isinstance(obj, dict) and "messages" in obj:
                for m in obj.get("messages", []):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        try:
                            payload = json.loads(m.get("content", "{}"))
                        except Exception:
                            payload = None
                        break
            # 如果已经是结构化对象，也尝试直接使用
            if payload is None and isinstance(obj, dict):
                payload = obj
            if not isinstance(payload, dict):
                continue
            # 兼容中文键
            try:
                event_date = payload.get("日期") or payload.get("event_date")
                category = payload.get("类别") or payload.get("category") or "未分类"
                amount = payload.get("金额") or payload.get("amount")
                description = payload.get("描述") or payload.get("description") or ""
                merchant = payload.get("商户") or payload.get("merchant")
                payment = payload.get("支付方式") or payload.get("payment_method")
                if not event_date or amount is None:
                    continue
                amount = float(amount)
                bill_type = str(payload.get("type", "expense")).lower()
                currency = payload.get("currency") or "CNY"
                imported = BillCreate(
                    event_date=event_date, category=category, type=bill_type,
                    amount=amount, currency=currency,
                    description=description or (f"{merchant or ''}消费").strip(),
                )
                metadata = {
                    "merchant": merchant,
                    "payment_method": payment,
                    "raw": payload,
                }
                metadata_json = json.dumps(metadata, ensure_ascii=False)
                _db_conn.execute(
                    """
                    INSERT INTO bills
                    (event_date, category, type, amount, currency, description, source, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        imported.event_date.isoformat(),
                        imported.category,
                        imported.type,
                        imported.amount,
                        imported.currency,
                        imported.description,
                        "jsonl",
                        metadata_json,
                        now,
                    ),
                )
                inserted += 1
            except Exception:
                # 单条失败忽略，继续导入其它行
                continue
        if inserted:
            _db_conn.execute(
                "INSERT INTO data_imports (name, source_path, record_count, imported_at) VALUES ('initial_jsonl', ?, ?, ?)",
                (str(p.resolve()), inserted, now),
            )
        _db_conn.commit()
    # 可在启动日志上体现
    print(f"[bootstrap] Imported {inserted} bills from JSONL: {p}")


_import_jsonl_if_needed()


def _normalize_category(raw: Optional[str]) -> str:
    if not raw:
        return "其他"
    text = str(raw).strip()
    if text in EXPENSE_CATEGORIES + INCOME_CATEGORIES:
        return text
    for key, value in CATEGORY_ALIASES.items():
        if key in text:
            return value
    return text or "其他"


def _extract_payload_from_jsonl(line: str) -> Optional[dict]:
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if isinstance(obj, dict) and "messages" in obj:
        for message in obj.get("messages", []):
            if isinstance(message, dict) and message.get("role") == "assistant":
                try:
                    return json.loads(message.get("content", "{}"))
                except Exception:
                    return None
    if isinstance(obj, dict):
        return obj
    return None


def _load_synthetic_records() -> List[dict]:
    records: List[dict] = []
    if not JSONL_PATH.exists():
        return records
    with JSONL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = _extract_payload_from_jsonl(line)
            if not isinstance(payload, dict):
                continue
            date_str = payload.get("日期") or payload.get("event_date")
            amount = payload.get("金额") or payload.get("amount")
            type_str = str(payload.get("type", "expense")).lower()
            category = _normalize_category(payload.get("类别") or payload.get("category"))
            if not date_str or amount is None:
                continue
            try:
                event_date = datetime.fromisoformat(date_str).date()
            except ValueError:
                continue
            try:
                amount_value = float(amount)
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "event_date": event_date,
                    "amount": amount_value,
                    "type": type_str,
                    "category": category,
                }
            )
    return records


_RAW_SYNTHETIC_RECORDS = _load_synthetic_records()
REPORT_REFERENCE_DATE = max((r["event_date"] for r in _RAW_SYNTHETIC_RECORDS), default=date.today())
SYNTHETIC_RECORDS = [
    r
    for r in _RAW_SYNTHETIC_RECORDS
    if REPORT_REFERENCE_DATE - timedelta(days=366) <= r["event_date"] <= REPORT_REFERENCE_DATE
]


def _filter_records(records: List[dict], start: date, end: date) -> List[dict]:
    return [r for r in records if start <= r["event_date"] <= end]


def _sum_income_expense(records: List[dict]) -> tuple[float, float]:
    income = sum(r["amount"] for r in records if r["type"] == "income")
    expense = sum(r["amount"] for r in records if r["type"] == "expense")
    return round(income, 2), round(expense, 2)


def _category_totals(records: List[dict]) -> dict[str, float]:
    totals = {cat: 0.0 for cat in DISPLAY_CATEGORIES}
    for r in records:
        if r["type"] != "expense":
            continue
        totals[r["category"]] = totals.get(r["category"], 0.0) + r["amount"]
    return {k: round(v, 2) for k, v in totals.items()}


def _build_category_comparison(curr_records: List[dict], prev_records: List[dict]) -> List[dict]:
    current = _category_totals(curr_records)
    previous = _category_totals(prev_records)
    items = []
    for cat in dict.fromkeys([*DISPLAY_CATEGORIES, *current, *previous]):
        items.append(
            {
                "name": cat,
                "current": round(current.get(cat, 0.0), 2),
                "previous": round(previous.get(cat, 0.0), 2),
            }
        )
    return items


def _build_pie_data(curr_records: List[dict]) -> List[dict]:
    totals = _category_totals(curr_records)
    data = []
    for cat, value in totals.items():
        if value > 0:
            data.append({"name": cat, "value": value})
    return data


def _build_net_summary(curr_records: List[dict], prev_records: List[dict]) -> dict:
    curr_income, curr_expense = _sum_income_expense(curr_records)
    prev_income, prev_expense = _sum_income_expense(prev_records)
    current = round(curr_income - curr_expense, 2)
    previous = round(prev_income - prev_expense, 2)
    return {
        "current": current,
        "previous": previous,
        "difference": round(current - previous, 2),
    }


def _build_summary(records: List[dict]) -> dict:
    income, expense = _sum_income_expense(records)
    return {"income_total": income, "expense_total": expense}


def _format_range(start: date, end: date) -> str:
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _shift_month(base: date, months: int) -> date:
    month = base.month - 1 + months
    year = base.year + month // 12
    month = month % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _build_week_report(records: List[dict], ref_date: date) -> dict:
    current_start = ref_date - timedelta(days=ref_date.weekday())
    current_end = current_start + timedelta(days=6)
    previous_start = current_start - timedelta(days=7)
    previous_end = current_start - timedelta(days=1)

    current_records = _filter_records(records, current_start, current_end)
    previous_records = _filter_records(records, previous_start, previous_end)

    income_series = [0.0] * 7
    expense_series = [0.0] * 7
    for r in current_records:
        idx = (r["event_date"] - current_start).days
        if 0 <= idx < 7:
            if r["type"] == "income":
                income_series[idx] += r["amount"]
            else:
                expense_series[idx] += r["amount"]

    return {
        "title": "本周",
        "range": _format_range(current_start, current_end),
        "bars": {
            "labels": WEEKDAY_LABELS,
            "income": [round(v, 2) for v in income_series],
            "expense": [round(v, 2) for v in expense_series],
            "unit": "天",
        },
        "net": _build_net_summary(current_records, previous_records),
        "categories": _build_category_comparison(current_records, previous_records),
        "pie": _build_pie_data(current_records),
        "summary": _build_summary(current_records),
    }


def _build_month_report(records: List[dict], ref_date: date) -> dict:
    month_start = ref_date.replace(day=1)
    next_month_start = _shift_month(month_start, 1)
    month_end = next_month_start - timedelta(days=1)
    previous_month_start = _shift_month(month_start, -1)
    previous_month_end = month_start - timedelta(days=1)

    current_records = _filter_records(records, month_start, month_end)
    previous_records = _filter_records(records, previous_month_start, previous_month_end)

    labels: List[str] = []
    income_series: List[float] = []
    expense_series: List[float] = []

    segment_start = month_start
    week_index = 1
    while segment_start <= month_end:
        segment_end = min(segment_start + timedelta(days=6), month_end)
        segment_records = _filter_records(records, segment_start, segment_end)
        income, expense = _sum_income_expense(segment_records)
        labels.append(f"第{week_index}周")
        income_series.append(income)
        expense_series.append(expense)
        week_index += 1
        segment_start = segment_end + timedelta(days=1)

    return {
        "title": "本月",
        "range": _format_range(month_start, month_end),
        "bars": {
            "labels": labels,
            "income": [round(v, 2) for v in income_series],
            "expense": [round(v, 2) for v in expense_series],
            "unit": "周",
        },
        "net": _build_net_summary(current_records, previous_records),
        "categories": _build_category_comparison(current_records, previous_records),
        "pie": _build_pie_data(current_records),
        "summary": _build_summary(current_records),
    }


def _build_year_report(records: List[dict], ref_date: date) -> dict:
    year_start = date(ref_date.year, 1, 1)
    next_year_start = date(ref_date.year + 1, 1, 1)
    year_end = next_year_start - timedelta(days=1)
    previous_year_start = date(ref_date.year - 1, 1, 1)
    previous_year_end = year_start - timedelta(days=1)

    current_records = _filter_records(records, year_start, year_end)
    previous_records = _filter_records(records, previous_year_start, previous_year_end)

    income_series = []
    expense_series = []
    for month in range(1, 13):
        month_start = date(ref_date.year, month, 1)
        month_end = _shift_month(month_start, 1) - timedelta(days=1)
        month_records = _filter_records(records, month_start, month_end)
        income, expense = _sum_income_expense(month_records)
        income_series.append(income)
        expense_series.append(expense)

    return {
        "title": "本年",
        "range": _format_range(year_start, year_end),
        "bars": {
            "labels": MONTH_LABELS,
            "income": [round(v, 2) for v in income_series],
            "expense": [round(v, 2) for v in expense_series],
            "unit": "月",
        },
        "net": _build_net_summary(current_records, previous_records),
        "categories": _build_category_comparison(current_records, previous_records),
        "pie": _build_pie_data(current_records),
        "summary": _build_summary(current_records),
    }


def _build_custom_report(records: List[dict], start: date, end: date) -> dict:
    if start > end:
        start, end = end, start
    span_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span_days - 1)

    current_records = _filter_records(records, start, end)
    previous_records = _filter_records(records, previous_start, previous_end)

    if span_days <= 31:
        unit = "天"
        labels = []
        income_series = []
        expense_series = []
        day = start
        while day <= end:
            day_records = _filter_records(records, day, day)
            income, expense = _sum_income_expense(day_records)
            labels.append(day.strftime("%m-%d"))
            income_series.append(income)
            expense_series.append(expense)
            day += timedelta(days=1)
    elif span_days <= 180:
        unit = "周"
        labels = []
        income_series = []
        expense_series = []
        segment_start = start
        index = 1
        while segment_start <= end:
            segment_end = min(segment_start + timedelta(days=6), end)
            segment_records = _filter_records(records, segment_start, segment_end)
            income, expense = _sum_income_expense(segment_records)
            labels.append(f"第{index}周")
            income_series.append(income)
            expense_series.append(expense)
            index += 1
            segment_start = segment_end + timedelta(days=1)
    else:
        unit = "月"
        labels = []
        income_series = []
        expense_series = []
        month_cursor = start.replace(day=1)
        while month_cursor <= end:
            month_end = _shift_month(month_cursor, 1) - timedelta(days=1)
            if month_end < start:
                month_cursor = _shift_month(month_cursor, 1)
                continue
            if month_cursor > end:
                break
            segment_start = max(month_cursor, start)
            segment_end = min(month_end, end)
            segment_records = _filter_records(records, segment_start, segment_end)
            income, expense = _sum_income_expense(segment_records)
            labels.append(month_cursor.strftime("%Y-%m"))
            income_series.append(income)
            expense_series.append(expense)
            month_cursor = _shift_month(month_cursor, 1)

    return {
        "title": "自定义区间",
        "range": _format_range(start, end),
        "bars": {
            "labels": labels,
            "income": [round(v, 2) for v in income_series],
            "expense": [round(v, 2) for v in expense_series],
            "unit": unit,
        },
        "net": _build_net_summary(current_records, previous_records),
        "categories": _build_category_comparison(current_records, previous_records),
        "pie": _build_pie_data(current_records),
        "summary": _build_summary(current_records),
    }


def _empty_report(unit: str) -> dict:
    return {
        "title": "",
        "range": "",
        "bars": {"labels": [], "income": [], "expense": [], "unit": unit},
        "net": {"current": 0.0, "previous": 0.0, "difference": 0.0},
        "categories": [
            {"name": cat, "current": 0.0, "previous": 0.0} for cat in DISPLAY_CATEGORIES
        ],
        "pie": [],
        "summary": {"income_total": 0.0, "expense_total": 0.0},
    }


def _scope_sql(*, cashflow_only=False) -> str:
    # One owner-selected scope is shared by the ledger, reports, RAG and profile.
    scope = finance_router.workspace().profile().ledger_scope
    clause = "1=1" if scope == 'demo' else "COALESCE(source,'') NOT LIKE 'synthetic%' AND COALESCE(source,'') != 'jsonl'"
    if cashflow_only:
        clause += " AND COALESCE(json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END,'$.movement'),'cashflow') NOT IN ('internal_transfer','principal')"
    return clause


def _ledger_reference_date() -> date:
    scope = _scope_sql()
    with _db_lock:
        latest = _db_conn.execute(f"SELECT MAX(date(event_date)) FROM bills WHERE {scope}").fetchone()[0]
    return date.fromisoformat(latest) if latest else date.today()


def build_report_payload() -> dict:
    reference = _ledger_reference_date()
    records = _fetch_db_records(date(reference.year - 1, 1, 1), date(reference.year, 12, 31))
    return {
        "week": _build_week_report(records, reference),
        "month": _build_month_report(records, reference),
        "year": _build_year_report(records, reference),
    }


def _fetch_db_records(start: date, end: date) -> List[dict]:
    scope = _scope_sql(cashflow_only=True)
    with _db_lock:
        rows = _db_conn.execute(
            f"""
            SELECT event_date, category, type, amount, description
            FROM bills
            WHERE date(event_date) BETWEEN date(?) AND date(?) AND {scope}
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    records: List[dict] = []
    for row in rows:
        try:
            event_date = datetime.fromisoformat(row["event_date"]).date()
        except ValueError:
            continue
        records.append(
            {
                "event_date": event_date,
                "category": _normalize_category(row["category"]),
                "type": row["type"],
                "amount": float(row["amount"]),
                "description": row["description"],
            }
        )
    return records


def _category_summary(records: List[dict]) -> dict:
    totals = defaultdict(float)
    for r in records:
        if r["type"] == "expense":
            totals[r["category"]] += r["amount"]
    return {k: round(v, 2) for k, v in totals.items()}


def _build_overview_context(ref_date: date) -> dict:
    current_end = ref_date
    current_start = current_end - timedelta(days=29)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=30)

    curr_records = _fetch_db_records(current_start, current_end)
    prev_records = _fetch_db_records(previous_start, previous_end)

    curr_income, curr_expense = _sum_income_expense(curr_records)
    prev_income, prev_expense = _sum_income_expense(prev_records)
    curr_categories = _category_summary(curr_records)
    prev_categories = _category_summary(prev_records)

    category_changes = []
    for cat in DISPLAY_CATEGORIES:
        curr_val = curr_categories.get(cat, 0.0)
        prev_val = prev_categories.get(cat, 0.0)
        if curr_val == 0 and prev_val == 0:
            continue
        change_pct = 0.0
        if prev_val > 0:
            change_pct = (curr_val - prev_val) / prev_val
        elif curr_val > 0:
            change_pct = 1.0
        category_changes.append(
            {
                "category": cat,
                "current": curr_val,
                "previous": prev_val,
                "change_pct": round(change_pct, 4),
            }
        )
    category_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    anomalies = sorted(
        [r for r in curr_records if r["type"] == "expense"],
        key=lambda x: x["amount"],
        reverse=True,
    )[:5]
    anomaly_payload = [
        {
            "date": r["event_date"].isoformat(),
            "category": r["category"],
            "amount": round(r["amount"], 2),
            "description": r["description"],
        }
        for r in anomalies
    ]

    return {
        "period": {"start": current_start.isoformat(), "end": current_end.isoformat()},
        "summary": {
            "income": round(curr_income, 2),
            "expense": round(curr_expense, 2),
            "net": round(curr_income - curr_expense, 2),
        },
        "previous_summary": {
            "income": round(prev_income, 2),
            "expense": round(prev_expense, 2),
            "net": round(prev_income - prev_expense, 2),
        },
        "category_changes": category_changes[:6],
        "largest_transactions": anomaly_payload,
    }


def _build_behavior_context(ref_date: date) -> dict:
    end = ref_date
    start = end - timedelta(days=179)
    records = _fetch_db_records(start, end)
    monthly_totals = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    category_monthly = defaultdict(lambda: defaultdict(float))

    for r in records:
        month_key = r["event_date"].strftime("%Y-%m")
        if r["type"] == "income":
            monthly_totals[month_key]["income"] += r["amount"]
        else:
            monthly_totals[month_key]["expense"] += r["amount"]
            category_monthly[r["category"]][month_key] += r["amount"]

    months_sorted = sorted(monthly_totals.keys())
    patterns = []
    risk_flags = []
    for cat in DISPLAY_CATEGORIES:
        series = [category_monthly[cat].get(m, 0.0) for m in months_sorted]
        if not any(series):
            continue
        avg = statistics.mean(series)
        std = statistics.pstdev(series) if len(series) > 1 else 0.0
        trend = "stable"
        if len(series) >= 2:
            if series[-1] > (series[0] * 1.15):
                trend = "up"
            elif series[-1] < (series[0] * 0.85):
                trend = "down"
        patterns.append(
            {
                "category": cat,
                "average": round(avg, 2),
                "volatility": round(std, 2),
                "trend": trend,
            }
        )
        if avg > 0 and std > avg * 0.5:
            risk_flags.append(
                {
                    "category": cat,
                    "std": round(std, 2),
                    "avg": round(avg, 2),
                    "message": "波动显著，建议监控",
                }
            )

    behavior_monthly = [
        {
            "month": month,
            "income": round(monthly_totals[month]["income"], 2),
            "expense": round(monthly_totals[month]["expense"], 2),
        }
        for month in months_sorted
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "monthly_totals": behavior_monthly,
        "category_patterns": patterns,
        "risk_flags": risk_flags[:5],
    }


def _build_advice_center_context(ref_date: date) -> dict:
    end = ref_date
    start = end - timedelta(days=89)
    records = _fetch_db_records(start, end)
    income, expense = _sum_income_expense(records)
    net = income - expense
    savings_rate = (net / income) if income > 0 else 0.0

    category_totals = _category_summary(records)
    total_expense = sum(category_totals.values())
    category_share = []
    for cat, value in category_totals.items():
        share = (value / total_expense) if total_expense else 0
        category_share.append(
            {"category": cat, "amount": round(value, 2), "share": round(share, 4)}
        )
    category_share.sort(key=lambda x: x["share"], reverse=True)

    fixed_candidates = [
        cat
        for cat, value in category_totals.items()
        if value > 0 and value >= total_expense * 0.15
    ]
    pressure_points = [
        item["category"] for item in category_share if item["share"] > 0.3
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "income_total": round(income, 2),
        "expense_total": round(expense, 2),
        "net": round(net, 2),
        "savings_rate": round(savings_rate, 4),
        "category_share": category_share[:6],
        "fixed_candidates": fixed_candidates,
        "pressure_points": pressure_points,
    }


def build_advice_payload(reference_date: Optional[date] = None) -> dict:
    today = reference_date or _ledger_reference_date()
    return {
        "overview": _build_overview_context(today),
        "behavior": _build_behavior_context(today),
        "advice": _build_advice_center_context(today),
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


tokenizer: Optional[AutoTokenizer] = None
model: Optional[AutoModelForCausalLM] = None
device: str = "not_loaded"
_resolved_model_dir: Optional[Path] = None
_is_vl_model: bool = False
_processor = None


def _row_to_bill(row: sqlite3.Row) -> Bill:
    metadata = json.loads(row["metadata"]) if row["metadata"] else None
    return Bill(
        id=row["id"],
        event_date=datetime.fromisoformat(row["event_date"]).date(),
        category=row["category"],
        type=row["type"],
        amount=row["amount"],
        currency=row["currency"],
        description=row["description"],
        source=row["source"],
        metadata=metadata,
        created_at=datetime.fromisoformat(row["created_at"]),
        version=hashlib.sha256(json.dumps(dict(row), ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    )


def fetch_recent_bills(limit: int = 20) -> List[Bill]:
    scope = _scope_sql(cashflow_only=True)
    with _db_lock:
        cur = _db_conn.execute(
            f"""
            SELECT * FROM bills WHERE {scope}
            ORDER BY datetime(event_date) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [_row_to_bill(row) for row in rows]


def build_bill_context(limit: int = 20) -> str:
    bills = fetch_recent_bills(limit)
    if not bills:
        return ""
    lines = ["以下为系统已记录的最新账单（最多20条，时间倒序）："]
    for bill in bills:
        lines.append(
            f"- {bill.event_date.isoformat()} | {bill.category} | {bill.type} | {bill.amount:.2f}{bill.currency} | {bill.description}"
        )
    return "\n".join(lines)


def build_retrieval_context(month_window: Optional[int] = 12) -> str:
    """
    基于整个数据库构建检索上下文：
    - 全局总览（总收入/支出）
    - 按类别汇总（收入/支出）
    - 按月汇总（最近 N 个月，或全量）
    - Top 商户（支出 Top 10）
    """
    scope = _scope_sql(cashflow_only=True)
    with _db_lock:
        totals = _db_conn.execute(
            f"""
            SELECT 
                SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS total_income,
                SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS total_expense
            FROM bills WHERE {scope}
            """
        ).fetchone()
        by_category = _db_conn.execute(
            f"""
            SELECT category, type, SUM(amount) AS total
            FROM bills WHERE {scope}
            GROUP BY category, type
            ORDER BY total DESC
            """
        ).fetchall()
        # 按月汇总
        if month_window and month_window > 0:
            by_month = _db_conn.execute(
                f"""
                SELECT strftime('%Y-%m', date(event_date)) AS ym, type, SUM(amount) AS total
                FROM bills WHERE {scope}
                GROUP BY ym, type
                ORDER BY ym DESC
                """
            ).fetchall()
        else:
            by_month = _db_conn.execute(
                f"""
                SELECT strftime('%Y-%m', date(event_date)) AS ym, type, SUM(amount) AS total
                FROM bills WHERE {scope}
                GROUP BY ym, type
                ORDER BY ym DESC
                """
            ).fetchall()
        # Top 商户（从 metadata.raw/merchant 或 metadata.merchant 中提取）
        top_merchants = _db_conn.execute(
            f"""
            SELECT 
                COALESCE(
                    json_extract(metadata, '$.merchant'),
                    json_extract(metadata, '$.raw.商户'),
                    json_extract(metadata, '$.raw.merchant'),
                    '未知商户'
                ) AS merchant,
                SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS expense_total,
                COUNT(*) AS cnt
            FROM bills WHERE {scope}
            GROUP BY merchant
            ORDER BY expense_total DESC
            LIMIT 10
            """
        ).fetchall()

    lines = []
    lines.append("以下为基于全库的检索上下文：")
    lines.append(f"- 总收入：{(totals['total_income'] or 0):.2f}")
    lines.append(f"- 总支出：{(totals['total_expense'] or 0):.2f}")
    lines.append(f"- 净现金结余：{((totals['total_income'] or 0) - (totals['total_expense'] or 0)):.2f}")

    # 类别汇总
    lines.append("\n[按类别汇总]")
    # 合并为 dict[(category, type)] = total
    category_map = {}
    for row in by_category:
        category_map.setdefault(row["category"], {}).update({row["type"]: row["total"]})
    for cat, tmap in category_map.items():
        income = tmap.get("income", 0) or 0
        expense = tmap.get("expense", 0) or 0
        lines.append(f"- {cat} | 收入：{income:.2f} | 支出：{expense:.2f}")

    # 月度汇总（可截断最近 N 个月）
    lines.append("\n[按月汇总]")
    # 聚合为 dict[ym] = {"income": x, "expense": y}
    month_map = {}
    for row in by_month:
        ym = row["ym"]
        month_map.setdefault(ym, {}).update({row["type"]: row["total"]})
    # 只保留最近 month_window 个月（如果设置了）
    months_sorted = sorted(month_map.keys(), reverse=True)
    if month_window and month_window > 0:
        months_sorted = months_sorted[:month_window]
    for ym in months_sorted:
        income = month_map[ym].get("income", 0) or 0
        expense = month_map[ym].get("expense", 0) or 0
        lines.append(f"- {ym} | 收入：{income:.2f} | 支出：{expense:.2f} | 结余：{income-expense:.2f}")

    # Top 商户
    lines.append("\n[支出 Top 商户]")
    for row in top_merchants:
        lines.append(f"- {row['merchant']} | 支出：{(row['expense_total'] or 0):.2f} | 笔数：{row['cnt']}")

    return "\n".join(lines)


def _force_no_cot_instruction() -> str:
    return (
        "重要：不要输出思考过程、草稿或 <think> 内容；只输出清晰、可核对的最终答案。"
    )


def _sanitize_reply(text: str) -> str:
    if not text:
        return text
    # 去除明显的 <think> 开头段
    if text.lstrip().startswith("<think>"):
        # 尝试剪掉直到 </think>，若没有则剪掉到第一个空行
        start = text.find("<think>")
        end = text.find("</think>", start + 7)
        if end != -1:
            text = text[end + len("</think>") :].lstrip()
        else:
            # 没有闭合标签，剪到第一个双换行或整段
            sep = "\n\n"
            pos = text.find(sep, start + 7)
            text = text[pos + len(sep) :].lstrip() if pos != -1 else ""
    # 清理残余的 think 标签
    text = text.replace("<think>", "").replace("</think>", "")
    # 兜底：若清理后为空，返回简短答复提示
    return text.strip() or "（已根据数据库给出结果，未显示思考过程）"


def load_model_if_needed() -> None:
    global tokenizer, model, _resolved_model_dir, _is_vl_model, _processor, torch, device
    if tokenizer is not None and model is not None:
        return

    if not MODEL_DIR:
        raise HTTPException(status_code=503, detail="尚未配置本地模型。请设置 QWEN_MODEL_DIR，或在 .env 配置 API 模式。")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, AutoModelForVision2Seq, AutoProcessor
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="本地模型依赖未安装。请安装 requirements-local.txt。") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 解析模型目录：若根目录无 config.json 而存在子目录 model/config.json，则使用子目录
    base_dir = Path(MODEL_DIR)
    cfg_at_root = (base_dir / "config.json").exists()
    cfg_at_sub = (base_dir / "model" / "config.json").exists()
    _resolved_model_dir = base_dir / "model" if (not cfg_at_root and cfg_at_sub) else base_dir

    # 判断是否为 Qwen2.5-VL 等多模态模型
    model_type = ""
    try:
        with open(_resolved_model_dir / "config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            model_type = str(cfg.get("model_type", "")).lower()
    except Exception:
        pass
    _is_vl_model = model_type in {"qwen2_5_vl", "qwen2_vl"}

    if _is_vl_model and AutoModelForVision2Seq is not None:
        tokenizer = AutoTokenizer.from_pretrained(_resolved_model_dir, trust_remote_code=True)
        try:
            _processor = AutoProcessor.from_pretrained(_resolved_model_dir, trust_remote_code=True) if AutoProcessor else None
        except Exception:
            _processor = None
        model = AutoModelForVision2Seq.from_pretrained(
            _resolved_model_dir,
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            trust_remote_code=True,
            device_map="auto" if torch.cuda.is_available() else None,
        )
    else:
        tokenizer = AutoTokenizer.from_pretrained(_resolved_model_dir, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            _resolved_model_dir,
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            trust_remote_code=True,
            device_map="auto" if torch.cuda.is_available() else None,
        )
    if not torch.cuda.is_available():
        model.to(device)
    model.eval()


from finance_workspace import FinanceRouter


def _finance_bills():
    scope = _scope_sql()
    with _db_lock:
        return [dict(row) for row in _db_conn.execute(f'SELECT id,event_date,category,type,amount,currency,metadata FROM bills WHERE {scope}').fetchall()]


def _import_bank_bills(records):
    validated = [(r, BillCreate(**{key: r[key] for key in ('event_date', 'category', 'type', 'amount', 'currency', 'description', 'source', 'metadata')})) for r in records]
    ids, created, skipped = [], 0, 0
    with _db_lock:
        try:
            _db_conn.execute('BEGIN IMMEDIATE')
            _db_conn.execute('CREATE TABLE IF NOT EXISTS bank_import_index (import_key TEXT PRIMARY KEY, bill_id INTEGER NOT NULL)')
            for original, bill in validated:
                key = original['metadata']['bank_import_key']
                prior = _db_conn.execute('SELECT bills.* FROM bank_import_index JOIN bills ON bills.id=bank_import_index.bill_id WHERE import_key=?', (key,)).fetchone()
                if prior:
                    old = _row_to_bill(prior)
                    if (old.event_date, old.amount, old.type, old.currency, old.description, (old.metadata or {}).get('movement', 'cashflow')) != (bill.event_date, bill.amount, bill.type, bill.currency, bill.description, bill.metadata['movement']):
                        raise HTTPException(409, '已有相同流水标识但内容不同，整批未写入，请核对旧账单')
                    ids.append(prior['id'])
                    if old.metadata.get('import_batch') == bill.metadata['import_batch']:
                        created += 1  # recover the result of a previously committed, lost response
                    else:
                        skipped += 1
                    continue
                cursor = _db_conn.execute('INSERT INTO bills(event_date,category,type,amount,currency,description,source,metadata,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (bill.event_date.isoformat(), bill.category, bill.type, bill.amount, bill.currency, bill.description,
                     bill.source, json.dumps(bill.metadata, ensure_ascii=False), datetime.utcnow().isoformat()))
                _db_conn.execute('INSERT OR REPLACE INTO bank_import_index VALUES (?,?)', (key, cursor.lastrowid))
                ids.append(cursor.lastrowid)
                created += 1
            _db_conn.commit()
        except Exception:
            _db_conn.rollback()
            raise
    return {'created': created, 'duplicates': skipped, 'bill_ids': ids}


finance_router = FinanceRouter(DATA_DIR, _finance_bills, _import_bank_bills)
app.include_router(finance_router.router)
app.add_event_handler('shutdown', finance_router.reset)


@app.get("/health")
def health():
    scope = _scope_sql()
    with _db_lock:
        count = _db_conn.execute("SELECT COUNT(*) AS c FROM bills").fetchone()["c"]
        visible = _db_conn.execute(f"SELECT COUNT(*) FROM bills WHERE {scope}").fetchone()[0]
        bounds = _db_conn.execute(f"SELECT MIN(date(event_date)), MAX(date(event_date)) FROM bills WHERE {scope}").fetchone()
        categories = [row[0] for row in _db_conn.execute(f"SELECT DISTINCT category FROM bills WHERE {scope} ORDER BY category")]
        sources = {row[0] or 'unknown': row[1] for row in _db_conn.execute(f"SELECT source, COUNT(*) FROM bills WHERE {scope} GROUP BY source")}
    return {"status": "ok", "service": "bookkeeper", "device": device,
            "bill_count": count, "visible_bill_count": visible, "ledger_scope": finance_router.workspace().profile().ledger_scope,
            "database": str(DB_PATH), "llm": model_status(MODEL_DIR),
            "date_range": {"start": bounds[0], "end": bounds[1]},
            "categories": categories, "sources": sources,
            "category_schema": {"expense": EXPENSE_CATEGORIES, "income": INCOME_CATEGORIES},
            "dataset": {"version": os.getenv("ACCOUNTING_DATASET_VERSION", "original"),
                        "synthetic": bool(sources.get("synthetic_v2") or sources.get("synthetic") or sources.get("jsonl"))},
            "capabilities": {"ocr": provider(MODEL_DIR) == "local"}}


class ParseBillRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    reference_date: date


@app.post("/bills/parse")
def parse_bill(req: ParseBillRequest):
    reason = multiple_bill_reason(req.text)
    if reason:
        return {'needs_clarification': True, 'reason': reason, 'code': 'multiple_transactions'}
    guard = ("如果单笔的日期（包括今天或昨天）、金额和用途已明确，就输出账单，不要再次询问已提供的信息。"
             "明确的礼金支出和退货退款都是可记账的现金流；退款金额已明确时不要求凭证；不得引入输入未提及的借款。")
    reply = chat(ChatRequest(messages=[ChatMessage(role="user", content=req.text)],
                             system_prompt=extraction_prompt(req.reference_date.isoformat()) + guard,
                             include_bill_context=False, retrieval=False, max_new_tokens=512, temperature=0)).reply
    try:
        payload = json.loads(reply.strip().removeprefix("```json").removesuffix("```").strip())
        if not isinstance(payload, dict):
            raise ValueError("账单必须是 JSON 对象")
        if payload.get("needs_clarification") is True:
            return {"needs_clarification": True, "reason": str(payload.get("reason") or "请补充账单信息。")}
        allowed = INCOME_CATEGORIES if payload.get("type") == "income" else EXPENSE_CATEGORIES
        if payload.get("category") not in allowed or payload.get("currency") != "CNY":
            raise ValueError("分类、收支或币种不符合账本规范")
        bill = BillCreate.model_validate(payload)
        return {**bill.model_dump(mode="json"), "payment_method": explicit_payment(req.text)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "模型未返回有效账单，请补充日期、金额和用途后重试。") from exc


@app.get("/bills", response_model=List[Bill])
def list_bills(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    category: Optional[str] = None,
    type: Optional[str] = Query(None, pattern="^(expense|income)$"),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    sql = "SELECT * FROM bills WHERE " + _scope_sql()
    params: List = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if type:
        sql += " AND type = ?"
        params.append(type)
    if start_date:
        sql += " AND date(event_date) >= date(?)"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND date(event_date) <= date(?)"
        params.append(end_date.isoformat())
    sql += " ORDER BY datetime(event_date) DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _db_lock:
        rows = _db_conn.execute(sql, params).fetchall()
    return [_row_to_bill(row) for row in rows]


@app.post("/bills", response_model=Bill, status_code=201)
def create_bill(bill: BillCreate):
    metadata_json = (
        json.dumps(bill.metadata, ensure_ascii=False) if bill.metadata else None
    )
    now = datetime.utcnow().isoformat()

    with _db_lock:
        # A stable draft identifier makes retries safe after a lost response.
        request_id = (bill.metadata or {}).get("client_request_id")
        if request_id:
            existing = _db_conn.execute(
                "SELECT * FROM bills WHERE json_extract(metadata, '$.client_request_id') = ? LIMIT 1",
                (str(request_id),),
            ).fetchone()
            if existing is not None:
                prior = _row_to_bill(existing)
                if (prior.event_date, prior.amount, prior.currency, prior.type, prior.category, prior.description, prior.metadata) != (bill.event_date, bill.amount, bill.currency, bill.type, bill.category, bill.description, bill.metadata):
                    raise HTTPException(status_code=409, detail="这份草稿已经保存且内容不同，请刷新账本核对。")
                return prior
        cursor = _db_conn.execute(
            """
            INSERT INTO bills (event_date, category, type, amount, currency, description, source, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bill.event_date.isoformat(),
                bill.category,
                bill.type,
                bill.amount,
                bill.currency,
                bill.description,
                bill.source,
                metadata_json,
                now,
            ),
        )
        _db_conn.commit()
        new_id = cursor.lastrowid
        row = _db_conn.execute("SELECT * FROM bills WHERE id = ?", (new_id,)).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="账单写入失败")
    return _row_to_bill(row)


@app.delete("/bills/{bill_id}")
def delete_bill(bill_id: int):
    with _db_lock:
        # 先检查账单是否存在
        row = _db_conn.execute("SELECT id FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="账单不存在")
        # 删除账单
        _db_conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        _db_conn.commit()
    return {"message": "账单已删除", "id": bill_id}


@app.put('/bills/{bill_id}', response_model=Bill)
def update_bill(bill_id: int, data: BillUpdate):
    scope = _scope_sql()
    with _db_lock:
        row = _db_conn.execute('SELECT * FROM bills WHERE id=? AND ' + scope, (bill_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '账单不存在于当前账本')
        current = _row_to_bill(row)
        if current.version != data.expected_version:
            raise HTTPException(409, '账单已发生变化，请关闭后重新打开再编辑。')
        if data.source is not None or data.metadata is not None:
            raise HTTPException(422, '编辑不能替换账单来源或导入关联')
        metadata = dict(current.metadata or {})
        metadata['payment_method'] = data.payment_method
        _db_conn.execute('INSERT INTO bill_revisions (bill_id,prior,changed_at) VALUES (?,?,?)',
                         (bill_id, json.dumps(dict(row), ensure_ascii=False), datetime.utcnow().isoformat()))
        _db_conn.execute('UPDATE bills SET event_date=?,category=?,type=?,amount=?,currency=?,description=?,metadata=? WHERE id=?',
                         (data.event_date.isoformat(), data.category, data.type, data.amount, data.currency,
                          data.description, json.dumps(metadata, ensure_ascii=False), bill_id))
        _db_conn.commit()
        return _row_to_bill(_db_conn.execute('SELECT * FROM bills WHERE id=?', (bill_id,)).fetchone())


@app.post("/bills/ocr")
async def ocr_bill(file: UploadFile = File(...)):
    """
    上传图片进行OCR识别，返回解析后的账单信息
    """
    import io
    from PIL import Image
    
    if provider(MODEL_DIR) != "local":
        raise HTTPException(status_code=503, detail="票据识别需要配置本地 Qwen 视觉模型。")

    # 检查是否为图片文件
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    
    # 读取图片
    try:
        image_data = await file.read(10 * 1024 * 1024 + 1)
        if len(image_data) > 10 * 1024 * 1024:
            raise ValueError("图片不能超过 10 MB")
        image = Image.open(io.BytesIO(image_data))
        # 转换为RGB格式（如果需要）
        if image.mode != 'RGB':
            image = image.convert('RGB')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片读取失败: {str(e)}")
    
    # 检查是否是多模态模型
    load_model_if_needed()
    if not _is_vl_model or _processor is None:
        # 如果不是视觉模型，使用文本描述的方式
        # 这里可以集成其他OCR服务，暂时返回提示
        raise HTTPException(
            status_code=501, 
            detail="当前模型不支持图片识别，请使用文本描述或配置视觉模型"
        )
    
    # 使用视觉模型进行识别
    try:
        # 构建提示词
        prompt = "请识别这张票据图片中的账单信息，包括日期、金额、类别、商户名称等。请以JSON格式输出，包含以下字段：event_date(YYYY-MM-DD格式的日期), category(类别), type(expense或income), amount(金额数字), currency(币种，默认CNY), description(描述信息)。仅输出JSON，不要其他内容。"
        
        # 使用processor处理图片和文本
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": prompt}
                ]
            }
        ]
        
        # 使用processor处理
        try:
            # 尝试使用processor处理图片和文本
            if hasattr(_processor, 'apply_chat_template'):
                text = _processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                image_inputs, video_inputs = [image], None
                inputs = _processor(
                    text=[text],
                    images=image_inputs,
                    videos=video_inputs,
                    padding=True,
                    return_tensors="pt"
                )
                inputs = inputs.to(model.device)
                
                # 生成回复
                with torch.no_grad():
                    generated_ids = model.generate(
                        **inputs,
                        max_new_tokens=512,
                        do_sample=False,
                    )
                generated_ids_trimmed = [
                    out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
                ]
                reply = _processor.batch_decode(
                    generated_ids_trimmed,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False
                )[0]
            else:
                # 如果processor不支持，使用tokenizer和模型直接处理
                # 这里需要根据具体模型调整
                raise NotImplementedError("Processor不支持图片处理")
        except Exception as proc_error:
            raise HTTPException(status_code=502, detail="票据识别失败，请改用手动录入；没有生成或保存账单。") from proc_error

        # 解析JSON
        import re
        import json
        # 尝试找到JSON对象（支持嵌套）
        json_match = None
        # 先尝试找到 ```json 代码块
        json_block_match = re.search(r'```json\s*(\{.*?\})\s*```', reply, re.DOTALL)
        if json_block_match:
            json_match = json_block_match.group(1)
        else:
            # 尝试找到第一个 { 到最后一个 } 之间的内容
            first_brace = reply.find('{')
            last_brace = reply.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                json_match = reply[first_brace:last_brace+1]
        
        if json_match:
            try:
                bill_data = json.loads(json_match)
                # 验证和规范化数据
                if 'event_date' not in bill_data or 'amount' not in bill_data:
                    raise ValueError("缺少必要字段")
                return {"parsed": bill_data, "raw_text": reply}
            except (json.JSONDecodeError, ValueError) as e:
                raise HTTPException(status_code=500, detail=f"解析JSON失败: {str(e)}, 原始回复: {reply[:200]}")
        else:
            raise HTTPException(status_code=500, detail=f"未找到有效的JSON，原始回复: {reply[:200]}")
            
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[ERROR] OCR failed: {error_detail}")
        raise HTTPException(status_code=500, detail=f"OCR识别失败: {str(e)}")


@app.get("/bills/summary")
def bill_summary():
    scope = _scope_sql(cashflow_only=True)
    with _db_lock:
        totals = _db_conn.execute(
            f"""
            SELECT 
                SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS total_income,
                SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS total_expense
            FROM bills WHERE {scope}
            """
        ).fetchone()
        by_category = _db_conn.execute(
            f"""
            SELECT category, type, SUM(amount) AS total
            FROM bills WHERE {scope}
            GROUP BY category, type
            ORDER BY total DESC
            """
        ).fetchall()
    return {
        "total_income": totals["total_income"] or 0,
        "total_expense": totals["total_expense"] or 0,
        "by_category": [
            {"category": row["category"], "type": row["type"], "total": row["total"]}
            for row in by_category
        ],
    }


@app.get("/reports/aggregate")
def aggregate_reports(
    start_date: Optional[date] = Query(None, description="自定义开始日期 YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="自定义结束日期 YYYY-MM-DD"),
):
    if start_date and end_date:
        if start_date > end_date:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")
        if (end_date - start_date).days > 3660:
            raise HTTPException(status_code=400, detail="日期范围不能超过十年")
        previous_start = start_date - timedelta(days=(end_date - start_date).days + 1)
        records = _fetch_db_records(previous_start, end_date)
        # 同时读取上期，保证比较值正确
        return {"custom": _build_custom_report(records, start_date, end_date)}
    if start_date or end_date:
        raise HTTPException(status_code=400, detail="请同时提供开始与结束日期")
    return build_report_payload()


@app.get("/advice/context")
def advice_context(reference_date: Optional[date] = Query(None)):
    try:
        return build_advice_payload(reference_date)
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[ERROR] /advice/context failed: {error_detail}")
        raise HTTPException(status_code=500, detail=f"生成财务建议上下文失败: {str(e)}")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:

    system_prompt = req.system_prompt or DEFAULT_SYSTEM_PROMPT
    # 注入禁止 CoT 的系统指令
    system_prompt = f"{system_prompt}\n\n{_force_no_cot_instruction()}".strip()
    # RAG：如果启用检索，则使用全库检索上下文；否则按原逻辑使用最新 20 条
    use_retrieval = req.retrieval if req.retrieval is not None else DEFAULT_RETRIEVAL
    if req.ledger_range and req.include_bill_context:
        bounds = req.ledger_range
        scope = _scope_sql(cashflow_only=True)
        with _db_lock:
            rows = _db_conn.execute('SELECT type,category,amount FROM bills WHERE ' + scope +
                                   ' AND date(event_date)>=? AND date(event_date)<=?',
                                   (bounds.start_date.isoformat(), bounds.end_date.isoformat())).fetchall()
        income = sum(round(r['amount'] * 100) for r in rows if r['type'] == 'income')
        expense = sum(round(r['amount'] * 100) for r in rows if r['type'] == 'expense')
        totals = {'start_date': bounds.start_date.isoformat(), 'end_date': bounds.end_date.isoformat(),
                  'income': income / 100, 'expense': expense / 100, 'net': (income-expense) / 100}
        totals['expense_categories'] = {c: round(sum(r['amount'] for r in rows if r['type']=='expense' and r['category']==c),2) for c in sorted({r['category'] for r in rows if r['type']=='expense'})}
        bill_context = '用户当前选定区间，必须优先使用此区间回答“本月/这段时间”，不要替换成系统当前月：' + json.dumps(totals, ensure_ascii=False)
    elif use_retrieval and req.include_bill_context:
        bill_context = build_retrieval_context(month_window=req.retrieval_months)
    else:
        bill_context = build_bill_context() if req.include_bill_context else ""

    if bill_context:
        bill_context = (f"今天是 {datetime.now(ZoneInfo('Asia/Shanghai')).date()}（Asia/Shanghai）。"
                        "账本可能包含合成演示记录，不能视作用户真实财务情况。\n" + bill_context)

    if provider(MODEL_DIR) != "local":
        messages = [{"role": "system", "content": system_prompt}]
        if bill_context:
            messages.append({"role": "system", "content": bill_context})
        messages.extend(m.model_dump() for m in req.messages)
        return ChatResponse(reply=_sanitize_reply(api_chat(messages, req.max_new_tokens, req.temperature, req.top_p)))
    load_model_if_needed()

    conversation_parts: List[str] = []
    if system_prompt:
        conversation_parts.append(f"<|system|>\n{system_prompt}")
    if bill_context:
        conversation_parts.append(f"<|system|>\n{bill_context}")
    for m in req.messages:
        role = m.role.strip().lower()
        if role not in {"user", "assistant", "system"}:
            role = "user"
        if role == "system":
            conversation_parts.append(f"<|system|>\n{m.content}")
        elif role == "user":
            conversation_parts.append(f"<|user|>\n{m.content}")
        else:
            conversation_parts.append(f"<|assistant|>\n{m.content}")

    if hasattr(tokenizer, "apply_chat_template"):
        hf_messages = []
        if system_prompt:
            hf_messages.append({"role": "system", "content": system_prompt})
        if bill_context:
            hf_messages.append({"role": "system", "content": bill_context})
        for m in req.messages:
            hf_messages.append({"role": m.role, "content": m.content})
        input_ids = tokenizer.apply_chat_template(
            hf_messages,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(model.device)
    else:
        input_text = "\n".join(conversation_parts) + "\n<|assistant|>\n"
        input_ids = tokenizer.encode(input_text, return_tensors="pt").to(model.device)

    # 服务端统一长度策略（默认/上限/下限）
    eff_max_new = req.max_new_tokens if req.max_new_tokens and req.max_new_tokens > 0 else GEN_DEFAULT_MAX_NEW_TOKENS
    eff_max_new = max(32, min(eff_max_new, GEN_MAX_NEW_TOKENS_CAP))
    eff_min_new = None
    if req.min_new_tokens is not None:
        try:
            eff_min_new = max(0, min(req.min_new_tokens, eff_max_new // 2))
        except Exception:
            eff_min_new = None

    with torch.no_grad():
        generated_ids = model.generate(
            input_ids=input_ids,
            max_new_tokens=eff_max_new,
            min_new_tokens=eff_min_new,
            do_sample=req.temperature > 0,
            temperature=req.temperature,
            top_p=req.top_p,
            repetition_penalty=req.repetition_penalty,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    new_tokens = generated_ids[0, input_ids.shape[-1]:]
    reply = tokenizer.decode(new_tokens, skip_special_tokens=True)
    reply = _sanitize_reply(reply)

    return ChatResponse(reply=reply.strip())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8010"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)





