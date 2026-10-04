"""Personal finance BFF. Real MOSAIC control plane, explicitly simulated bank.

The model may propose a draft. Only a separately submitted parameter form is
signed as user input. Confirmation and MFA are verified here, never by a model.
All money movement, including simulated investment principal, uses the gateway.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import struct
import sys
import time
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from llm_runtime import api_chat
from accounting_schema import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from bank_import import decimal_amount, parse_csv, statement_date
from finance_schema import INTENT_PROMPT, PLAN_PROMPT, plan_facts, plan_target, validate_plan_selection, foreign_currency_pending
from learning_loop import LearningLoop

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'vendor/mosaic_guard/src'))
from scns_guard.auth import ControlClaims, CredentialAuthority, IdentityClaims
from scns_guard.canonical import canonical_json, sha256_hex
from scns_guard.causal import AgentTrace, SourceMessage
from scns_guard.deployment import ServiceConfig, _private, initialize
from scns_guard.durable_bank import DurableBankLedger
from scns_guard.enums import SourceKind, SourceTrust
from scns_guard.gateway import ReviewRequest, SafetyGateway
from scns_guard.lineage import LineageAuthority
from scns_guard.policy import PolicyEngine
from scns_guard.runtime_store import RuntimeStore
from scns_guard.simulator import BankAccount
from scns_guard.trust import FactAuthority

COOKIE = 'qingcai_finance'
ACTOR = 'user-1'  # Single-owner private demo; not a multi-tenant bank login.
Amount = Annotated[str, StringConstraints(strict=True, pattern=r'^(0|[1-9]\d{0,7})(\.\d{1,2})?$')]
Code = Annotated[str, StringConstraints(strict=True, pattern=r'^\d{6}$')]
PRODUCTS = (
    {'id': 'reserve', 'name': '灵活现金', 'account': 'fund-reserve', 'risk': 1,
     'liquidity': '随时赎回', 'horizon_months': 0, 'fee': '0', 'purpose': '应急金与近期目标'},
    {'id': 'growth', 'name': '长期均衡', 'account': 'fund-growth', 'risk': 3,
     'liquidity': '体验中即时赎回', 'horizon_months': 12, 'fee': '0', 'purpose': '一年以上的长期资金'},
)
RECIPIENTS = (
    {'id': 'acct-alice', 'name': '小林', 'phone': '13800000001', 'remark': '朋友'},
    {'id': 'acct-landlord', 'name': '房东', 'phone': '13800000002', 'remark': '房租'},
)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Profile(StrictInput):
    ledger_scope: Literal['demo', 'personal'] = 'demo'
    stage: Literal['unknown', 'student', 'early_career', 'freelance'] = 'unknown'
    goal_name: str = Field(default='', max_length=40)
    goal_amount: Amount = '0'
    goal_months: int = Field(default=12, strict=True, ge=1, le=60)
    reserve_months: int = Field(default=3, strict=True, ge=1, le=6)
    commitment_amount: Amount = '0'
    risk_answers: list[Annotated[int, Field(strict=True, ge=0, le=2)]] = Field(default_factory=list, max_length=4)
    goal_recorded_amount: Amount = '0'
    goal_percent: int = Field(default=20, strict=True, ge=1, le=100)
    goal_cycle: Literal['week', 'month', 'year'] = 'month'
    goal_id: str = Field(default='legacy', min_length=1, max_length=64)
    cashflow_confirmed: bool = Field(default=False, strict=True)
    debt_monthly_amount: Amount = '0'

    @model_validator(mode='after')
    def complete_questionnaire(self):
        if len(self.risk_answers) not in {0, 4}:
            raise ValueError('请完成全部四个风险偏好问题')
        if minor(self.goal_amount) > 0 and not self.goal_name.strip():
            raise ValueError('请为目标起一个名字')
        return self


class ProfileUpdate(Profile):
    goal_action: Literal['keep', 'replace', 'carry'] | None = None


class OperationInput(StrictInput):
    kind: Literal['transfer', 'subscribe', 'redeem']
    amount: Amount
    recipient_id: str | None = Field(default=None, max_length=64)
    product_id: str | None = Field(default=None, max_length=64)
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    feedback_ref: str | None = Field(default=None, max_length=80)


class Confirmation(StrictInput):
    challenge: str = Field(min_length=16, max_length=80)
    code: Code | None = None


class MFAInput(StrictInput):
    code: Code


class ConversationMessage(StrictInput):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=2000)


class FinanceConversation(StrictInput):
    messages: list[ConversationMessage] = Field(min_length=1, max_length=12)


class CSVPreview(StrictInput):
    content: str = Field(min_length=1, max_length=1_400_000)
    account_label: str = Field(default='默认账户', min_length=1, max_length=40)


class ImportedRow(StrictInput):
    row: int = Field(strict=True, ge=1, le=500)
    event_date: str = Field(min_length=8, max_length=32)
    type: Literal['expense', 'income']
    amount: Amount
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    movement: Literal['cashflow', 'internal_transfer', 'principal'] = 'cashflow'

    @model_validator(mode='after')
    def valid_bill(self):
        self.event_date = statement_date(self.event_date)
        decimal_amount(self.amount)
        allowed = INCOME_CATEGORIES if self.type == 'income' else EXPENSE_CATEGORIES
        if self.category not in allowed:
            raise ValueError('分类与收支方向不一致')
        return self


class CSVCommit(StrictInput):
    challenge: str = Field(min_length=16, max_length=80)
    rows: list[ImportedRow] = Field(min_length=1, max_length=500)


class GoalProgress(StrictInput):
    amount: Amount
    request_id: str = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    goal_id: str | None = Field(default=None, max_length=64)


class LearningConsent(StrictInput):
    enabled: bool = Field(strict=True)


class FeedbackReview(StrictInput):
    approve: bool = Field(strict=True)


class ProtectionRecovery(StrictInput):
    challenge: str = Field(min_length=16, max_length=80)
    code: Code


class BankIntent(StrictInput):
    intent: Literal['transfer', 'subscribe', 'redeem', 'plan', 'unsupported']
    recipient: str | None = Field(default=None, max_length=64)
    product_id: Literal['reserve', 'growth'] | None = None
    amount: Amount | None = None

    @model_validator(mode='after')
    def slots_match_intent(self):
        if self.intent=='transfer' and self.product_id is not None:
            raise ValueError('转账不能带产品参数')
        if self.intent in {'subscribe','redeem'} and self.recipient is not None:
            raise ValueError('产品操作不能带收款人参数')
        if self.intent in {'plan','unsupported'} and any(value is not None for value in (self.recipient,self.product_id,self.amount)):
            raise ValueError('说明不能包含资金操作参数')
        return self


def minor(value: str) -> int:
    return int(Decimal(value) * 100)


def bill_minor(value) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def month_shift(day: date, offset: int) -> date:
    index = day.year * 12 + day.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def cashflow(rows: list[dict]) -> dict:
    """Six completed months. Refunds offset expenses, internal principal is excluded."""
    anchor = max((date.fromisoformat(r['event_date'][:10]) for r in rows), default=date.today())
    # A September statement imported in October includes September. The current
    # calendar month remains incomplete; missing months never count as observations.
    end = min(date.today().replace(day=1), month_shift(anchor, 1))
    start = month_shift(end, -6)
    buckets = {month_shift(start, i).strftime('%Y-%m'): {'income_minor': 0, 'expense_minor': 0,
                'refund_minor': 0, 'fixed_minor': 0, 'count': 0} for i in range(6)}
    for r in rows:
        if r.get('currency') != 'CNY':
            continue
        metadata = r.get('metadata') or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (ValueError, TypeError):
                metadata = {}
        if not isinstance(metadata, dict) or metadata.get('movement') in {'internal_transfer', 'principal'}:
            continue
        key = r['event_date'][:7]
        if key not in buckets:
            continue
        item = buckets[key]
        amount = bill_minor(r['amount'])
        if amount <= 0:
            continue
        item['count'] += 1
        if r['category'] == '退款' and r['type'] == 'income':
            item['refund_minor'] += amount
        elif r['type'] == 'income':
            item['income_minor'] += amount
        elif r['type'] == 'expense':
            item['expense_minor'] += amount
            if r['category'] in {'住房', '生活缴费', '保险', '教育学习'}:
                item['fixed_minor'] += amount
    months = [{'month': key, **value, 'gross_expense_minor': value['expense_minor'],
               'expense_minor': value['expense_minor'] - value['refund_minor']}
              for key, value in buckets.items()]
    present = [x for x in months if x['count']]
    n = len(present) or 1
    income = sum(x['income_minor'] for x in present) // n
    expense = sum(x['expense_minor'] for x in present) // n
    income_values = [x['income_minor'] for x in present]
    variability = (max(income_values) - min(income_values)) / income if income else None
    return {'start_date': start.isoformat(), 'end_date': (end - timedelta(days=1)).isoformat(), 'months': months,
            'observed_months': len(present), 'monthly_income_minor': income,
            'expense_months': sum(x['gross_expense_minor'] > 0 for x in present),
            'monthly_expense_minor': expense, 'monthly_net_minor': income - expense,
            'monthly_gross_expense_minor': sum(x['gross_expense_minor'] for x in present) // n,
            'fixed_expense_minor': sum(x['fixed_minor'] for x in present) // n,
            'income_stability': '待补充' if len(present) < 3 else '较稳定' if variability is not None and variability <= .5 else '有波动'}


def ledger_planning(profile: Profile, flow: dict) -> dict:
    """A read-only monthly plan; ledger entries never create bank balances.

    Goal progress is user-recorded, not verified cash. Emergency coverage is a
    stock target whose actual funding is unknown. Neither is trading authority.
    """
    gross = flow['monthly_gross_expense_minor']
    commitment = minor(profile.commitment_amount)
    extra = max(0, commitment - gross)
    debt = minor(profile.debt_monthly_amount)
    available = max(0, flow['monthly_net_minor'] - extra - debt)
    target = minor(profile.goal_amount)
    recorded = minor(profile.goal_recorded_amount)
    remaining = max(0, target - recorded)
    required = (remaining + profile.goal_months - 1) // profile.goal_months
    allocated = min(available, required)
    has_expenses = flow.get('expense_months', 0) >= 3 or commitment > 0
    ready = flow['observed_months'] >= 3 and has_expenses and profile.cashflow_confirmed
    return {
        'currency': 'CNY', 'bank_effect': False,
        'ready': ready,
        'quality': {'has_expenses': has_expenses, 'confirmed': profile.cashflow_confirmed,
                    'expense_months': flow.get('expense_months', 0),
                    'message': '先补充至少三个月收支' if flow['observed_months'] < 3 else
                               '请补充生活开销' if not has_expenses else
                               '请核对收支是否完整' if not profile.cashflow_confirmed else ''},
        'basis': {'kind': 'observed_monthly_average', 'scope': profile.ledger_scope,
                  'start_date': flow['start_date'], 'end_date': flow['end_date'],
                  'observed_months': flow['observed_months']},
        'monthly': {'income_minor': flow['monthly_income_minor'],
                    'net_expense_minor': flow['monthly_expense_minor'],
                    'gross_expense_minor': gross, 'net_minor': flow['monthly_net_minor'],
                    'commitment_extra_minor': extra, 'debt_minor': debt, 'available_minor': available},
        'goal': {'target_minor': target, 'recorded_minor': recorded,
                 'remaining_minor': remaining, 'months': profile.goal_months,
                 'monthly_required_minor': required, 'monthly_allocated_minor': allocated,
                 'shortfall_minor': required - allocated, 'feasible': required <= available},
        'emergency': {'target_minor': (max(gross, commitment) + debt) * profile.reserve_months,
                      'recorded_minor': None, 'gap_minor': None, 'status': 'unconfirmed' if gross or commitment else 'needs_expenses'},
        'after_goal_minor': max(0, available - required),
    }


def bill_checks(rows: list[dict]) -> list[dict]:
    """Review candidates, not fraud verdicts or automatic deletion instructions."""
    from bank_import import is_cashflow
    end=max((r['event_date'][:10] for r in rows),default=date.today().isoformat())
    start=(date.fromisoformat(end)-timedelta(days=90)).isoformat()
    recent=[r for r in rows if r.get('id') and r['event_date'][:10]>=start and r.get('currency')=='CNY' and is_cashflow(r.get('metadata'))]
    groups={}
    for row in recent:
        if row['type']=='expense':groups.setdefault(row['category'],[]).append(bill_minor(row['amount']))
    candidates=[]
    for row in sorted(recent,key=lambda r:r['event_date'],reverse=True):
        values=groups.get(row['category'],[])
        amount=bill_minor(row['amount'])
        if row['type']=='expense' and len(values)>=5 and amount>max(20_000,median(values)*4):
            candidates.append({'id':row['id'],'event_date':row['event_date'][:10],'category':row['category'],
                'amount_minor':amount,'reason':'高于近 90 天同类记录的通常金额，请核对'})
    return candidates[:6]


class FinanceWorkspace:
    def __init__(self, state_dir: Path, read_bills, import_bills=None):
        self.read_bills = read_bills
        self.import_bills = import_bills
        self.lock = RLock()
        policy = ROOT / 'vendor/mosaic_guard/configs/transfer_policy.yaml'
        config_path = state_dir / 'service.json'
        if not state_dir.exists():
            initialize(state_dir, policy_path=policy)
        _private(state_dir, directory=True)
        _private(config_path)
        config = ServiceConfig.model_validate_json(config_path.read_text())
        if config.policy_sha256 != hashlib.sha256(Path(config.policy_path).read_bytes()).hexdigest():
            raise ValueError('安全策略版本已变化，需要审核后迁移')
        keys = {}
        for name in ('identity', 'approval', 'lineage', 'token', 'bank', 'audit'):
            path = state_dir / f'{name}.key'
            _private(path)
            keys[name] = bytes.fromhex(path.read_text().strip())
        if len(set(keys.values())) != 6 or any(len(k) != 32 for k in keys.values()):
            raise ValueError('安全密钥角色不独立')
        for name in ('runtime.sqlite3', 'bank.sqlite3', 'bank.sqlite3.lock'):
            path = state_dir / name
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            _private(path)
        self.store = RuntimeStore(state_dir / 'runtime.sqlite3')
        anchor = sha256_hex({n: hashlib.sha256(k).hexdigest() for n, k in keys.items()})
        with self.store._atomic():
            self.store._db.execute('CREATE TABLE IF NOT EXISTS trust_anchor (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)')
            self.store._db.execute('INSERT OR IGNORE INTO trust_anchor VALUES (1,?)', (anchor,))
            if self.store._db.execute('SELECT digest FROM trust_anchor WHERE id=1').fetchone()[0] != anchor:
                raise ValueError('运行时信任密钥已变化')
        self.bank = DurableBankLedger(state_dir / 'bank.sqlite3', [
            BankAccount('acct-user', ACTOR, 5_000_000), BankAccount('acct-alice', 'alice', 10_000),
            BankAccount('acct-landlord', 'landlord', 0), BankAccount('acct-mallory', 'mallory', 0),
            *(BankAccount(p['account'], ACTOR, 0) for p in PRODUCTS)], blocked_recipients={'acct-mallory'})
        facts = FactAuthority({'bank-core': keys['bank']})
        self.gateway = SafetyGateway(store=self.store, policy=PolicyEngine.from_yaml(config.policy_path),
            fact_authority=facts, tool=self.bank, fact_supplier=lambda actor: self.bank.issue_facts(actor, facts),
            lineage_authority=LineageAuthority({'frontend': keys['lineage']}),
            identity_authority=CredentialAuthority(issuer='login', audience='guard', keys={'v1': keys['identity']}),
            control_authority=CredentialAuthority(issuer='approval', audience='guard', keys={'v1': keys['approval']}, max_ttl=120),
            token_secret=keys['token'])
        self.gateway.audit_secret = keys['audit']
        with self.bank.transaction():
            self.store.recover_unresolved(self.gateway.circuit.namespace)
        with self.store._atomic():
            schema = '''
                CREATE TABLE IF NOT EXISTS app_sessions (token_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS app_profile (actor TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS app_operations (handle TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, input_digest TEXT NOT NULL, meta TEXT NOT NULL,
                    result TEXT NOT NULL, challenge TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL,
                    UNIQUE(session_id,request_id));
                CREATE TABLE IF NOT EXISTS app_mfa (actor TEXT PRIMARY KEY, secret TEXT, pending TEXT,
                    pending_session TEXT, pending_expires REAL, last_counter INTEGER NOT NULL DEFAULT -1,
                    failed INTEGER NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS app_imports (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    payload TEXT NOT NULL, challenge TEXT NOT NULL, expires REAL NOT NULL,
                    submitted TEXT, result TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS app_goal_progress (request_id TEXT PRIMARY KEY, amount TEXT NOT NULL,
                    result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS app_recovery (session_id TEXT PRIMARY KEY, challenge TEXT NOT NULL,
                    generation INTEGER NOT NULL, expires REAL NOT NULL);
            '''
            for statement in schema.split(';'):
                if statement.strip():
                    self.store._db.execute(statement)
            columns = {row['name'] for row in self.store._db.execute('PRAGMA table_info(app_goal_progress)')}
            for name, definition in [('goal_id', "TEXT NOT NULL DEFAULT 'legacy'"), ('created', 'REAL NOT NULL DEFAULT 0'), ('undone', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    self.store._db.execute(f'ALTER TABLE app_goal_progress ADD COLUMN {name} {definition}')
        self.learning = LearningLoop(self.store, ROOT)
        self.closed = False

    def close(self):
        if not self.closed:
            self.bank.close()
            self.store.close()
            self.closed = True

    def start_session(self, request: Request, response: Response) -> dict:
        raw = request.cookies.get(COOKIE, '')
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        with self.store._atomic():
            row = self.store._db.execute('SELECT * FROM app_sessions WHERE token_hash=? AND expires>?', (hashed, time.time())).fetchone()
            if row is None:
                raw, csrf, sid = secrets.token_urlsafe(32), secrets.token_urlsafe(32), str(uuid4())
                self.store._db.execute('INSERT INTO app_sessions VALUES (?,?,?,?)',
                    (hashlib.sha256(raw.encode()).hexdigest(), sid, csrf, time.time() + 28800))
            else:
                csrf, sid = row['csrf'], row['session_id']
        response.set_cookie(COOKIE, raw, httponly=True, samesite='strict', max_age=28800,
                            secure=request.headers.get('Origin', '').startswith('https:'))
        response.headers['Cache-Control'] = 'no-store'
        return {'csrf': csrf, 'session_id': sid, 'mode': 'private_demo'}

    def session(self, request: Request) -> str:
        raw = request.cookies.get(COOKIE, '')
        with self.store._lock:
            row = self.store._db.execute('SELECT * FROM app_sessions WHERE token_hash=? AND expires>?',
                (hashlib.sha256(raw.encode()).hexdigest(), time.time())).fetchone()
        if row is None:
            raise HTTPException(401, '访问会话已失效，请刷新页面')
        if request.method not in {'GET', 'HEAD'}:
            origins = os.getenv('FINANCE_PUBLIC_ORIGINS', f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', '5500')},http://localhost:{os.getenv('FRONTEND_PORT', '5500')}").split(',')
            if request.headers.get('Origin') not in origins or not hmac.compare_digest(request.headers.get('X-Qingcai-CSRF', ''), row['csrf']):
                raise HTTPException(403, '请在FinPilot页面内确认此操作')
        return row['session_id']

    def credential(self, sid: str, *, operator=False) -> str:
        with self.store._lock:
            expires = self.store._db.execute('SELECT MAX(expires) FROM app_sessions WHERE session_id=?', (sid,)).fetchone()[0]
        remaining = int((expires or 0) - time.time())
        if remaining < 2:
            raise HTTPException(401, '访问会话已失效，请刷新页面')
        scopes = ('audit:read', 'operator:reset') if operator else ('agent:review', 'agent:execute', 'human:confirm')
        return self.gateway.identity_authority.issue(IdentityClaims(actor_id=ACTOR, session_id=sid, scopes=scopes), key_id='v1', ttl=min(120, remaining))

    def profile(self) -> Profile:
        with self.store._lock:
            row = self.store._db.execute('SELECT payload FROM app_profile WHERE actor=?', (ACTOR,)).fetchone()
        return Profile.model_validate_json(row[0]) if row else Profile()

    def save_profile(self, profile: Profile):
        with self.lock:
            old = self.profile()
            action = getattr(profile, 'goal_action', None)
            if profile.goal_id != old.goal_id:
                raise HTTPException(409, '目标已更换，请重新打开计划')
            changed = old.goal_name and profile.goal_name != old.goal_name
            if changed and action is None:
                raise HTTPException(409, '请确认是修改原目标还是替换为新目标')
            values = profile.model_dump(exclude={'goal_action'})
            if action in {'replace', 'carry'} or old.goal_name and not profile.goal_name or not old.goal_name and profile.goal_name:
                values['goal_id'] = str(uuid4())
                values['goal_recorded_amount'] = profile.goal_recorded_amount if action == 'carry' else '0'
            else:
                values['goal_recorded_amount'] = old.goal_recorded_amount
            if profile.ledger_scope != old.ledger_scope:
                values['cashflow_confirmed'] = False
            profile = Profile.model_validate(values)
            with self.store._atomic():
                self.store._db.execute('INSERT OR REPLACE INTO app_profile VALUES (?,?)', (ACTOR, profile.model_dump_json()))
            return self.overview()

    def preview_import(self, sid: str, data: CSVPreview) -> dict:
        try:
            rows = parse_csv(data.content, data.account_label)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        handle, challenge = str(uuid4()), secrets.token_urlsafe(32)
        with self.store._atomic():
            self.store._db.execute('DELETE FROM app_imports WHERE expires<? AND submitted IS NULL', (time.time(),))
            count = self.store._db.execute('SELECT COUNT(*) FROM app_imports WHERE session_id=? AND created>?',
                                          (sid, time.time() - 60)).fetchone()[0]
            if count >= 5:
                raise HTTPException(429, '预览过于频繁，请稍后再试')
            self.store._db.execute('INSERT INTO app_imports VALUES (?,?,?,?,?,NULL,NULL,?)',
                (handle, sid, canonical_json(rows), challenge, time.time() + 1800, time.time()))
        # Keys are private deduplication data, not editable client authority.
        return {'id': handle, 'challenge': challenge, 'rows': [{k: v for k, v in r.items() if k != 'import_key'} for r in rows],
                'categories': {'income': INCOME_CATEGORIES, 'expense': EXPENSE_CATEGORIES}}

    def record_goal_progress(self, data: GoalProgress) -> dict:
        if minor(data.amount) <= 0:
            raise HTTPException(422, '记录金额至少为 0.01 元')
        with self.lock, self.store._atomic():
            profile = self.profile()
            if data.goal_id is not None and data.goal_id != profile.goal_id:
                raise HTTPException(409, '目标已更换，请重新记录')
            old = self.store._db.execute('SELECT * FROM app_goal_progress WHERE request_id=?', (data.request_id,)).fetchone()
            if old:
                if old['goal_id'] != profile.goal_id or old['undone']:
                    raise HTTPException(409, '这条进度已撤销或属于其他目标')
                if minor(old['amount']) != minor(data.amount):
                    raise HTTPException(409, '同一记录已绑定其他金额')
                return json.loads(old['result'])
            if not profile.goal_name or minor(profile.goal_amount) <= 0:
                raise HTTPException(409, '请先设置目标')
            total = minor(profile.goal_recorded_amount) + minor(data.amount)
            if total > 9_999_999_999:
                raise HTTPException(422, '记录金额超过支持范围')
            profile = profile.model_copy(update={'goal_recorded_amount': f'{total // 100}.{total % 100:02d}'})
            self.store._db.execute('INSERT OR REPLACE INTO app_profile VALUES (?,?)', (ACTOR, profile.model_dump_json()))
            result = {'goal_recorded_amount': profile.goal_recorded_amount, 'bank_effect': False}
            self.store._db.execute('INSERT INTO app_goal_progress (request_id,amount,result,goal_id,created) VALUES (?,?,?,?,?)',
                                   (data.request_id, data.amount, canonical_json(result), profile.goal_id, time.time()))
            return result

    def goal_history(self):
        profile = self.profile()
        with self.store._lock:
            rows = self.store._db.execute('SELECT request_id,amount,created,undone FROM app_goal_progress WHERE goal_id=? ORDER BY created DESC LIMIT 50', (profile.goal_id,)).fetchall()
        return {'goal_id': profile.goal_id, 'records': [dict(row) for row in rows]}

    def undo_goal_progress(self, request_id: str):
        with self.lock, self.store._atomic():
            profile = self.profile()
            row = self.store._db.execute('SELECT * FROM app_goal_progress WHERE request_id=? AND goal_id=?', (request_id, profile.goal_id)).fetchone()
            if not row:
                raise HTTPException(404, '当前目标没有这条进度')
            if not row['undone']:
                total = max(0, minor(profile.goal_recorded_amount) - minor(row['amount']))
                profile = profile.model_copy(update={'goal_recorded_amount': f'{total // 100}.{total % 100:02d}'})
                self.store._db.execute('INSERT OR REPLACE INTO app_profile VALUES (?,?)', (ACTOR, profile.model_dump_json()))
                self.store._db.execute('UPDATE app_goal_progress SET undone=1 WHERE request_id=?', (request_id,))
            return {'goal_recorded_amount': profile.goal_recorded_amount, 'bank_effect': False}

    def commit_import(self, sid: str, handle: str, data: CSVCommit) -> dict:
        if self.import_bills is None:
            raise HTTPException(503, '流水导入暂不可用')
        with self.lock:
            with self.store._lock:
                row = self.store._db.execute('SELECT * FROM app_imports WHERE id=? AND session_id=?', (handle, sid)).fetchone()
            if row is None:
                raise HTTPException(404, '请在当前会话重新预览文件')
            if not hmac.compare_digest(data.challenge, row['challenge']):
                raise HTTPException(409, '导入确认无效')
            items = [item.model_dump() for item in data.rows]
            submitted = canonical_json(items)
            if row['submitted'] and row['submitted'] != submitted:
                raise HTTPException(409, '这次导入已绑定其他内容，请核对入账结果')
            if row['result']:
                return json.loads(row['result'])
            if row['expires'] < time.time() and not row['submitted']:
                raise HTTPException(409, '预览已过期，请重新选择文件')
            source = {item['row']: item for item in json.loads(row['payload'])}
            if len({item['row'] for item in items}) != len(items) or any(item['row'] not in source for item in items):
                raise HTTPException(422, '导入行号重复或不属于本次预览')
            records = []
            for item in items:
                records.append({**item, 'currency': 'CNY', 'source': 'bank_import', 'metadata': {
                    'bank_import_key': source[item['row']]['import_key'], 'movement': item['movement'],
                    'payment_method': '银行卡', 'import_batch': handle}})
            with self.store._atomic():
                self.store._db.execute('UPDATE app_imports SET submitted=? WHERE id=?', (submitted, handle))
            result = self.import_bills(records)  # atomic ledger batch + stable deduplication keys
            result = {**result, 'id': handle, 'status': 'imported'}
            with self.store._atomic():
                self.store._db.execute('UPDATE app_imports SET result=?,payload=? WHERE id=?',
                    (canonical_json(result), '[]', handle))
                # Synthetic demo income must never influence a personal recommendation.
                profile = self.profile().model_copy(update={'ledger_scope': 'personal', 'cashflow_confirmed': False})
                self.store._db.execute('INSERT OR REPLACE INTO app_profile VALUES (?,?)', (ACTOR, profile.model_dump_json()))
            return result

    def overview(self) -> dict:
        profile = self.profile()
        rows = self.read_bills()
        flow = cashflow(rows)
        planning = ledger_planning(profile, flow)
        snap = self.bank.snapshot()
        cash = snap['accounts']['acct-user']['balance_minor']
        reserve_holding = snap['accounts']['fund-reserve']['balance_minor']
        # A one-off refund increases cashflow, but does not reduce recurring needs.
        reserve = planning['emergency']['target_minor']
        goal = minor(profile.goal_amount)
        # Manual goal progress is not verified bank funding and cannot release
        # capital for simulated purchases. Keep the original full-goal buffer.
        goal_buffer = goal if profile.goal_months <= 12 else (goal * 3 + profile.goal_months - 1) // profile.goal_months
        investable = max(0, cash + reserve_holding - reserve - goal_buffer)
        completed = len(profile.risk_answers) == 4
        if not planning['ready'] or planning['monthly']['available_minor'] <= 0 or not completed:
            investable = 0
        score = sum(profile.risk_answers)
        risk = 1 if not completed or score <= 2 else 2 if score <= 5 else 3
        if completed:
            # A high total cannot override an explicit short horizon or inability
            # to bear loss. The questionnaire is a prototype preference aid.
            risk = min(risk, profile.risk_answers[0]+1, profile.risk_answers[2]+1,
                       2 if profile.risk_answers[1] == 0 else 3)
        products = [{**p, 'holding_minor': snap['accounts'][p['account']]['balance_minor'],
                     'eligible': p['risk'] <= risk and (p['risk'] == 1 or (flow['observed_months'] >= 3 and flow['monthly_net_minor'] > 0 and investable > 0))}
                    for p in PRODUCTS]
        reasons = []
        stage_reason = {'student':'在校阶段，优先核对学费与生活费的近期安排',
                        'early_career':'初入职场，先为固定开销与近期大额支出留出空间',
                        'freelance':'自由职业阶段，按不同收入月份复核预留资金'}.get(profile.stage)
        if stage_reason:
            reasons.append({'id':'stage','text':stage_reason})
        if flow['observed_months'] < 3:
            reasons.append({'id': 'history', 'text': '先补齐三个月账单，暂不推荐长期产品'})
        elif not planning['ready']:
            reasons.append({'id': 'coverage', 'text': planning['quality']['message'] + '，再比较长期方案'})
        if cash + reserve_holding < reserve:
            reasons.append({'id': 'reserve', 'text': '先补足应急金，资金保持灵活'})
        if planning['goal']['remaining_minor']:
            reasons.append({'id': 'goal', 'text': f'为「{profile.goal_name}」优先留出目标资金'})
            if not planning['goal']['feasible']:
                reasons.append({'id':'goal_capacity','text':'目标的每月计划超过可安排的月均结余，可延长期限或调整目标金额'})
        if not completed:
            reasons.append({'id': 'risk', 'text': '完成风险偏好后，再考虑长期配置'})
        if investable and completed and products[1]['eligible']:
            reasons.append({'id': 'longterm', 'text': '应急金与目标资金预留后，可以比较长期方案'})
        if not reasons:
            reasons.append({'id': 'liquid', 'text': '按目前资金安排，优先使用灵活现金'})
        with self.store._lock:
            mfa = self.store._db.execute('SELECT secret FROM app_mfa WHERE actor=?', (ACTOR,)).fetchone()
            operations = self.store._db.execute('SELECT * FROM app_operations ORDER BY created DESC LIMIT 12').fetchall()
        return {'mode': 'simulated_bank', 'cash_minor': cash,
                'assets_minor': cash + sum(p['holding_minor'] for p in products), 'profile': profile.model_dump(),
                'cashflow': flow, 'planning': planning,
                'reserve_target_minor': reserve, 'reserve_available_minor': cash + reserve_holding,
                'goal_buffer_minor': goal_buffer, 'goal_monthly_minor': planning['goal']['monthly_required_minor'],
                'goal_feasible': planning['goal']['feasible'],
                'investable_minor': min(cash, investable), 'risk_level': risk, 'risk_completed': completed,
                'products': products, 'recipients': list(RECIPIENTS), 'reasons': reasons,
                'bill_checks': bill_checks(rows),
                'mfa_enabled': bool(mfa and mfa['secret']), 'policy_version': '0.2.2',
                'protection_locked': self.gateway.circuit.snapshot(ACTOR)['locked'],
                'operations': [self.public_operation(row, include_challenge=False) for row in operations]}

    def resolve_params(self, data: OperationInput) -> tuple[dict, dict]:
        amount = minor(data.amount)
        if not 1 <= amount <= 1_000_000:
            raise HTTPException(422, '单笔体验金额应为 0.01–10,000 元')
        if data.kind == 'transfer':
            recipient = next((r for r in RECIPIENTS if r['id'] == data.recipient_id), None)
            if recipient is None or data.product_id is not None:
                raise HTTPException(422, '请选择已核对的收款人')
            return {'from_account': 'acct-user', 'to_account': recipient['id'], 'amount_minor': amount}, {
                'kind': data.kind, 'label': f"转给{recipient['name']}", 'target_name': recipient['name'], 'target_detail': recipient['phone']}
        product = next((p for p in PRODUCTS if p['id'] == data.product_id), None)
        if product is None or data.recipient_id is not None:
            raise HTTPException(422, '请选择当前体验产品')
        overview = self.overview()
        current = next(p for p in overview['products'] if p['id'] == product['id'])
        if data.kind == 'subscribe':
            if not current['eligible']:
                raise HTTPException(409, '当前风险偏好或资金安排不适合此产品')
            capacity = overview['cash_minor'] if product['risk'] == 1 else overview['investable_minor']
            if amount > capacity:
                raise HTTPException(409, '金额超过当前可安排资金，请先保留应急金与目标资金')
            source, target, label = 'acct-user', product['account'], '申购'
        else:
            if amount > current['holding_minor']:
                raise HTTPException(409, '金额超过当前持有本金')
            source, target, label = product['account'], 'acct-user', '赎回'
        return {'from_account': source, 'to_account': target, 'amount_minor': amount}, {
            'kind': data.kind, 'product_id': product['id'], 'label': label + product['name'],
            'target_name': product['name'] if data.kind == 'subscribe' else 'FinPilot体验账户',
            'target_detail': '模拟本金划转', 'input': data.model_dump()}

    def public_operation(self, row, *, include_challenge=True) -> dict:
        result, meta = json.loads(row['result']), json.loads(row['meta'])
        output = {'handle': row['handle'], **result, **meta, 'created_at': row['created']}
        if include_challenge:
            output.update(challenge=row['challenge'], challenge_expires=row['expires'])
        return output

    def operation_row(self, sid: str, handle: str):
        with self.store._lock:
            row = self.store._db.execute('SELECT * FROM app_operations WHERE handle=? AND session_id=?', (handle, sid)).fetchone()
        if row is None:
            raise HTTPException(404, '此操作不属于当前会话')
        return row

    def owner_history_row(self, handle: str):
        # The BFF's private-entry session authenticates the single prototype owner.
        # It grants read/reconcile only; it never adopts another action's session.
        with self.store._lock:
            row = self.store._db.execute('SELECT * FROM app_operations WHERE handle=?', (handle,)).fetchone()
        if row is None:
            raise HTTPException(404, '操作不存在')
        return row

    def review(self, sid: str, data: OperationInput) -> dict:
        digest = sha256_hex(data.model_dump())
        with self.lock:
            with self.store._lock:
                old = self.store._db.execute('SELECT * FROM app_operations WHERE session_id=? AND request_id=?', (sid, data.request_id)).fetchone()
                count = self.store._db.execute('SELECT COUNT(*) FROM app_operations WHERE session_id=? AND created>?', (sid, time.time() - 60)).fetchone()[0]
            if old:
                if old['input_digest'] != digest:
                    raise HTTPException(409, '同一请求已绑定其他参数，请重新核对')
                return self.public_operation(old)
            with self.store._lock:
                unknown = self.store._db.execute("SELECT 1 FROM app_operations WHERE json_extract(result,'$.status')='unknown' LIMIT 1").fetchone()
            if unknown:
                raise HTTPException(409, '此前操作结果待核实，请先核实银行记录，再安排新的资金操作')
            if count >= 20:
                raise HTTPException(429, '操作过于频繁，请稍后再试')
            params, meta = self.resolve_params(data)
            text = f"用户在参数表单提交：{meta['label']}"
            content = {'text': text, 'payload': params}
            source = self.gateway.lineage.issue_root(kind=SourceKind.USER, trust=SourceTrust.USER,
                content=content, producer_id='frontend', metadata={'actor_id': ACTOR, 'session_id': sid})
            trace = AgentTrace(trace_id=str(uuid4()), actor_id=ACTOR, session_id=sid,
                               messages=(SourceMessage(source=source, **content),))
            model_output = canonical_json({'schema_version': 'mosaic-planner-v2', 'kind': 'task',
                                            'action_type': 'transfer', 'params': params})
            review = self.gateway.review(self.credential(sid), ReviewRequest(model_output=model_output, trace=trace))
            if review.get('status') != 'reviewed':
                raise HTTPException(409, '参数未形成可确认的操作')
            handle = review['handle']
            exact = self.gateway.confirmation_view(self.credential(sid), handle)
            result = {**review['outcome'], 'level': review['level'], 'params': exact['params']}
            with self.store._atomic():
                self.store._db.execute('INSERT INTO app_operations VALUES (?,?,?,?,?,?,?,?,?)',
                    (handle, sid, data.request_id, digest, canonical_json(meta), canonical_json(result),
                     secrets.token_urlsafe(32), time.time() + 180, time.time()))
            self.learning.capture(sid, data.feedback_ref, data.model_dump())
            return self.public_operation(self.operation_row(sid, handle))

    def update_result(self, handle: str, result: dict):
        with self.store._atomic():
            old = self.store._db.execute('SELECT result FROM app_operations WHERE handle=?', (handle,)).fetchone()
            combined = {**json.loads(old[0]), **result}
            self.store._db.execute('UPDATE app_operations SET result=? WHERE handle=?', (canonical_json(combined), handle))

    @staticmethod
    def totp(secret: str, counter: int | None = None) -> str:
        key = base64.b32decode(secret, casefold=True)
        digest = hmac.new(key, struct.pack('>Q', counter if counter is not None else int(time.time() // 30)), hashlib.sha1).digest()
        offset = digest[-1] & 15
        value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff
        return f'{value % 1_000_000:06d}'

    def verify_code(self, code: str | None, *, sid: str, enrollment=False):
        error = None
        with self.store._atomic():
            row = self.store._db.execute('SELECT * FROM app_mfa WHERE actor=?', (ACTOR,)).fetchone()
            secret = row['pending' if enrollment else 'secret'] if row else None
            if not secret or (enrollment and (row['pending_session'] != sid or row['pending_expires'] < time.time())):
                raise HTTPException(409, '请先配置动态验证码')
            if row['blocked_until'] > time.time():
                raise HTTPException(429, '验证码尝试过多，请五分钟后再试')
            counter = int(time.time() // 30)
            matched = next((n for n in (counter - 1, counter, counter + 1)
                            if n > row['last_counter'] and hmac.compare_digest(self.totp(secret, n), code or '')), None)
            if matched is None:
                failures = row['failed'] + 1
                self.store._db.execute('UPDATE app_mfa SET failed=?,blocked_until=? WHERE actor=?',
                    (failures if failures < 5 else 0, time.time() + 300 if failures >= 5 else 0, ACTOR))
                error = HTTPException(409, '验证码无效或已使用，请输入下一组验证码')
            else:
                self.store._db.execute('UPDATE app_mfa SET last_counter=?,failed=0,blocked_until=0 WHERE actor=?', (matched, ACTOR))
                if enrollment:
                    self.store._db.execute('UPDATE app_mfa SET secret=pending,pending=NULL,pending_session=NULL,pending_expires=NULL WHERE actor=?', (ACTOR,))
        if error:
            raise error

    def setup_mfa(self, sid: str) -> dict:
        import qrcode
        import qrcode.image.svg
        with self.store._atomic():
            old = self.store._db.execute('SELECT * FROM app_mfa WHERE actor=?', (ACTOR,)).fetchone()
            if old and old['secret']:
                raise HTTPException(409, '动态验证码已开启')
            secret = base64.b32encode(secrets.token_bytes(20)).decode()
            self.store._db.execute('INSERT INTO app_mfa(actor,pending,pending_session,pending_expires) VALUES (?,?,?,?) '
                'ON CONFLICT(actor) DO UPDATE SET pending=excluded.pending,pending_session=excluded.pending_session,pending_expires=excluded.pending_expires',
                (ACTOR, secret, sid, time.time() + 600))
        uri = f'otpauth://totp/Qingcai:Experience?secret={secret}&issuer=Qingcai&algorithm=SHA1&digits=6&period=30'
        buffer = io.BytesIO()
        qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
        return {'secret': secret, 'qr': 'data:image/svg+xml;base64,' + base64.b64encode(buffer.getvalue()).decode()}

    def confirm(self, sid: str, handle: str, data: Confirmation) -> dict:
        with self.lock:
            row = self.operation_row(sid, handle)
            op = self.public_operation(row)
            if op['status'] in {'succeeded', 'unknown', 'cancelled', 'blocked'}:
                return op
            if row['expires'] < time.time() or not hmac.compare_digest(data.challenge, row['challenge']):
                raise HTTPException(409, '确认已失效，请重新打开此操作核对参数')
            meta = json.loads(row['meta'])
            if meta.get('input'):
                # Business suitability/available capital can change after review.
                self.resolve_params(OperationInput.model_validate(meta['input']))
            if op['status'] == 'needs_mfa':
                self.verify_code(data.code, sid=sid)
            credential = self.credential(sid)
            view = self.gateway.confirmation_view(credential, handle)
            for obligation in (('confirmation', 'mfa') if op['status'] == 'needs_mfa' else ('confirmation',)):
                proof = self.gateway.control_authority.issue(ControlClaims(operation=obligation, actor_id=ACTOR,
                    session_id=sid, target=handle, binding=view['action_digest']), key_id='v1', ttl=60)
                self.gateway.approve(credential, handle, proof)
            # Persist uncertainty before dispatch; lost response never means retry.
            self.update_result(handle, {'status': 'unknown', 'message': '正在核实执行结果，请勿重复提交'})
            try:
                result = self.gateway.execute(credential, handle)
            except Exception:
                result = {'status': 'unknown', 'message': '结果尚未确认，请核实此操作，不要重新提交'}
            self.update_result(handle, result)
            return self.public_operation(self.operation_row(sid, handle))

    def read_operation(self, sid: str, handle: str) -> dict:
        with self.lock:
            row = self.owner_history_row(handle)
            if row['session_id'] != sid:
                op = self.public_operation(row, include_challenge=False)
                if op['status'] in {'needs_confirmation','needs_mfa'}:
                    with self.store._lock:
                        active = self.store._db.execute('SELECT 1 FROM app_sessions WHERE session_id=? AND expires>?', (row['session_id'], time.time())).fetchone()
                    if active:
                        raise HTTPException(404, '此待确认操作不属于当前会话')
                    op.update(status='expired', message='原会话已结束，这份草稿不会执行')
                return {**op, 'historical': True}
            result = json.loads(row['result'])
            if result['status'] in {'needs_confirmation', 'needs_mfa'} and row['expires'] < time.time():
                with self.store._atomic():
                    self.store._db.execute('UPDATE app_operations SET challenge=?,expires=? WHERE handle=?',
                        (secrets.token_urlsafe(32), time.time() + 180, handle))
                row = self.operation_row(sid, handle)
            return self.public_operation(row)

    def cancel(self, sid: str, handle: str) -> dict:
        with self.lock:
            row = self.operation_row(sid, handle)
            credential = self.credential(sid)
            view = self.gateway.confirmation_view(credential, handle)
            proof = self.gateway.control_authority.issue(ControlClaims(operation='cancel', actor_id=ACTOR,
                session_id=sid, target=handle, binding=view['action_digest']), key_id='v1', ttl=60)
            result = self.gateway.cancel(credential, handle, proof)
            if result['cancelled']:
                self.update_result(handle, {'status': 'cancelled', 'message': '已取消，资金未划出'})
            return self.public_operation(self.operation_row(sid, handle))

    def reconcile(self, sid: str, handle: str) -> dict:
        with self.lock:
            self.owner_history_row(handle)
            result = self.gateway.reconcile(self.credential(sid, operator=True), handle)
            if result['backend_status'] == 'posted':
                self.update_result(handle, {'status': 'succeeded', 'data': result['backend_result'],
                    'message': '模拟银行的持久记录已核实，本次操作已完成', 'evidence': 'backend_reconciliation'})
            return self.read_operation(sid, handle)

    def recovery_view(self, sid: str) -> dict:
        state = self.gateway.circuit.snapshot(ACTOR)
        with self.store._lock:
            pending = self.store._db.execute("SELECT COUNT(*) FROM app_operations WHERE json_extract(result,'$.status')='unknown'").fetchone()[0]
        challenge = secrets.token_urlsafe(32)
        with self.store._atomic():
            self.store._db.execute('INSERT OR REPLACE INTO app_recovery VALUES (?,?,?,?)',
                (sid, challenge, state['generation'], time.time()+180))
        return {'locked':state['locked'], 'unresolved':pending, 'challenge':challenge}

    def recover_protection(self, sid: str, data: ProtectionRecovery) -> dict:
        with self.lock:
            credential = self.credential(sid, operator=True)
            with self.store._lock:
                row = self.store._db.execute('SELECT * FROM app_recovery WHERE session_id=?', (sid,)).fetchone()
                pending = self.store._db.execute("SELECT 1 FROM app_operations WHERE json_extract(result,'$.status')='unknown' LIMIT 1").fetchone()
            state = self.gateway.circuit.snapshot(ACTOR)
            if not state['locked']:
                return {'locked':False}
            if pending:
                raise HTTPException(409, '还有待核实操作，请先逐笔核实银行记录')
            if not row or row['expires'] < time.time() or row['generation'] != state['generation'] or not hmac.compare_digest(row['challenge'],data.challenge):
                raise HTTPException(409, '恢复确认已失效，请重新查看保护状态')
            self.verify_code(data.code, sid=sid)
            proof = self.gateway.control_authority.issue(ControlClaims(operation='reset', actor_id=ACTOR,
                session_id=sid, target=ACTOR, binding=self.gateway.reset_binding(ACTOR,state['generation'])), key_id='v1', ttl=60)
            result = self.gateway.reset(credential, ACTOR, proof)
            with self.store._atomic():
                self.store._db.execute('DELETE FROM app_recovery WHERE session_id=?', (sid,))
            return {'locked':result['locked']}

    def assistant(self, sid: str, data: FinanceConversation) -> dict:
        """LLM intent and grounded explanation; neither grants bank authority."""
        latest = next((m.content for m in reversed(data.messages) if m.role == 'user'), '')
        scenario = re.search(r'(?:改(?:为|成)|缩短(?:为|到)?|延长(?:为|到)?|换成|按|那|期限(?:为|是)?|规划)\s*([0-9一二两三四五六七八九十]+)\s*个?月', latest)
        if scenario and not re.search(r'转账|申购|赎回|转给|买入', latest):
            value = scenario.group(1)
            digits = {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
            months = int(value) if value.isdigit() else digits.get(value)
            if months is None and '十' in value:
                left, right = value.split('十', 1)
                months = (digits.get(left, 1) * 10) + digits.get(right, 0)
            if not months or months > 60:
                return {'reply': '请使用 1 至 60 个月测算目标。', 'draft': None, 'planner_status': 'scenario'}
            plan = self.overview()['planning']
            remaining = plan['goal']['remaining_minor']
            if not plan['goal']['target_minor']:
                return {'reply': '先设置目标金额，再计算不同期限的每月预留。', 'draft': None, 'plan_next': 'profile', 'planner_status': 'scenario'}
            required = (remaining + months - 1) // months
            return {'reply': f"目标剩余 {remaining / 100:,.2f} 元。按 {months} 个月测算，每月需预留 {required / 100:,.2f} 元；原 {plan['goal']['months']} 个月计划为 {plan['goal']['monthly_required_minor'] / 100:,.2f} 元。\n\n这次只做测算，现有计划和资金未改变。",
                    'draft': None, 'plan_next': 'profile', 'planner_status': 'scenario',
                    'scenario': {'months': months, 'monthly_required_minor': required, 'remaining_minor': remaining}}
        system = INTENT_PROMPT
        messages = [{'role': 'system', 'content': system}]
        for message in data.messages:
            text=message.content
            # The general minimizer masks phones. Resolve only the two registered
            # experience contacts before screening; arbitrary phones remain masked.
            for recipient in RECIPIENTS:
                text=re.sub(r'(?<!\d)'+re.escape(recipient['phone'])+r'(?!\d)',recipient['name'],text)
            screened = self.gateway.screen(self.credential(sid), text)
            messages.append({'role': message.role, 'content': screened['safe_text']})
        raw = api_chat(messages, max_tokens=256, temperature=0, top_p=1).strip()
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.S).strip()
        if raw.startswith('```') and raw.endswith('```'):
            raw = '\n'.join(raw.splitlines()[1:-1]).strip()
        try:
            intent = BankIntent.model_validate_json(raw)
        except ValueError:
            return {'reply': '这次没能理解完整操作。请告诉我收款人或产品名称，以及金额。', 'draft': None, 'planner_status': 'invalid_output'}
        if intent.intent == 'plan':
            overview = self.overview()
            flow = overview['cashflow']
            facts = plan_facts(overview)
            selection = plan_target(facts)
            explanation_status = 'verified_fallback'
            try:
                raw_plan = api_chat([{'role':'system','content':PLAN_PROMPT},
                                    {'role':'user','content':json.dumps(facts,ensure_ascii=False)}],
                                    max_tokens=256,temperature=0,top_p=1)
                candidate = json.loads(raw_plan)
                if validate_plan_selection(candidate,facts):
                    selection, explanation_status = candidate, 'model_grounded'
            except (ValueError, TypeError, RuntimeError, HTTPException):
                pass  # A malformed/unavailable explanation never becomes money advice.
            planning = overview['planning']
            goal = planning['goal']
            observed = planning['basis']['observed_months']
            money_text = lambda value: f'{Decimal(value) / 100:,.2f}'
            sentences = []
            if observed:
                label = '默认账本' if overview['profile']['ledger_scope'] == 'demo' else '个人账本'
                sentences.append(f"按 {flow['start_date']} 至 {flow['end_date']} 内 {observed} 个有记录月份的{label}，月均结余 {money_text(flow['monthly_net_minor'])} 元。")
            else:
                sentences.append('目前没有可用于估算月均结余的完整月份账单。')
            if goal['target_minor']:
                sentences.append(f"按已记录的目标进度，目标剩余 {money_text(goal['remaining_minor'])} 元；按设置的 {goal['months']} 个月规划，每月需预留 {money_text(goal['monthly_required_minor'])} 元。")
            if planning['ready']:
                extra = planning['monthly']['commitment_extra_minor']
                prefix = f"扣除每月额外固定预留 {money_text(extra)} 元后，" if extra else ''
                sentences.append(f"{prefix}目标预留后月均结余为 {money_text(planning['after_goal_minor'])} 元，尚未扣减待核对的应急金缺口。")
            else:
                sentences.append(planning['quality']['message'] + '，暂不据此判断可长期安排的月度金额。')
            emergency = planning['emergency']['target_minor']
            if emergency:
                sentences.append(f"应急金目标为 {money_text(emergency)} 元，实际已预留金额尚未核对。")
            else:
                sentences.append('应急金目标需补充开销后再估算，实际已预留金额尚未核对。')
            reasons = {item['id']:item['text'] for item in overview['reasons']}
            if observed < 3 and 'goal_capacity' in reasons:
                reasons['goal_capacity'] = '目标每月计划仍需补充账单后核对'
            sentences.extend(('模拟账户：' if key in {'reserve', 'longterm', 'liquid'} else '') + reasons[key] + '。'
                             for key in selection['reason_ids'])
            sentences.append(f"模拟账户的长期资金测算额为 {money_text(overview['investable_minor'])} 元；这是独立模拟本金，申购仍须通过产品适配与确认，不计入上述账本结余。")
            return {'reply': '\n\n'.join(sentences), 'draft': None, 'planner_status': 'understood',
                    'explanation_status':explanation_status, 'plan_next':selection['next_step'],
                    'plan_focus':selection['focus']}
        if intent.intent == 'unsupported':
            return {'reply': '我可以帮你核对个人计划、转账，或模拟申购与赎回。你想先做哪一步？', 'draft': None, 'planner_status': 'unsupported'}
        if foreign_currency_pending(data.messages):
            return {'reply':'体验账户使用人民币。请确认人民币金额，例如“转 80 元给小林”。',
                    'draft':None,'planner_status':'needs_clarification'}
        if not intent.amount or minor(intent.amount) <= 0:
            return {'reply': '这笔操作的金额是多少？', 'draft': None, 'planner_status': 'needs_clarification'}
        if intent.intent == 'transfer':
            recipient = next((r for r in RECIPIENTS if intent.recipient in {r['name'], r['phone'], r['remark']}), None)
            if recipient is None:
                return {'reply': '转给谁？目前可核对的收款人有小林和房东。', 'draft': None, 'planner_status': 'needs_clarification'}
            draft = {'kind': 'transfer', 'recipient_id': recipient['id'], 'amount': intent.amount}
        else:
            product = next((p for p in PRODUCTS if intent.product_id == p['id']), None)
            selected_by_user=product and any(product['name'] in message.content or product['id']==message.content.strip()
                                             for message in data.messages if message.role=='user')
            if product is None or not selected_by_user:
                return {'reply': '想选择哪一个方案：灵活现金，还是长期均衡？', 'draft': None, 'planner_status': 'needs_clarification'}
            draft = {'kind': intent.intent, 'product_id': product['id'], 'amount': intent.amount}
        ref = self.learning.remember_draft(sid, draft)
        return {'reply': '草稿已整理，请核对收款方和金额后再确认。', 'draft': draft, 'feedback_ref': ref, 'planner_status': 'understood'}


class FinanceRouter:
    def __init__(self, data_dir: Path, read_bills, import_bills=None):
        self.data_dir, self.read_bills = data_dir, read_bills
        self.import_bills = import_bills
        self.instance = None
        self.lock = RLock()
        self.router = APIRouter(prefix='/finance', tags=['personal-finance'])
        self.routes()

    def workspace(self) -> FinanceWorkspace:
        with self.lock:
            if self.instance is None:
                state_dir = Path(os.getenv('FINANCE_STATE_DIR', str(self.data_dir / '.qingcai-workspace')))
                self.instance = FinanceWorkspace(state_dir, self.read_bills, self.import_bills)
            return self.instance

    def reset(self):
        with self.lock:
            if self.instance:
                self.instance.close()
            self.instance = None

    def routes(self):
        router = self.router

        @router.get('/session')
        def session(request: Request, response: Response):
            return self.workspace().start_session(request, response)

        @router.get('/overview')
        def overview(request: Request, response: Response):
            service = self.workspace()
            service.session(request)
            response.headers['Cache-Control'] = 'no-store'
            return service.overview()

        @router.post('/profile')
        def profile(request: Request, data: ProfileUpdate):
            service = self.workspace()
            service.session(request)
            return service.save_profile(data)

        @router.post('/assistant')
        def assistant(request: Request, data: FinanceConversation):
            service = self.workspace()
            return service.assistant(service.session(request), data)

        @router.post('/imports/preview')
        def preview_import(request: Request, data: CSVPreview):
            service = self.workspace()
            return service.preview_import(service.session(request), data)

        @router.post('/goal/progress')
        def goal_progress(request: Request, data: GoalProgress):
            service = self.workspace()
            service.session(request)
            return service.record_goal_progress(data)

        @router.get('/goal/progress')
        def goal_history(request: Request):
            service = self.workspace()
            service.session(request)
            return service.goal_history()

        @router.post('/goal/progress/{request_id}/undo')
        def undo_goal_progress(request: Request, request_id: str):
            service = self.workspace()
            service.session(request)
            return service.undo_goal_progress(request_id)

        @router.get('/improvement')
        def improvement(request: Request):
            service = self.workspace()
            service.session(request)
            return service.learning.status()

        @router.post('/improvement/consent')
        def learning_consent(request: Request, data: LearningConsent):
            service = self.workspace()
            service.session(request)
            return service.learning.consent(data.enabled)

        @router.get('/protection/recovery')
        def recovery_view(request: Request):
            service = self.workspace()
            return service.recovery_view(service.session(request))

        @router.post('/protection/recover')
        def recover_protection(request: Request, data: ProtectionRecovery):
            service = self.workspace()
            return service.recover_protection(service.session(request), data)

        @router.post('/improvement/{handle}/review')
        def feedback_review(request: Request, handle: str, data: FeedbackReview):
            service = self.workspace()
            service.session(request)
            return service.learning.review(handle, data.approve)

        @router.delete('/improvement/{handle}')
        def delete_feedback(request: Request, handle: str):
            service = self.workspace()
            service.session(request)
            return service.learning.delete(handle)

        @router.post('/imports/{handle}/commit')
        def commit_import(request: Request, handle: str, data: CSVCommit):
            service = self.workspace()
            return service.commit_import(service.session(request), handle, data)

        @router.post('/operations/review')
        def review(request: Request, data: OperationInput):
            service = self.workspace()
            return service.review(service.session(request), data)

        @router.get('/operations/{handle}')
        def operation(request: Request, handle: str):
            service = self.workspace()
            return service.read_operation(service.session(request), handle)

        @router.post('/operations/{handle}/confirm')
        def confirm(request: Request, handle: str, data: Confirmation):
            service = self.workspace()
            return service.confirm(service.session(request), handle, data)

        @router.post('/operations/{handle}/cancel')
        def cancel(request: Request, handle: str, data: StrictInput):
            service = self.workspace()
            return service.cancel(service.session(request), handle)

        @router.post('/operations/{handle}/reconcile')
        def reconcile(request: Request, handle: str, data: StrictInput):
            service = self.workspace()
            return service.reconcile(service.session(request), handle)

        @router.post('/mfa/setup')
        def setup(request: Request, data: StrictInput):
            service = self.workspace()
            return service.setup_mfa(service.session(request))

        @router.post('/mfa/activate')
        def activate(request: Request, data: MFAInput):
            service = self.workspace()
            service.verify_code(data.code, sid=service.session(request), enrollment=True)
            return {'enabled': True}
