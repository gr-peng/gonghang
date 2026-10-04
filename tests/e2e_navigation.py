"""Read-only browser checks for navigation, history and unsaved form state."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'.runtime/navigation-review'
out.mkdir(parents=True,exist_ok=True)
FRONT=os.getenv('QINGCAI_TEST_FRONT','http://127.0.0.1:25500')
checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH'))
    page=browser.new_page(viewport={'width':393,'height':852},locale='zh-CN')
    errors=[]
    writes=[]
    page.on('request',lambda req:writes.append(req.url) if '/api/' in req.url and req.method not in ['GET','HEAD','OPTIONS'] else None)
    page.on('pageerror',lambda error:errors.append(str(error)))
    def ready(route):
        print("waiting",route,page.url,flush=True)
        page.wait_for_function('(r)=>document.querySelector("#app")?.dataset.route===r',arg=route)
        page.wait_for_function('!document.querySelector("main .loading")')
        assert not page.locator('main .error').count(),page.locator('main').inner_text()
    def act(name):page.locator('[data-action="'+name+'"]').first.click()
    def tab(name):page.locator('[data-nav-tab="'+name+'"]').click()
    def back(route):act('back');ready(route)
    page.goto(FRONT+'/#/home',wait_until='networkidle');ready('home')
    assert page.locator('[data-action="back"]').count()==0
    tab('visualization');ready('visualization')
    expect(page.locator('[data-action="back"]')).to_have_attribute('aria-label','返回首页')
    page.locator('#month-picker').fill('2026-09');ready('visualization')
    page.locator('button[data-action="visual-category"]').first.click();ready('ledger')
    expect(page.locator('[data-action="back"]')).to_have_attribute('aria-label','返回图表')
    assert page.locator('.tab-bar [aria-current="page"]').get_attribute('data-nav-tab')=='ledger'
    act('settings');ready('settings');back('ledger');back('visualization')
    page.go_forward();ready('ledger');page.go_forward();ready('settings');page.go_back();ready('ledger')
    checks.append('图表分类→账本→设置→原入口；浏览器前进后退同步')
    act('entry');ready('entry')
    page.locator('#entry-amount').fill('32.18');page.locator('#entry-description').fill('导航草稿，不保存')
    act('settings');ready('settings');back('entry')
    expect(page.locator('#entry-amount')).to_have_value('32.18')
    expect(page.locator('#entry-description')).to_have_value('导航草稿，不保存')
    back('ledger');checks.append('记账草稿往返保留，无账单保存')
    tab('investment');ready('investment');act('journey-profile');ready('profile')
    page.locator('#finance-goal').fill('导航草稿，不提交')
    page.locator('input[name="risk-0"][value="1"]').check()
    act('settings');ready('settings');act('improvement-open');ready('improvement')
    assert page.locator('.tab-bar [aria-current="page"]').get_attribute('data-nav-tab')=='investment'
    back('settings');back('profile')
    expect(page.locator('#finance-goal')).to_have_value('导航草稿，不提交')
    expect(page.locator('input[name="risk-0"][value="1"]')).to_be_checked()
    back('investment');checks.append('我的计划未提交表单和嵌套设置返回来源均保留')
    act('journey-ask-plan');ready('assistant');page.locator('#chat-input').fill('未发送的聊天草稿')
    act('settings');ready('settings');back('assistant');expect(page.locator('#chat-input')).to_have_value('未发送的聊天草稿')
    back('investment');checks.append('助手从原入口返回，聊天草稿保留')
    act('journey-research');ready('research');act('dashboard');ready('dashboard');act('settings');ready('settings')
    page.reload(wait_until='networkidle');ready('settings');back('dashboard');back('research');back('investment')
    checks.append('历史投研子页面返回及刷新后来源保留')
    page.goto(FRONT+'/#/ledger',wait_until='networkidle');ready('ledger');act('statement-import');ready('import')
    assert page.locator('.tab-bar [aria-current="page"]').get_attribute('data-nav-tab')=='ledger'
    page.locator('#statement-account').fill('导航草稿账户');act('settings');ready('settings');back('import')
    expect(page.locator('#statement-account')).to_have_value('导航草稿账户');back('ledger')
    checks.append('导入流水正确归属账本，账户别名草稿保留')

    # Hold the read request so users can type into the visible loading form.
    # The completed page must retain that input, then apply exactly those dates.
    page=browser.new_page(viewport={'width':393,'height':852},locale='zh-CN')
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda req:writes.append(req.url) if '/api/' in req.url and req.method not in ['GET','HEAD','OPTIONS'] else None)
    page.goto(FRONT+'/#/visualization',wait_until='networkidle');ready('visualization')
    held=[]
    page.route('**/api/book/reports/aggregate?*',lambda route:held.append(route))
    page.locator('[data-action="visual-period"][data-value="自定义"]').click()
    page.wait_for_function('!!document.querySelector("main .loading #visual-start")')
    page.locator('#visual-start').fill('2026-08-10');page.locator('#visual-end').fill('2026-09-15')
    assert held,'The aggregate GET must be pending during date input'
    for route in held:route.continue_()
    page.unroute('**/api/book/reports/aggregate?*')
    ready('visualization')
    expect(page.locator('#visual-start')).to_have_value('2026-08-10')
    expect(page.locator('#visual-end')).to_have_value('2026-09-15')
    page.locator('#visual-range-form button[type="submit"]').click();ready('visualization')
    act('visual-insights');ready('insights')
    expect(page.locator('.period-stamp.center')).to_have_text('2026-08-10 — 2026-09-15')
    back('visualization')
    expect(page.locator('#visual-start')).to_have_value('2026-08-10')
    expect(page.locator('#visual-end')).to_have_value('2026-09-15')
    checks.append('延迟GET期间日期输入保留，应用后详细分析与返回均沿用正确区间')

    # Entry mutations are intercepted in a separate browser context. These
    # checks must never save a bill or invoke parsing on the running server.
    page=browser.new_page(viewport={'width':393,'height':852},locale='zh-CN')
    page.on('pageerror',lambda error:errors.append(str(error)))
    mocked=[]
    def isolated_entry_api(route):
        request=route.request
        if request.method in ['GET','HEAD','OPTIONS']:
            route.continue_();return
        if request.url.endswith('/api/book/bills/parse'):
            mocked.append('parse')
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'amount':45.67,'event_date':'2026-09-03','category':'购物','description':'解析后的测试草稿','type':'expense','payment_method':'支付宝','currency':'CNY'}));return
        if request.url.endswith('/api/book/bills'):
            body=request.post_data_json
            assert body['amount']==45.67 and body['description']=='解析后的测试草稿',body
            mocked.append('save')
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'id':900001,'event_date':'2026-09-03'}));return
        writes.append(request.url);route.abort()
    page.route('**/api/**',isolated_entry_api)
    page.goto(FRONT+'/#/entry',wait_until='networkidle');ready('entry')
    page.locator('#entry-amount').fill('12.34');page.locator('#entry-description').fill('将要丢弃的草稿')
    act('discard-draft');act('discard-confirm')
    expect(page.locator('#entry-amount')).to_have_value('');expect(page.locator('#entry-description')).to_have_value('')
    checks.append('丢弃草稿后金额和描述确实清空')
    page.locator('#entry-amount').fill('20.00');page.locator('#entry-description').fill('解析前的旧草稿')
    act('parse-entry');page.locator('#parse-text').fill('只用于隔离响应的解析请求');act('parse-confirm')
    expect(page.locator('#entry-amount')).to_have_value('45.67')
    expect(page.locator('#entry-description')).to_have_value('解析后的测试草稿')
    expect(page.locator('#entry-date')).to_have_value('2026-09-03')
    expect(page.locator('#entry-payment')).to_have_value('支付宝')
    checks.append('隔离模拟解析结果替换旧表单，不被离页同步覆盖')
    page.locator('#save-entry').click();ready('ledger');act('entry');ready('entry')
    expect(page.locator('#entry-amount')).to_have_value('');expect(page.locator('#entry-description')).to_have_value('')
    assert mocked==['parse','save'],mocked
    checks.append('隔离模拟保存后新账单保持空白，未向服务器写入账单')
    assert not errors,errors
    assert not writes,writes
    out.joinpath('results.json').write_text(json.dumps({'checks':checks,'javascript_errors':errors,'api_writes':writes,'mocked_entry_requests':mocked},ensure_ascii=False,indent=2))
    print(json.dumps({'checks':checks,'javascript_errors':errors,'api_writes':writes,'mocked_entry_requests':mocked},ensure_ascii=False,indent=2))
    browser.close()
