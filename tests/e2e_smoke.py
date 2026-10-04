"""Real Chromium + real FastAPI smoke tests. AI success uses an explicit test response.
Run: python tests/e2e_smoke.py (requires playwright + installed chromium).
"""
from datetime import date
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'preview'
OUT.mkdir(parents=True, exist_ok=True)
proc = subprocess.Popen([sys.executable, 'run.py', '--no-install', '--no-browser', '--demo'], cwd=ROOT, env={**os.environ, 'LLM_PROVIDER':'disabled'}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=os.name!='nt')
checks=[]


def check(name):
    checks.append(name)
    print('PASS', name, flush=True)


try:
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(proc.communicate()[0])
        try:
            with urlopen('http://127.0.0.1:8010/bills') as r:
                data=json.load(r)
            with urlopen('http://127.0.0.1:5500/'):
                if len(data)>=6:break
        except Exception:
            pass
        time.sleep(.15)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':393,'height':852},device_scale_factor=1)
        errors=[];external=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1','data:')) else None)
        def visit(route,ready):
            page.goto('http://127.0.0.1:5500/#/'+route)
            page.wait_for_selector(ready)
            page.wait_for_function("!document.querySelector('main .loading')")
        def screenshot(name):
            page.screenshot(path=str(OUT / (name+'.png')),full_page=True)
        visit('home','.hero-amount')
        expect(page.locator('.hero-amount')).to_contain_text('8,482.00')
        screenshot('P01-home')
        check('首页从真实后端读取示例账本')
        page.locator('[data-action="mask"]').click()
        expect(page.locator('.hero-amount')).to_have_text('••••')
        expect(page.locator('.chart-hidden')).to_be_visible()
        page.reload();expect(page.locator('.hero-amount')).to_have_text('••••')
        page.locator('[data-action="mask"]').click()
        check('金额隐藏、图表隐藏与刷新持久化')
        visit('entry','#entry-form')
        page.locator('#entry-amount').fill('18.25')
        page.locator('#entry-description').fill('端到端测试午餐 <img src=x onerror=alert(1)>')
        page.locator('#entry-payment').select_option('微信支付')
        screenshot('P03-entry')
        page.locator('[data-action="back"]').click()
        page.locator('[data-action="entry"]').first.click()
        expect(page.locator('#entry-amount')).to_have_value('18.25')
        page.locator('#save-entry').click()
        page.wait_for_url('**/#/ledger')
        page.wait_for_selector('.bill-row')
        expect(page.locator('main')).to_contain_text('3,536.25')
        expect(page.locator('.bill-row').first).to_contain_text('<img src=x onerror=alert(1)>')
        assert page.locator('.bill-row img').count()==0
        check('草稿保留、保存写入、报表立即更新、文本转义')
        page.locator('#bill-search').fill('端到端测试')
        expect(page.locator('.bill-row')).to_have_count(1)
        page.locator('.bill-row').click()
        page.locator('[data-action="delete-prompt"]').click()
        expect(page.locator('#sheet')).to_be_visible()
        page.locator('[data-action="delete-confirm"]').click()
        page.wait_for_function("!document.querySelector('#sheet').open")
        expect(page.locator('.metric').last).to_contain_text('3,518.00')
        page.locator('#bill-search').fill('')
        page.locator('[data-action="filter"]').click()
        page.locator('#filter-category').select_option('餐饮')
        page.locator('[data-action="apply-filter"]').click()
        page.wait_for_selector('.bill-row')
        expect(page.locator('.bill-row')).to_have_count(1)
        screenshot('P02-ledger')
        check('账单搜索、分类筛选、确认删除及统计回退')
        visit('insights','.hero-amount')
        page.locator('[data-action="period"][data-value="年"]').click()
        page.wait_for_selector('.hero-amount')
        page.locator('[data-action="insight-tab"][data-value="习惯"]').click()
        expect(page.locator('main')).to_contain_text('最近 180 天')
        page.locator('[data-action="insight-tab"][data-value="建议"]').click()
        expect(page.locator('main')).to_contain_text('本期收支观察')
        page.locator('[data-action="insight-tab"][data-value="收支"]').click()
        page.locator('[data-action="period"][data-value="月"]').click()
        page.wait_for_selector('.hero-amount')
        screenshot('P05-insights')
        check('分析三视图与周月年范围切换')
        visit('investment','.stock-row')
        expect(page.locator('main')).to_contain_text('2025-12-05')
        page.locator('[data-action="stock"][data-code="NVDA"]').click()
        expect(page.locator('main')).to_contain_text('没有这只证券的行情')
        page.locator('[data-action="watchlist"]').click()
        page.wait_for_selector('[data-code="600036"]')
        screenshot('P06-investment')
        page.locator('[data-action="stock"][data-code="600036"]').click()
        expect(page.locator('main')).to_contain_text('2025-12-04')
        page.locator('[data-action="favorite"]').click()
        expect(page.locator('[data-action="favorite"]')).to_contain_text('已关注')
        page.locator('[data-action="stock-days"][data-value="30日"]').click()
        page.wait_for_selector('.chart')
        page.locator('[data-action="stock-tab"][data-value="资讯"]').click()
        page.wait_for_selector('.news-row')
        page.locator('.news-row').first.click()
        expect(page.locator('#sheet')).to_be_visible()
        page.locator('[data-action="close-sheet"]').click()
        page.locator('[data-action="stock-tab"][data-value="日报"]').click()
        page.wait_for_selector('[data-action="stock-report"]')
        screenshot('P07-stock')
        check('历史快照、A10 日期、未覆盖持仓、资讯、关注、区间行情')
        page.locator('[data-action="stock-report"]').click()
        expect(page.locator('#sheet')).to_contain_text('尚未配置')
        page.locator('[data-action="close-sheet"]').click()
        visit('assistant','#chat-input')
        page.locator('#chat-input').fill('本月支出是多少？')
        page.locator('[data-action="send"]').click()
        expect(page.locator('.chat-message').last).to_contain_text('尚未配置')
        check('AI 未配置时返回真实错误，不伪造回复')
        page.route('**/chat',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'reply':'测试服务响应：此处验证聊天接口接线。'})))
        page.locator('#chat-input').fill('接口接线测试')
        page.locator('[data-action="send"]').click()
        expect(page.locator('.chat-message').last).to_contain_text('测试服务响应')
        page.locator('[data-action="chat-mode"][data-value="投研"]').click()
        page.locator('#chat-input').fill('组合接口测试')
        page.locator('[data-action="send"]').click()
        expect(page.locator('.chat-message').last).to_contain_text('测试服务响应')
        screenshot('P04-assistant')
        check('双模式助手请求与响应显示（成功响应为明确测试替身）')
        page.unroute('**/chat')
        visit('settings','#service-status')
        assert not page.locator('details').get_attribute('open')
        page.locator('[data-action="health"]').click()
        expect(page.locator('#service-status')).to_contain_text('已连接')
        page.locator('#pref-reduceTransparency').check()
        page.locator('#pref-reduceMotion').check()
        assert page.locator('html').get_attribute('data-reduce-transparency')=='true'
        page.locator('#pref-reduceTransparency').uncheck()
        page.locator('#pref-reduceMotion').uncheck()
        screenshot('P08-settings')
        check('高级设置默认收起、双服务检测、外观设置')
        for width in [360,393,430,1280]:
            page.set_viewport_size({'width':width,'height':852})
            for route,ready in [('home','.hero-amount'),('ledger','.bill-row'),('entry','#entry-form'),('assistant','#chat-input'),('insights','.hero-amount'),('investment','.stock-row'),('stock?code=600036&name=招商银行','.chart'),('settings','#service-status')]:
                visit(route,ready)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,route)
                if route=='assistant':
                    a=page.locator('.composer').bounding_box();b=page.locator('.tab-bar').bounding_box()
                    assert a['y']+a['height']<=b['y'],(a,b)
        check('360/393/430/1280 宽度八页无横向溢出，输入栏与导航不重叠')
        page.route('**/reports/aggregate?*',lambda route:route.abort())
        visit('home','.error')
        expect(page.locator('main')).to_contain_text('无法连接服务')
        assert page.locator('.hero-amount').count()==0
        check('服务失败显示错误，不回退成设计稿金额')
        assert not errors,errors
        assert not external,external
        check('无浏览器脚本错误、无外部 CDN 请求')
        browser.close()
    (ROOT/'docs'/'e2e-results.json').write_text(json.dumps({'checks':checks,'count':len(checks),'browser':'Chromium 140 / Playwright 1.55','real_model_tested':False},ensure_ascii=False,indent=2),encoding='utf-8')
finally:
    if proc.poll() is None:
        if os.name=='nt':proc.terminate()
        else:os.killpg(proc.pid,signal.SIGINT)
    try:output=proc.communicate(timeout=15)[0]
    except subprocess.TimeoutExpired:
        if os.name!='nt':os.killpg(proc.pid,signal.SIGKILL)
        else:proc.kill()
        output=proc.communicate()[0]
    print(output[-1200:])
