"""CSV statement normalization. Preview first; no file or model can authorize a write.

Only selected columns are retained. Account numbers, balances and unrelated CSV
columns never enter the ledger or training feedback. Money is parsed as Decimal.
"""
from __future__ import annotations

import base64
import csv
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re

from accounting_schema import EXPENSE_CATEGORIES, INCOME_CATEGORIES

MAX_BYTES, MAX_ROWS = 1_000_000, 500
ALIASES = {
    'date': ('日期', '交易日期', '记账日期', '交易时间', 'date', 'event_date'),
    'amount': ('金额', '交易金额', '发生额', 'amount'),
    'type': ('收支', '收支类型', '交易类型', '借贷标志', 'direction', 'type'),
    'expense': ('支出金额', '借方发生额', '借方金额', 'expense', 'debit'),
    'income': ('收入金额', '贷方发生额', '贷方金额', 'income', 'credit'),
    'description': ('摘要', '备注', '用途', '交易摘要', 'description', 'memo'),
    'category': ('分类', '类别', 'category'),
    'currency': ('币种', '货币', 'currency'),
    'txid': ('流水号', '交易流水号', '交易编号', 'transaction_id', 'txid'),
    'movement': ('资金性质', 'movement'),
}
KINDS = {'支出': 'expense', '支': 'expense', '借': 'expense', '借方': 'expense',
         'expense': 'expense', 'debit': 'expense', '收入': 'income', '收': 'income',
         '贷': 'income', '贷方': 'income', 'income': 'income', 'credit': 'income'}
MOVEMENTS = {'收支': 'cashflow', 'cashflow': 'cashflow', '内部划转': 'internal_transfer',
             'internal_transfer': 'internal_transfer', '本金划转': 'principal', 'principal': 'principal'}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def decimal_amount(raw: str, *, signed=False) -> Decimal:
    text = raw.strip().replace('￥', '').replace('¥', '')
    # Grouped thousands are allowed only when their grouping is unambiguous.
    if ',' in text:
        if not re.fullmatch(r'[+-]?\d{1,3}(,\d{3})+(\.\d{1,2})?', text):
            raise ValueError('金额的千位分隔格式无效')
        text = text.replace(',', '')
    pattern = r'[+-]?\d+(\.\d{1,2})?' if signed else r'\d+(\.\d{1,2})?'
    if not re.fullmatch(pattern, text):
        raise ValueError('金额须为最多两位小数的人民币数值')
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError('金额无效') from None
    if not amount.is_finite() or not Decimal('.01') <= abs(amount) <= Decimal('99999999.99'):
        raise ValueError('金额应在 0.01–99,999,999.99 元之间')
    return amount


def statement_date(raw: str) -> str:
    text = raw.strip()
    # Four-digit year is required; ambiguous DD/MM dates are not guessed.
    match = re.fullmatch(r'(\d{4})[-/年]?(\d{2})[-/月]?(\d{2})日?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?', text)
    if not match:
        raise ValueError('日期须包含四位年份，例如 2026-09-01')
    value = date(*(int(part) for part in match.groups()))
    if not date(2000, 1, 1) <= value <= date.today():
        raise ValueError('请核对日期，不能导入未来流水')
    return value.isoformat()


def suggested_category(description: str, kind: str) -> str:
    if kind == 'income':
        rules = [('退款', ('退款', '退货')), ('工资', ('工资', '月薪')), ('奖金', ('奖金', '绩效')),
                 ('理财收益', ('利息', '分红')), ('副业', ('兼职', '稿费', '外包'))]
        fallback = '其他收入'
    else:
        rules = [('住房', ('房租', '物业')), ('生活缴费', ('电费', '水费', '燃气', '宽带')),
                 ('教育学习', ('学费', '教材', '课程')), ('出行', ('地铁', '公交', '打车', '车票')),
                 ('餐饮', ('午餐', '晚餐', '早餐', '咖啡', '餐饮')), ('医疗健康', ('医院', '挂号', '药品')),
                 ('保险', ('保费',)), ('娱乐', ('电影', '会员')), ('购物', ('购买', '购物'))]
        fallback = '其他'
    return next((cat for cat, terms in rules if any(term in description for term in terms)), fallback)


