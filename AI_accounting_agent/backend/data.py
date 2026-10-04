"""Synthetic bill generator with monthly-balanced income/expense."""

import json
import os
import random
import datetime
import sqlite3
from pathlib import Path


EXPENSE_CATEGORIES = {
    "餐饮": ["星巴克", "肯德基", "麦当劳", "海底捞", "喜茶", "瑞幸咖啡", "外卖", "全家便利店"],
    "出行": ["滴滴出行", "12306", "高德打车", "共享单车", "携程"],
    "购物": ["淘宝", "京东", "拼多多", "天猫超市", "唯品会"],
    "生活缴费": ["国家电网", "中国移动", "中国联通", "自来水公司", "燃气公司"],
    "娱乐": ["腾讯视频", "爱奇艺", "Bilibili", "Steam", "Netflix"],
    "其他": ["快递费", "打印店", "本地小店", "便利店"],
}

INCOME_SOURCES = {
    "工资": ["公司工资发放"],
    "奖金": ["绩效奖金", "季度奖金"],
    "副业": ["兼职收入", "外包项目"],
    "理财收益": ["余额宝收益", "基金分红"],
}

PAYMENT_METHODS = ["微信支付", "支付宝", "银行卡", "信用卡"]

expense_templates = [
    "{date}在{merchant}消费了{amount}元，使用{payment}支付。",
    "{date}通过{payment}在{merchant}花了{amount}元。",
    "{date}我在{merchant}花费{amount}元（{payment}）。",
]

income_templates = [
    "{date}收到来自{merchant}的收入，共计{amount}元。",
    "{date}获得{merchant}发放的{amount}元入账。",
    "{date}账户增加收入{amount}元，来源：{merchant}。",
]

MONTH_EXPENSE_WEIGHTS = {
    1: 0.9,
    2: 0.8,
    3: 1.0,
    4: 1.1,
    5: 1.0,
    6: 1.1,
    7: 0.9,
    8: 0.8,
    9: 1.2,
    10: 1.3,
    11: 1.1,
    12: 1.4,
}

CATEGORY_DISTRIBUTION = {
    "餐饮": 0.35,
    "出行": 0.10,
    "购物": 0.25,
    "生活缴费": 0.10,
    "娱乐": 0.15,
    "其他": 0.05,
}

EXPENSE_AMOUNT_RANGE = {
    "餐饮": (20, 150),
    "出行": (3, 100),
    "购物": (20, 1000),
    "生活缴费": (50, 600),
    "娱乐": (10, 800),
    "其他": (5, 200),
}

INCOME_AMOUNT_RANGE = {
    "工资": (11000, 13000),
    "奖金": (1000, 3000),
    "副业": (200, 1000),
    "理财收益": (5, 200),
}


def generate_expense(date: str):
    category = random.choices(
        list(CATEGORY_DISTRIBUTION.keys()),
        list(CATEGORY_DISTRIBUTION.values()),
    )[0]
    merchant = random.choice(EXPENSE_CATEGORIES[category])
    payment = random.choice(PAYMENT_METHODS)
    low, high = EXPENSE_AMOUNT_RANGE[category]
    amount = round(random.uniform(low, high), 2)
    template = random.choice(expense_templates)
    text = template.format(date=date, merchant=merchant, amount=amount, payment=payment)
    return text, {
        "日期": date,
        "金额": amount,
        "类别": category,
        "商户": merchant,
        "支付方式": payment,
        "描述": f"{category}消费",
        "type": "expense",
    }


def generate_income(date: str):
    category = random.choice(list(INCOME_SOURCES.keys()))
    merchant = random.choice(INCOME_SOURCES[category])
    low, high = INCOME_AMOUNT_RANGE[category]
    amount = round(random.uniform(low, high), 2)
    template = random.choice(income_templates)
    text = template.format(date=date, merchant=merchant, amount=amount)
    return text, {
        "日期": date,
        "金额": amount,
        "类别": category,
        "商户": merchant,
        "支付方式": "银行入账",
        "描述": f"{category}收入",
        "type": "income",
    }


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            category TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            description TEXT NOT NULL,
            source TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def main():
    def _split_amount(total, n):
        if n <= 0:
            return []
        weights = [random.random() + 0.5 for _ in range(n)]
        s = sum(weights)
        return [round(total * w / s, 2) for w in weights]

    def _generate_month(year, month):
        income_target = random.uniform(12000, 20000)
        expense_target = income_target * random.uniform(0.72, 0.95)

        income_entries = []
        salary_count = random.randint(1, 2)
        other_income_count = random.randint(0, 2)
        income_parts = salary_count + other_income_count
        income_amounts = _split_amount(income_target, income_parts)
        for i in range(income_parts):
            day = random.randint(3, 12) if i < salary_count else random.randint(13, 25)
            date = datetime.date(year, month, min(day, 28)).isoformat()
            text, output = generate_income(date)
            output["日期"] = date
            output["金额"] = abs(income_amounts[i])
            output["type"] = "income"
            output["描述"] = output.get("描述", "收入")
            income_entries.append((text, output))

        base_expense = random.randint(60, 100)
        weight = MONTH_EXPENSE_WEIGHTS.get(month, 1.0)
        expense_count = max(30, int(base_expense * weight))
        expense_raw = []
        for _ in range(expense_count):
            day = random.randint(1, 28)
            date = datetime.date(year, month, day).isoformat()
            text, output = generate_expense(date)
            output["日期"] = date
            expense_raw.append((text, output))
        current_total = sum(item[1]["金额"] for item in expense_raw)
        scale = expense_target / current_total if current_total > 0 else 1.0
        scale = min(1.4, max(0.6, scale))
        expense_entries = []
        for text, output in expense_raw:
            scaled = round(output["金额"] * scale, 2)
            output["金额"] = max(1.0, scaled)
            output["type"] = "expense"
            output["描述"] = output.get("描述", "支出")
            expense_entries.append((text, output))

        return income_entries + expense_entries

    data_dir = Path(
        os.environ.get("AI_BOOKKEEPER_DATA_DIR", Path(__file__).resolve().parent / "data")
    )
    data_dir.mkdir(parents=True, exist_ok=True)

    dataset = []
    rows_for_db = []
    today = datetime.date.today()

    for m_offset in range(12):
        dt = today - datetime.timedelta(days=30 * m_offset)
        year, month = dt.year, dt.month
        entries = _generate_month(year, month)
        for text, output in entries:
            dataset.append(
                {
                    "messages": [
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                    ]
                }
            )
            now = datetime.datetime.utcnow().isoformat()
            rows_for_db.append(
                (
                    output["日期"],
                    output["类别"],
                    output["type"],
                    float(output["金额"]),
                    "CNY",
                    output["描述"],
                    "synthetic",
                    json.dumps(output, ensure_ascii=False),
                    now,
                )
            )

    total_rows = len(rows_for_db)

    jsonl_path = data_dir / "synthetic_bank_bills.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for item in dataset:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    db_path = data_dir / "bills.db"
    conn = sqlite3.connect(db_path)
    _ensure_db(conn)
    conn.executemany(
        """
        INSERT INTO bills
        (event_date, category, type, amount, currency, description, source, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_for_db,
    )
    conn.commit()
    conn.close()

    print("✅ 数据生成完毕")
    print(" - JSONL:", jsonl_path)
    print(" - SQLite:", db_path)
    print(" - 总记录数:", total_rows)


if __name__ == "__main__":
    main()