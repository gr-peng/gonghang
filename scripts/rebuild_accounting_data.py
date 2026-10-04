"""Deterministic, non-destructive ledger audit and synthetic SFT corpus builder.

Money is generated once in integer cents, then rendered into both text and JSON.
Train/validation/test use disjoint seeds, dates and phrasing. Never writes the source DB.
"""
import argparse
import calendar
from collections import Counter
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AI_accounting_agent/backend"))
from accounting_schema import EXPENSE_CATEGORIES, INCOME_CATEGORIES, extraction_prompt

# Category-specific *plausible example ranges*, not a claim about any user's finances.
SCENARIOS = {
    "餐饮": ("expense", 100, 60000, ["午餐", "咖啡", "晚餐", "朋友聚餐"]),
    "出行": ("expense", 100, 80000, ["地铁车票", "公交车票", "出租车费", "高铁车票"]),
    "购物": ("expense", 100, 2500000, ["购买耳机", "购买衣服", "购买手机", "购买电脑"]),
    "生活缴费": ("expense", 100, 150000, ["水费", "电费", "燃气费", "宽带费"]),
    "娱乐": ("expense", 100, 100000, ["电影票", "视频会员", "游戏充值", "演唱会门票"]),
    "住房": ("expense", 50000, 1800000, ["房租", "物业费", "房屋维修费"]),
    "医疗健康": ("expense", 100, 2000000, ["门诊挂号费", "购买药品", "体检费", "牙科治疗费"]),
    "教育学习": ("expense", 100, 3000000, ["课程学费", "教材费", "考试报名费"]),
    "保险": ("expense", 10000, 2000000, ["医疗保险保费", "意外险保费", "家庭财产保险保费"]),
    "人情往来": ("expense", 100, 500000, ["婚礼礼金", "生日红包", "给长辈的节日礼金"]),
    "旅行": ("expense", 1000, 3000000, ["旅行酒店住宿费", "旅游团费", "旅行景区门票"]),
    "其他": ("expense", 1, 100000, ["快递费", "打印费", "遗失物品补办费"]),
    "工资": ("income", 200000, 10000000, ["税后工资", "公司月薪"]),
    "奖金": ("income", 10000, 10000000, ["季度奖金", "年终奖金", "绩效奖金"]),
    "副业": ("income", 1000, 3000000, ["兼职收入", "设计外包收入", "稿费收入"]),
    "理财收益": ("income", 1, 500000, ["银行存款利息", "基金分红", "理财利息收益"]),
    "退款": ("income", 1, 2500000, ["退货退款", "订单取消退款", "机票退款"]),
    "其他收入": ("income", 100, 500000, ["收到生日礼金", "收到节日红包"]),
}
PAYMENTS = ["微信支付", "支付宝", "银行卡", "现金", ""]


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(source, jsonl, output):
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    backup = output / "original-bills.db"
    with sqlite3.connect(backup) as dest:
        conn.backup(dest)
    records = [dict(r) for r in conn.execute("SELECT * FROM bills ORDER BY id")]
    categories = [dict(r) for r in conn.execute("SELECT category,type,count(*) count,round(sum(amount),2) total,min(amount) minimum,max(amount) maximum FROM bills GROUP BY category,type")]
    duplicates = [dict(r) for r in conn.execute("SELECT event_date,category,type,amount,description,count(*) count FROM bills GROUP BY event_date,category,type,amount,description HAVING count(*)>1")]
    mismatches = []
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, 1):
        messages = json.loads(line)["messages"]
        text = next(m["content"] for m in messages if m["role"] == "user")
        target = json.loads(next(m["content"] for m in messages if m["role"] == "assistant"))
        amounts = re.findall(r"(\d+(?:\.\d+)?)元", text)
        if amounts and all(Decimal(v) != Decimal(str(target["金额"])) for v in amounts):
            mismatches.append(idx)
    report = {
        "source": str(source), "record_count": len(records),
        "records_sha256": hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "date_range": [min(r["event_date"] for r in records), max(r["event_date"] for r in records)],
        "categories": categories, "sources": dict(Counter(r["source"] for r in records)),
        "missing_categories": sorted(set(SCENARIOS) - {r["category"] for r in records}),
        "duplicate_candidates": duplicates,
        "currency_counts": dict(Counter(r["currency"] for r in records)),
        "date_review_candidates": [],
        "source_training_rows": len(lines), "text_amount_mismatch_count": len(mismatches),
        "text_amount_mismatch_lines": mismatches,
        "findings": [
            "原生成器先渲染文字，再缩放/分配 JSON 金额，导致监督标签与输入冲突。",
            "原生成器每次追加数据库且没有生成批次标识；不能将多批合成数据的总和当作一个人的真实收支。",
            "工资配额随机选择收入类别，可把大额工资样本标成理财收益。",
            "原前端收入录入只显示支出类别；原后端报表将新增类别归入其他。",
            "重复候选和日期/币种异常仅记录，保留用户原记录，未自动纠正。",
        ],
        "scope": "个人人民币现金收支演示；不是完整复式会计、资产负债或多币种账本。",
    }
    for row in records:
        try:
            raw = json.loads(row.get("metadata") or "{}").get("raw", "")
            if not isinstance(raw, str): continue
            match = re.match(r"^(\d{1,2})-(\d{1,2})(?!\d)", raw)
            if match and row["event_date"][5:] != f"{int(match[1]):02d}-{int(match[2]):02d}":
                report["date_review_candidates"].append({"id":row["id"],"event_date":row["event_date"],"raw":raw,
                    "reason":"原文月日与存储日期不同；保留原记录，不擅自推断年份和纠正日期。"})
        except (ValueError, TypeError):
            continue
    dump(output / "audit.json", report)
    conn.close()
    return report, records


