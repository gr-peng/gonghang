"""Check an already-running local app against its original dataset.

Creates one clearly labelled test bill and removes only that bill in finally.
Run with PLAYWRIGHT_CHANNEL=msedge on Windows, or an installed Chromium.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import uuid

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run import read_env

read_env()
FRONT = f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', '5500')}"
BOOK = f"http://127.0.0.1:{os.getenv('BOOKKEEPER_PORT', '8010')}"
OUT = ROOT / '.runtime' / 'upstream-validation'
OUT.mkdir(parents=True, exist_ok=True)
source = [json.loads(m['content'])
          for line in (ROOT / 'AI_accounting_agent/backend/data/synthetic_bank_bills.jsonl').read_text(encoding='utf-8').splitlines()
          for m in json.loads(line)['messages'] if m['role'] == 'assistant']
checks = []


def check(name):
    checks.append(name)
    print('PASS', name, flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL') or None, headless=True)
    page = browser.new_page(viewport={'width':393, 'height':852})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    created_id = None
    initial_count = page.request.get(BOOK + '/health').json()['bill_count']

    def visit(route):
        page.goto(FRONT + '/#/' + route)
        page.wait_for_selector('main')
        page.wait_for_function("!document.querySelector('main .loading')")
        assert page.locator('main .error').count() == 0, route

    try:
        visit('home')
        expect(page.locator('#month-picker')).to_have_value('2025-12')
        for kind, index in [('income', 0), ('expense', 1)]:
            total = sum(Decimal(str(r['金额'])) for r in source if r['日期'].startswith('2025-12') and r['type'] == kind)
            expect(page.locator('.metric').nth(index)).to_contain_text(f'{total:,.2f}')
        expect(page.locator('.bill-row')).to_have_count(5)
        page.screenshot(path=str(OUT / 'home-mobile.png'), full_page=True)
        check('默认打开原数据最新月份，收支金额与原 JSONL 一致')

        visit('ledger')
        expect(page.locator('.bill-row')).to_have_count(123)
        page.locator('[data-action="filter"]').click()
        for category in ['工资', '奖金', '副业', '理财收益']:
            assert category in page.locator('#filter-category').inner_text()
        page.locator('#filter-type').select_option('income')
        page.locator('[data-action="apply-filter"]').click()
        expect(page.locator('.bill-row')).to_have_count(3)
        page.screenshot(path=str(OUT / 'income-filter.png'), full_page=True)
        page.locator('main [data-action="reset-filter"]').click()
        expect(page.locator('.bill-row')).to_have_count(123)
        page.locator('#month-picker').fill('2025-01')
        expect(page.locator('.bill-row')).to_have_count(68)
        page.locator('#month-picker').fill('2026-09')
        expect(page.locator('.bill-row')).to_have_count(0)
        expect(page.locator('#month-picker')).to_have_value('2026-09')
        page.locator('#month-picker').fill('2025-12')
        expect(page.locator('.bill-row')).to_have_count(123)
        check('账本完整加载、原收入分类筛选、历史月份和空月份切换')

        visit('insights')
        page.locator('[data-action="period"][data-value="年"]').click()
        year_expense = sum(Decimal(str(r['金额'])) for r in source if r['type'] == 'expense')
        expect(page.locator('.metric').last).to_contain_text(f'{year_expense:,.2f}')
        page.locator('[data-action="insight-tab"][data-value="习惯"]').click()
        expect(page.locator('main')).to_contain_text('2025-12-31')
        check('全年分析与所选历史区间的习惯分析')

        visit('entry')
        description = '原数据接线测试-' + uuid.uuid4().hex
        page.locator('#entry-amount').fill('12.34')
        page.locator('#entry-date').fill('2025-12-28')
        page.locator('#entry-description').fill(description)
        with page.expect_response(lambda r: r.url == BOOK + '/bills' and r.request.method == 'POST') as saved:
            page.locator('#save-entry').click()
        created_id = saved.value.json()['id']
        expect(page.locator('.bill-row')).to_have_count(124)
        page.locator('#bill-search').fill(description)
        expect(page.locator('.bill-row')).to_have_count(1)
        page.locator('.bill-row').click()
        page.locator('[data-action="delete-prompt"]').click()
        with page.expect_response(lambda r: r.url == BOOK + f'/bills/{created_id}' and r.request.method == 'DELETE') as deleted:
            page.locator('[data-action="delete-confirm"]').click()
        assert deleted.value.status == 200
        created_id = None
        expect(page.locator('.bill-row')).to_have_count(0)
        page.locator('#bill-search').fill('')
        expect(page.locator('.bill-row')).to_have_count(123)
        check('新版前端通过原接口保存、删除，账本与统计同步')

        for route in ['assistant', 'investment', 'stock?code=600036', 'settings']:
            visit(route)
        page.locator('[data-action="health"]').click()
        expect(page.locator('#service-status')).to_contain_text('已连接')
        assert page.request.get(BOOK + '/health').json()['llm']['provider'] == 'disabled'
        check('八页可用、投研原数据连接、LLM 保持关闭')
        visit('home')
        page.set_viewport_size({'width':1280,'height':900})
        page.screenshot(path=str(OUT / 'home-desktop.png'), full_page=True)
        assert not errors, errors
        assert page.request.get(BOOK + '/health').json()['bill_count'] == initial_count
        check('无浏览器脚本错误，测试账单已清理')
        (OUT / 'results.json').write_text(json.dumps({'checks': checks, 'count':len(checks), 'bill_count':initial_count, 'javascript_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        if created_id is not None:
            page.request.delete(BOOK + f'/bills/{created_id}')
        browser.close()
