"""Owner-entered wealth inventory. Never posts money or imports bank balances."""
from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Money = Annotated[str, StringConstraints(strict=True, pattern=r'^(0|[1-9]\d{0,9})(\.\d{1,2})?$')]
Revision = Annotated[int, Field(strict=True, ge=0)]
Rate = Annotated[str, StringConstraints(strict=True, pattern=r'^-?(0|[1-9]\d{0,2})(\.\d{1,2})?$')]
ASSETS = {'cash': '活期与现金', 'deposit': '存款', 'fund': '基金', 'stock': '股票',
          'bond': '债券', 'pension': '养老资产', 'property': '房产', 'other': '其他资产'}
DEBTS = {'credit': '信用卡待还', 'loan': '贷款', 'other': '其他负债'}


def today() -> date:
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def fen(value: str) -> int:
    return int(Decimal(value) * 100)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Account(Input):
    kind: Literal['asset', 'liability']
    category: str
    name: str = Field(min_length=1, max_length=40)
    amount: Money
    as_of: date
    available: Money | None = None
    emergency: Money = '0'
    goal: Money = '0'
    other_reserved: Money = '0'
    due: Money | None = None

    @model_validator(mode='after')
    def consistent(self):
        self.name = unicodedata.normalize('NFKC', self.name).strip()
        if not self.name or any(ord(c) < 32 for c in self.name):
            raise ValueError('请填写账户简称')
        if self.category not in (ASSETS if self.kind == 'asset' else DEBTS):
            raise ValueError('请选择对应的资产或负债类别')
        if self.as_of > today():
            raise ValueError('估值日期不能晚于今天')
        cash = self.kind == 'asset' and self.category in {'cash', 'deposit'}
        reserved = sum(fen(v) for v in [self.emergency, self.goal, self.other_reserved])
        if cash:
            if self.due is not None:
                raise ValueError('资产不填写待还金额')
            if self.available is not None and fen(self.available) > fen(self.amount):
                raise ValueError('可立即使用金额不能超过余额')
            if reserved > fen(self.available or '0'):
                raise ValueError('三项预留的合计不能超过可立即使用金额')
        elif self.available is not None or reserved:
            raise ValueError('只有现金和存款可以登记即时可用及预留金额')
        if self.kind == 'asset' and self.due is not None:
            raise ValueError('资产不填写待还金额')
        if self.due is not None and fen(self.due) > fen(self.amount):
            raise ValueError('未来30天待还不能超过未还本金及已计入的利息总额')
        return self


class CreateAccount(Account):
    request_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class UpdateAccount(Account):
    expected_version: Revision


class VersionInput(Input):
    expected_version: Revision


class ConfirmInventory(Input):
    expected_revision: Revision
    complete: Literal[True]


class Scenario(Input):
    principal: Money
    change_percent: Rate
    fee_percent: Rate

    @model_validator(mode='after')
    def ranges(self):
        if not -100 <= Decimal(self.change_percent) <= 100:
            raise ValueError('假设涨跌幅范围为 -100% 至 100%')
        if not 0 <= Decimal(self.fee_percent) <= 100:
            raise ValueError('假设一次性费用范围为 0% 至 100%')
        return self