def record(category, cents, day, description, payment="", source="synthetic_v2"):
    return dict(event_date=day, category=category, type=SCENARIOS[category][0], amount=cents / 100,
                currency="CNY", description=description, payment_method=payment, source=source)


def make_ledger(reference):
    rng = random.Random(20261003)
    rows = []
    # Twelve full months plus the current partial month; calendar arithmetic, no 30-day stepping.
    month_index = reference.year * 12 + reference.month - 1
    for index in range(month_index - 12, month_index + 1):
        year, month0 = divmod(index, 12)
        month = month0 + 1
        last = reference.day if index == month_index else calendar.monthrange(year, month)[1]
        def add(category, cents, day, description, payment="银行卡"):
            if day <= last:
                rows.append(record(category, cents, date(year, month, day).isoformat(), description, payment))
        add("工资", 2250000, 10, "税后月薪")
        add("住房", 580000, 1, "月度房租")
        add("生活缴费", rng.randint(24000, 52000), 3, "水电燃气与宽带")
        add("理财收益", rng.randint(100, 22000), 21, "存款利息")
        if month % 3 == 0:
            add("奖金", rng.randint(400000, 1200000), 15, "季度绩效奖金")
        if month % 2 == 0:
            add("副业", rng.randint(50000, 300000), 18, "设计外包收入")
        if month in [2, 8]:
            add("其他收入", 60000, 20, "收到生日礼金")
        for day in range(1, last + 1):
            for purpose in ["午餐", "晚餐"]:
                add("餐饮", rng.randint(1500, 6500), day, purpose, "支付宝")
            if date(year, month, day).weekday() < 5:
                add("出行", rng.choice([400, 600, 800, 1200]), day, "地铁通勤", "微信支付")
        for category, cents in [("购物", rng.randint(8000, 240000)), ("娱乐", rng.randint(3000, 36000)),
                                ("医疗健康", rng.randint(2500, 68000)), ("教育学习", rng.randint(3500, 120000)),
                                ("人情往来", rng.choice([20000, 50000, 80000])), ("其他", rng.randint(100, 8000))]:
            add(category, cents, 8, SCENARIOS[category][3][0])
        if month in [1, 7]: add("保险", 280000, 12, "年度医疗保险保费")
        if month in [2, 5, 10]: add("旅行", rng.randint(150000, 650000), 2, "假期旅游团费")
        # Refund has an explicit originating expense, never a made-up earning.
        if month % 2:
            add("购物", 12900, 16, "购买耳机（后续退货）")
            add("退款", 12900, 22, "耳机退货退款")
    return rows