def parse_csv(encoded: str, account_label: str) -> list[dict]:
    try:
        blob = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError('CSV 文件编码无效') from None
    if not blob or len(blob) > MAX_BYTES or b'\x00' in blob:
        raise ValueError('请选择不超过 1 MB 的 CSV 文件')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('文件须为 UTF-8 或 GB18030 编码')
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',\t;')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    if not headers or len(headers) != len(set(headers)) or len(headers) > 50:
        raise ValueError('CSV 表头缺失、重复或过多')
    columns = {key: next((h for h in headers if h in aliases), None) for key, aliases in ALIASES.items()}
    if not columns['date'] or not (columns['amount'] or columns['income'] or columns['expense']):
        raise ValueError('需要日期和金额列；也支持分别列出收入金额与支出金额')
    account = digest(account_label.strip())
    occurrences = Counter()
    rows = []
    for index, raw in enumerate(reader, 1):
        if index > MAX_ROWS:
            raise ValueError('一次最多导入 500 笔，请拆分文件')
        if None in raw or any(value is None for value in raw.values()):
            raise ValueError(f'第 {index} 行列数与表头不一致')
        values = {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}
        if not any(values.values()):
            continue
        value = lambda key: values.get(columns.get(key), '')
        description = value('description')[:512] or '银行流水'
        row = {'row': index, 'event_date': value('date')[:32], 'amount': '',
               'type': KINDS.get(value('type').lower(), ''), 'category': value('category')[:64],
               'description': description, 'currency': 'CNY',
               'movement': MOVEMENTS.get(value('movement'), 'cashflow'), 'enabled': True, 'errors': []}
        try:
            row['event_date'] = statement_date(value('date'))
        except ValueError as error:
            row['errors'].append(str(error))
        if value('currency').upper() not in {'', 'CNY', 'RMB', '人民币', '元'}:
            raise ValueError(f'第 {index} 行不是人民币，请先换算并核对')
        try:
            if value('income') or value('expense'):
                inc, exp = value('income'), value('expense')
                inc = inc if inc and Decimal(inc.replace(',', '')) != 0 else ''
                exp = exp if exp and Decimal(exp.replace(',', '')) != 0 else ''
                if bool(inc) == bool(exp):
                    raise ValueError('收入和支出金额需且只能填一列')
                row['type'] = 'income' if inc else 'expense'
                amount = decimal_amount(inc or exp)
            else:
                signed = value('amount').strip().startswith(('-', '+'))
                amount = decimal_amount(value('amount'), signed=True)
                if signed:
                    inferred = 'expense' if amount < 0 else 'income'
                    if row['type'] and row['type'] != inferred:
                        raise ValueError('金额正负号与收支方向冲突')
                    row['type'] = inferred
            row['amount'] = f'{abs(amount):.2f}'
        except (ValueError, InvalidOperation) as error:
            row['errors'].append(str(error) if isinstance(error, ValueError) else '金额无效')
        if not row['type']:
            row['errors'].append('请选择收入或支出；无符号金额不推测方向')
        allowed = INCOME_CATEGORIES if row['type'] == 'income' else EXPENSE_CATEGORIES
        if row['category'] not in allowed:
            row['category'] = suggested_category(description, row['type'])
        if value('movement') and value('movement') not in MOVEMENTS:
            row['errors'].append('请核对资金性质')
        # Preserve repeats within a statement; do not collapse two identical purchases.
        identity = digest({k: row[k] for k in ('event_date', 'amount', 'type', 'description', 'movement')})
        occurrences[identity] += 1
        txid = value('txid')
        row['import_key'] = digest([account, 'txid', txid]) if txid else digest([account, 'row', identity, occurrences[identity]])
        rows.append(row)
    if not rows:
        raise ValueError('文件中没有可预览的流水')
    keys = [r['import_key'] for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('文件中有重复流水号，请先核对')
    return rows


def is_cashflow(metadata) -> bool:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    return not isinstance(metadata, dict) or metadata.get('movement') not in {'internal_transfer', 'principal'}
