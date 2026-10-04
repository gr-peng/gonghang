"""Explicit demo data only; run.py --demo starts an isolated database."""
import json
import os
from datetime import date, timedelta
from urllib.request import Request, urlopen

today = date.today()
start = today.replace(day=1)
items = [(0, '工资', 'income', 12000, '工资 · 示例', '银行卡'), (2, '住房', 'expense', 2500, '房租 · 示例', '银行卡'), (4, '出行', 'expense', 90, '通勤 · 示例', '交通卡'), (7, '餐饮', 'expense', 328, '朋友聚餐 · 示例', '微信支付'), (9, '购物', 'expense', 480, '耳机 · 示例', '支付宝'), (11, '娱乐', 'expense', 120, '电影 · 示例', '微信支付')]
for day, category, kind, amount, description, payment in items:
    dt = min(start + timedelta(days=day), today)
    payload = dict(event_date=dt.isoformat(), category=category, type=kind, amount=amount, currency='CNY', description=description, source='synthetic_demo', metadata={'payment_method':payment, 'client_request_id':f'demo-{start}-{day}'})
    port = int(os.getenv('BOOKKEEPER_PORT', '8010'))
    req = Request(f'http://127.0.0.1:{port}/bills', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(req) as response:
        json.load(response)
print('独立示例账本已准备完成；页面会持续标注“示例”。')