def sample(category, rng, split, index):
    kind, low, high, purposes = SCENARIOS[category]
    # Log-scale sampling gives small, medium and large amounts instead of only large uniform values.
    import math
    cents = round(math.exp(rng.uniform(math.log(low), math.log(high))))
    if index == 0: cents = low
    if index == 1: cents = high
    year = {"train": 2025, "validation": 2026, "test": 2027}[split]
    day = date(year, rng.randint(1, 12), rng.randint(1, 28))
    payment = rng.choice(PAYMENTS)
    purpose = rng.choice(purposes)
    target = record(category, cents, day.isoformat(), purpose, payment)
    target.pop("source")
    amount = f"{cents / 100:.2f}"
    action = "支付" if kind == "expense" else "收到"
    method = f"，支付方式是{payment}" if payment else ""
    templates = {
        "train": ["{day}{action}{amount}元用于{purpose}{method}。", "帮我记账：{day}，{purpose}，{action}{amount}元{method}。", "{day}这笔{purpose}{action}人民币{amount}元{method}。"],
        "validation": ["账单备注：{purpose}；发生于{day}；{action}{amount}元{method}，请整理。"],
        "test": ["请录一笔，日期{day}。{action}{amount}元，事项为{purpose}{method}。"],
    }
    text = rng.choice(templates[split]).format(day=day, action=action, amount=amount, purpose=purpose, method=method)
    return {"task": "extract", "category": category, "messages": [
        {"role": "system", "content": extraction_prompt(day.isoformat())},
        {"role": "user", "content": text},
        {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
    ]}


def corpus(split, per_category):
    rng = random.Random({"train": 8117, "validation": 9013, "test": 10037}[split])
    rows = [sample(cat, rng, split, i) for cat in SCENARIOS for i in range(per_category)]
    scenarios = [
        ("今天买了午餐，帮我记一下", "请提供午餐的实际支付金额。"),
        ("在自己的两张银行卡之间转了{amount}元", "这是本人账户内部转账，不计收入或支出，请确认是否另有手续费。"),
        ("今天信用卡还款{amount}元", "信用卡还款不重复计为消费，请核对原消费是否已入账。"),
        ("向朋友借入{amount}元", "借款本金属于负债变动，请单独记录，不计收入。"),
        ("用{amount}元买入基金", "基金本金属于资产转换，不计消费支出；请确认有无单独手续费。"),
        ("午餐30元，晚餐{amount}元，帮我一次入账", "包含多笔交易，请分别确认每笔的日期、类别和金额。"),
        ("酒店花了{amount}美元", "当前账本使用人民币，请提供确认后的人民币金额和日期。"),
        ("2026年2月30日买书{amount}元", "该日期不存在，请确认实际交易日期。"),
    ]
    for i in range(max(16, per_category * 2)):
        template, reason = scenarios[i % len(scenarios)]
        rows.append({"task": "clarify", "messages": [
            {"role": "system", "content": extraction_prompt("2026-10-03")},
            {"role": "user", "content": template.format(amount=f"{rng.randint(1, 1000000)/100:.2f}")},
            {"role": "assistant", "content": json.dumps({"needs_clarification": True, "reason": reason}, ensure_ascii=False)},
        ]})
    for _ in range(per_category * 2):
        income, expense = rng.randint(100000, 6000000), rng.randint(50000, 7000000)
        balance = income - expense
        summary = {"income": income / 100, "expense": expense / 100, "net": balance / 100, "currency": "CNY", "period": "2026-09"}
        answer = f"2026年9月收入 {income/100:.2f} 元，支出 {expense/100:.2f} 元，结余 {balance/100:.2f} 元。"
        answer += "本期支出超过收入，建议先检查大额和非必要支出。" if balance < 0 else "本期有结余，可根据实际预算安排储蓄。"
        answer += "这是合成演示账本的区间统计，不能据此推断你的真实收入、职业或风险偏好。"
        rows.append({"task": "summary", "messages": [
            {"role": "system", "content": "你是记账助手。只依据提供的区间统计回答，不编造用户画像，不声称已执行交易。"},
            {"role": "user", "content": "请解释这份合成账本统计：" + json.dumps(summary, ensure_ascii=False)},
            {"role": "assistant", "content": answer},
        ]})
    # Refusal/clarification templates can repeat with no numeric input: remove exact input duplicates.
    unique = {json.dumps(r["messages"][:-1], ensure_ascii=False): r for r in rows}
    rows = list(unique.values())
    rng.shuffle(rows)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/accounting-v2")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 10, 3))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise SystemExit("Version already exists; select a new --output to avoid overwriting an active ledger.")
    source = ROOT / "AI_accounting_agent/backend/data/bills.db"
    report, old = audit(source, source.with_name("synthetic_bank_bills.jsonl"), output)
    rows = make_ledger(args.reference_date)
    ledger_dir = output / "ledger"
    ledger_dir.mkdir(exist_ok=True)
    with sqlite3.connect(ledger_dir / "bills.db") as conn:
        conn.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY AUTOINCREMENT,event_date TEXT NOT NULL,category TEXT NOT NULL,type TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,description TEXT NOT NULL,source TEXT,metadata TEXT,created_at TEXT NOT NULL)")
        # Preserve the user's four manual/voice records, IDs and metadata byte-for-byte.
        for r in old:
            if r["source"] != "synthetic":
                conn.execute("INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(r.values()))
        for r in rows:
            meta = json.dumps({"dataset_version": "accounting-v2", "synthetic": True, "payment_method": r["payment_method"]}, ensure_ascii=False)
            conn.execute("INSERT INTO bills (event_date,category,type,amount,currency,description,source,metadata,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (r["event_date"],r["category"],r["type"],r["amount"],r["currency"],r["description"],r["source"],meta,"2026-10-03T00:00:00+08:00"))
    dump(output / "ledger.json", rows)
    seen = set()
    splits = {}
    for split, count in [("train", 64), ("validation", 8), ("test", 8)]:
        samples = corpus(split, count)
        # Drop exact overlap across splits, including fixed missing-amount questions.
        clean = []
        for r in samples:
            key = json.dumps(r["messages"][:-1], ensure_ascii=False)
            if key in seen: continue
            seen.add(key)
            clean.append(r)
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in clean), encoding="utf-8")
        splits[split] = {"rows": len(clean), "tasks": dict(Counter(r["task"] for r in clean)), "sha256": sha(path)}
    summary = {k: {"count": sum(r["category"] == k for r in rows), "total": float(sum(Decimal(str(r["amount"])) for r in rows if r["category"] == k))} for k in SCENARIOS}
    manifest = {"version": "accounting-v2", "seed": 20261003, "reference_date": str(args.reference_date),
                "synthetic_ledger_count": len(rows), "preserved_user_records": sum(r["source"] != "synthetic" for r in old),
                "original_record_count": len(old), "original_unchanged": True, "splits": splits,
                "categories": summary, "scope": report["scope"],
                "refund_policy": "现金流收入单列退款；不等同于盈利，消费净额应扣除退款。",
                "original_snapshot": str(output / "original-bills.db")}
    dump(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