def scenario(data: Scenario) -> dict:
    principal = fen(data.principal)
    fee = int((Decimal(principal) * Decimal(data.fee_percent) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    invested = principal - fee
    final = int((Decimal(invested) * (1 + Decimal(data.change_percent) / 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return {'principal_minor': principal, 'fee_minor': fee, 'invested_minor': invested,
            'final_minor': final, 'change_minor': final - principal,
            'assumptions': data.model_dump(), 'bank_effect': False,
            'method': '先扣一次性假设费用，再按整个持有期间的假设涨跌计算；不含其他费用、税费或复利。'}


class WealthStore:
    def __init__(self, store):
        self.store = store
        with store._atomic():
            for sql in [
                'CREATE TABLE IF NOT EXISTS wealth_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, confirmed_revision INTEGER, confirmed_on TEXT)',
                'INSERT OR IGNORE INTO wealth_state VALUES (1,0,NULL,NULL)',
                'CREATE TABLE IF NOT EXISTS wealth_accounts (id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)',
                'CREATE TABLE IF NOT EXISTS wealth_requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL, account_id TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS wealth_audit (revision INTEGER PRIMARY KEY, account_id TEXT NOT NULL, before_payload TEXT, after_payload TEXT, changed_on TEXT NOT NULL)',
            ]:
                store._db.execute(sql)

    @staticmethod
    def _payload(data):
        return json.dumps(data.model_dump(mode='json', exclude={'request_id', 'expected_version'}), ensure_ascii=False, sort_keys=True)

    def _entry(self, row):
        return {'id': row['id'], **json.loads(row['payload']), 'version': row['version'], 'source': 'manual'}

    def _unique(self, data, exclude=''):
        for row in self.store._db.execute('SELECT id,payload FROM wealth_accounts WHERE deleted=0'):
            if row['id'] != exclude and json.loads(row['payload'])['name'].casefold() == data.name.casefold():
                raise HTTPException(409, '这个账户简称已经存在，请编辑原记录，避免重复计算')

    def _changed(self, account_id, before, after):
        db = self.store._db
        db.execute('UPDATE wealth_state SET revision=revision+1, confirmed_revision=NULL, confirmed_on=NULL WHERE id=1')
        revision = db.execute('SELECT revision FROM wealth_state WHERE id=1').fetchone()[0]
        db.execute('INSERT INTO wealth_audit VALUES (?,?,?,?,?)', (revision, account_id, before, after, datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()))

    def create(self, data: CreateAccount):
        payload = self._payload(data)
        with self.store._atomic():
            db = self.store._db
            seen = db.execute('SELECT * FROM wealth_requests WHERE id=?', (data.request_id,)).fetchone()
            if seen:
                if seen['payload'] != payload:
                    raise HTTPException(409, '重复请求的内容发生变化，请重新打开登记表')
                row = db.execute('SELECT * FROM wealth_accounts WHERE id=? AND deleted=0', (seen['account_id'],)).fetchone()
                if row is None:
                    raise HTTPException(409, '这条登记已删除，请重新打开登记表')
                return self._entry(row)
            if db.execute('SELECT COUNT(*) FROM wealth_accounts WHERE deleted=0').fetchone()[0] >= 200:
                raise HTTPException(422, '最多登记200个资产或负债账户')
            self._unique(data)
            account_id = str(uuid4())
            db.execute('INSERT INTO wealth_accounts VALUES (?,?,1,0)', (account_id, payload))
            db.execute('INSERT INTO wealth_requests VALUES (?,?,?)', (data.request_id, payload, account_id))
            self._changed(account_id, None, payload)
            return self._entry(db.execute('SELECT * FROM wealth_accounts WHERE id=?', (account_id,)).fetchone())

    def update(self, account_id, data: UpdateAccount | VersionInput, *, delete=False):
        with self.store._atomic():
            db = self.store._db
            row = db.execute('SELECT * FROM wealth_accounts WHERE id=? AND deleted=0', (account_id,)).fetchone()
            if row is None:
                raise HTTPException(404, '这条登记已不存在，请刷新')
            if row['version'] != data.expected_version:
                raise HTTPException(409, '这条登记已更新，请刷新后重新编辑')
            if delete:
                db.execute('UPDATE wealth_accounts SET deleted=1,version=version+1 WHERE id=?', (account_id,))
                after = None
            else:
                self._unique(data, account_id)
                after = self._payload(data)
                db.execute('UPDATE wealth_accounts SET payload=?,version=version+1 WHERE id=?', (after, account_id))
            self._changed(account_id, row['payload'], after)
        return self.snapshot()

    def confirm(self, data: ConfirmInventory):
        with self.store._atomic():
            db = self.store._db
            revision = db.execute('SELECT revision FROM wealth_state WHERE id=1').fetchone()[0]
            if revision != data.expected_revision:
                raise HTTPException(409, '资产负债已变动，请刷新后重新核对')
            db.execute('UPDATE wealth_state SET confirmed_revision=?,confirmed_on=? WHERE id=1', (revision, today().isoformat()))
        return self.snapshot()

    def snapshot(self):
        with self.store._lock:
            state = dict(self.store._db.execute('SELECT * FROM wealth_state WHERE id=1').fetchone())
            entries = [self._entry(r) for r in self.store._db.execute('SELECT * FROM wealth_accounts WHERE deleted=0 ORDER BY rowid')]
        stamp = today()
        assets = debts = immediate = emergency = goal = reserved = due = 0
        missing, stale, allocation = [], [], {}
        for entry in entries:
            amount = fen(entry['amount'])
            if (stamp - date.fromisoformat(entry['as_of'])).days > 30:
                stale.append(entry['id'])
            if entry['kind'] == 'asset':
                assets += amount
                allocation[entry['category']] = allocation.get(entry['category'], 0) + amount
                if entry['category'] in {'cash', 'deposit'}:
                    if entry['available'] is None:
                        missing.append(entry['id'])
                    immediate += fen(entry['available'] or '0')
                    emergency += fen(entry['emergency'])
                    goal += fen(entry['goal'])
                    reserved += fen(entry['other_reserved'])
            else:
                debts += amount
                if entry['due'] is None:
                    missing.append(entry['id'])
                due += fen(entry['due'] or '0')
        confirmed = state['confirmed_revision'] == state['revision'] and state['confirmed_on'] is not None
        current = not stale and (not confirmed or (stamp-date.fromisoformat(state['confirmed_on'])).days <= 30)
        ready = confirmed and current and not missing
        known = bool(entries) or confirmed
        free = immediate - emergency - goal - reserved - due
        return {'currency': 'CNY', 'source': 'manual', 'bank_effect': False, 'today': stamp.isoformat(),
                'revision': state['revision'], 'confirmed': confirmed, 'confirmed_on': state['confirmed_on'],
                'current': current, 'ready': ready, 'missing_ids': missing, 'stale_ids': stale,
                'accounts': entries, 'asset_categories': ASSETS, 'liability_categories': DEBTS,
                'assets_minor': assets if known else None, 'liabilities_minor': debts if known else None,
                'net_minor': assets-debts if known else None,
                'available_minor': max(0, free) if ready else None,
                'shortfall_minor': max(0, -free) if ready else None,
                'breakdown': {'immediate_minor': immediate, 'emergency_minor': emergency,
                              'goal_minor': goal, 'other_reserved_minor': reserved, 'due_minor': due},
                'allocation': [{'category': key, 'label': ASSETS[key], 'amount_minor': value} for key, value in allocation.items()]}
