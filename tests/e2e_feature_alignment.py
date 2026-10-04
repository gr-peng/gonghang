"""Read-only live acceptance; goal and chat changes stay in a fresh browser context."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.runtime/feature-alignment'
OUT.mkdir(exist_ok=True)
FRONT='http://127.0.0.1:25500'
checks=[]
def check(text):
    checks.append(text)
    print('PASS',text,flush=True)

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH'))
    page=browser.new_page(viewport={'width':393,'height':852},locale='zh-CN')
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    before=page.request.get(FRONT+'/api/book/health').json()['bill_count']
    def visit(route):
        page.goto(FRONT+'/#/'+route,wait_until='networkidle')
        page.wait_for_selector('main')
        page.wait_for_function("!document.querySelector('main .loading')")
        assert not page.locator('main .error').count(),route
    def action(name,value=None):
        selector=f'[data-action="{name}"]'+(f'[data-value="{value}"]' if value is not None else '')
        page.locator(selector).first.click()
    try:
        visit('home')
        expect(page.locator('nav a')).to_have_count(5)
        page.locator('a[href="#/visualization"]').click()
        expect(page.locator('h1')).to_have_text('图表')
        page.locator('#month-picker').fill('2026-09')
        expected=page.request.get(FRONT+'/api/book/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30').json()['custom']
        expect(page.locator('.metric').first).to_contain_text(f"{expected['summary']['income_total']:,.2f}")
        expect(page.locator('.metric').nth(1)).to_contain_text(f"{expected['summary']['expense_total']:,.2f}")
        point=page.locator('.bars-chart .chart-point').first
        point.focus();page.keyboard.press('Enter')
        expect(page.locator('.chart-value')).to_contain_text('收入')
        action('visual-type','折线')
        expect(page.locator('svg[aria-label="区间收支趋势"]')).to_be_visible()
        action('visual-period','自定义')
        page.locator('#visual-start').fill('2026-09-20');page.locator('#visual-end').fill('2026-09-01')
        page.locator('#visual-range-form button[type="submit"]').click()
        expect(page.locator('#visual-range-error')).to_contain_text('不能晚于')
        page.locator('#visual-start').fill('2026-09-01');page.locator('#visual-end').fill('2026-09-10')
        page.locator('#visual-range-form button[type="submit"]').click()
        page.wait_for_function("!document.querySelector('main .loading')")
        link=page.locator('button.donut-legend-row').first
        category=link.get_attribute('data-value');link.click()
        expect(page.locator('h1')).to_have_text('账本')
        expect(page.locator('main')).to_contain_text('2026-09-01 — 2026-09-10')
        for row in page.locator('.bill-row').all():assert category in row.inner_text()
        check('图表金额与 API 一致，柱/线切换、键盘读数、自定义日期校验、分类跳转正确')

        visit('visualization');action('visual-period','月')
        action('goal-edit')
        page.locator('#goal-name').fill('旅行基金 <核对>')
        page.locator('#goal-target').fill('10000');page.locator('#goal-percent').fill('20')
        page.locator('#goal-form button[type="submit"]').click()
        expect(page.locator('main')).to_contain_text('旅行基金 <核对>')
        action('goal-deposit')
        page.locator('#goal-deposit-amount').fill('250.50')
        page.locator('#goal-deposit-form button[type="submit"]').click()
        expect(page.locator('.goal-details')).to_contain_text('250.50')
        page.reload(wait_until='networkidle')
        expect(page.locator('main')).to_contain_text('旅行基金 <核对>')
        expect(page.locator('.goal-details')).to_contain_text('250.50')
        action('goal-edit');page.locator('#goal-target').fill('20000');page.locator('#goal-form button[type="submit"]').click()
        expect(page.locator('.goal-details')).to_contain_text('250.50')
        page.screenshot(path=str(OUT/'visualization-goal.png'),full_page=True)
        action('goal-remove');action('goal-remove-confirm')
        expect(page.locator('[data-action="goal-edit"]')).to_have_text('设置储蓄目标')
        page.locator('#month-picker').fill('2030-01')
        expect(page.locator('main')).to_contain_text('本期暂无收支数据')
        page.locator('#month-picker').fill('2026-09')
        check('目标创建、编辑、手动进度、刷新保存、移除和空月份；账本不产生新记录')

        visit('assistant');action('chat-add')
        expect(page.locator('.template-item')).to_have_count(17)
        page.locator('#template-search').fill('预算')
        expect(page.locator('.template-item')).to_have_count(1)
        page.locator('.template-item').click()
        expect(page.locator('#chat-input')).to_have_value('帮我设置一个预算计划')
        expect(page.locator('.chat-message')).to_have_count(0)
        action('chat-add');page.locator('[data-id="book-0"]').click()
        action('chat-parse-bill')
        expect(page.locator('#entry-amount')).to_have_value('50.00',timeout=180000)
        expect(page.locator('#entry-description')).not_to_have_value('')
        visit('assistant');action('chat-mode','投研');action('chat-add')
        expect(page.locator('.template-item')).to_have_count(23)
        page.locator('#template-search').fill('ETF和主动')
        expect(page.locator('.template-item')).to_have_count(1)
        page.screenshot(path=str(OUT/'quick-input.png'),full_page=True)
        page.locator('.template-item').click()
        with page.expect_response(lambda r:r.url.endswith('/api/trader/chat'),timeout=180000) as reply:
            page.locator('#chat-form button[type="submit"]').click()
        assert reply.value.status==200
        assert reply.value.json()['reply'].strip()
        expect(page.locator('.chat-message:not(.user)')).to_have_count(1,timeout=10000)
        page.screenshot(path=str(OUT/'assistant-reply.png'),full_page=True)
        check('17 个记账与 23 个投研模板可搜索、填入及编辑；真实模型提取草稿和回复，未自动入账')

        visit('insights');action('insight-tab','习惯')
        expect(page.locator('main')).to_contain_text('消费模式')
        action('insight-tab','建议')
        expect(page.locator('main')).to_contain_text('储蓄与支出结构')
        with page.expect_response(lambda r:r.url.endswith('/api/book/chat'),timeout=180000) as advice:
            action('advice-generate')
        assert advice.value.status==200
        expect(page.locator('[data-action="advice-generate"]')).to_have_text('重新生成',timeout=10000)
        page.screenshot(path=str(OUT/'advice.png'),full_page=True)
        check('财务概览、消费行为、储蓄结构和真实模型建议可用')

        visit('dashboard')
        expect(page.locator('.donut-chart')).to_have_count(2)
        action('benchmark-period','年')
        expect(page.locator('.benchmark-image')).to_have_attribute('src','assets/FinAgent_vs_SSE100_Year.png')
        page.wait_for_function("document.querySelector('.benchmark-image').naturalWidth>0")
        action('benchmark-open');expect(page.locator('#sheet .benchmark-image')).to_be_visible();action('close-sheet')
        for code in ['601088','601138']:
            page.locator(f'[data-compare-code="{code}"]').check()
            page.wait_for_function("!document.querySelector('main .loading')")
        expect(page.locator('[data-compare-code]:checked')).to_have_count(5)
        page.locator('[data-compare-code="601288"]').click()
        expect(page.locator('[data-compare-code="601288"]')).not_to_be_checked()
        expect(page.locator('.series-legend span')).to_have_count(5)
        page.screenshot(path=str(OUT/'dashboard-mobile.png'),full_page=True)
        visit('risk')
        expect(page.locator('.risk-metrics')).to_contain_text('52.5%')
        action('risk-ask');expect(page.locator('#chat-input')).to_contain_text('历史组合指标')
        check('行业/持仓分布、三档回测图片、5 只自选股共同日期对比、风险指标与追问入口')

        for width in [360,393,1280]:
            page.set_viewport_size({'width':width,'height':900})
            for route in ['home','ledger','entry','assistant','visualization','insights','investment','stock','dashboard','risk','settings']:
                visit(route)
                assert page.evaluate('document.documentElement.scrollWidth')<=width,(width,route)
            if width==1280:
                visit('visualization');page.screenshot(path=str(OUT/'visualization-desktop.png'),full_page=True)
        visit('settings');page.locator('#pref-mask').check()
        visit('visualization');expect(page.locator('main')).to_contain_text('金额已隐藏');expect(page.locator('.donut-chart')).to_have_count(0)
        visit('dashboard');expect(page.locator('main')).to_contain_text('收益图已隐藏');expect(page.locator('main .benchmark-image')).to_have_count(0)
        assert not errors,errors
        assert page.request.get(FRONT+'/api/book/health').json()['bill_count']==before
        check('11 页在 360/393/1280 宽度无页面溢出；隐藏金额生效；无 JS 错误，账单数量不变')
        (OUT/'results.json').write_text(json.dumps({'checks':checks,'javascript_errors':errors,'bill_count_before':before,'bill_count_after':before},ensure_ascii=False,indent=2))
    finally:
        browser.close()
