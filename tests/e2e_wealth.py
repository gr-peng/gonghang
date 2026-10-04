"""Start an isolated demo and exercise wealth/education flows; no production writes.

Run with the project's Python environment (Playwright Chromium installed).
Evidence is written to a fresh .runtime/wealth-review-* directory.
"""
from pathlib import Path
from datetime import datetime
import json
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.runtime' / ('wealth-review-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(parents=True)
(OUT/'screenshots').mkdir()
sockets=[]
for _ in range(3):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sockets.append(sock)
front,book,trader=[s.getsockname()[1] for s in sockets]
for s in sockets:s.close()
base=f'http://127.0.0.1:{front}'
env={**os.environ,'LLM_PROVIDER':'disabled','ACCOUNTING_MODEL_SERVER':'false',
     'FRONTEND_PORT':str(front),'BOOKKEEPER_PORT':str(book),'TRADER_PORT':str(trader),
     'AI_BOOKKEEPER_IMPORT_JSONL':'false','AI_TRADER_DATA_DIR':str(ROOT/'AI_accounting_agent/backend/data'),
     'FINANCE_STATE_DIR':str(OUT/'finance-state'),'FINANCE_PUBLIC_ORIGINS':base,'PYTHONUTF8':'1'}
report={'real_model_used':False,'production_writes':0,'checks':[],'layouts':[],'javascript_errors':[]}
def get(path):
    with urlopen(base+path,timeout=10) as r:return json.load(r)
log=(OUT/'startup.log').open('w')
process=subprocess.Popen([sys.executable,'run.py','--demo','--no-install','--no-browser'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
try:
    for _ in range(120):
        if process.poll() is not None:raise RuntimeError((OUT/'startup.log').read_text())
        try:
            health=get('/api/book/health')
            if health['bill_count']==6:break
        except Exception:pass
        time.sleep(.5)
    else:raise RuntimeError('Isolated service did not start')
    assert Path(health['database']).is_relative_to(ROOT/'.runtime')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_CHROMIUM','/home/pgr/.cache/ms-playwright/chromium-1181/chrome-linux/chrome'))
        context=browser.new_context(viewport={'width':393,'height':852},locale='zh-CN')
        page=context.new_page();page.on('pageerror',lambda error:report['javascript_errors'].append(str(error)))
        def route(name):
            page.goto(base+'/#/'+name,wait_until='networkidle')
            page.wait_for_function('document.querySelector("main") && !document.querySelector("main .loading")')
            assert not page.locator('main .error').count(),page.locator('main').inner_text()
        def overview():return page.evaluate("async()=>await (await fetch('/api/book/finance/overview')).json()")
        def save():
            page.locator('#wealth-account-form [type="submit"]').click()
            expect(page.locator('#sheet')).not_to_be_visible()
            page.wait_for_function('!document.querySelector("main .loading")')
        def add(kind,name,amount,category):
            page.locator(f'[data-action="wealth-add"][data-kind="{kind}"]').click()
            page.locator('#wealth-name').fill(name)
            page.locator('#wealth-category').select_option(category)
            page.locator('#wealth-amount').fill(amount)
        def verify():
            page.locator('[data-action="wealth-review"]').first.click()
            page.locator('#wealth-review-form [name="complete"]').check()
            page.locator('#wealth-review-form [type="submit"]').click()
            expect(page.locator('#sheet')).not_to_be_visible()
            page.wait_for_function('!document.querySelector("main .loading")')
        route('wealth');before=overview()
        expect(page.locator('[data-wealth-metric="net"]')).to_have_text('待核对')
        report['checks'].append('empty inventory remains unknown')
        add('asset','生活账户','20000','cash')
        page.locator('#wealth-available').fill('18000')
        page.locator('#sheet [data-term="liquidity"]').click()
        expect(page.locator('#sheet')).to_contain_text('锁定期')
        page.keyboard.press('Escape')
        expect(page.locator('#wealth-name')).to_have_value('生活账户')
        expect(page.locator('#wealth-available')).to_have_value('18000')
        page.locator('#wealth-account-form summary').click()
        page.locator('#wealth-emergency').fill('6000')
        page.locator('#wealth-goal').fill('2000')
        page.locator('#wealth-other_reserved').fill('1000')
        save();report['checks'].append('context help preserves unsaved form through Escape')
        add('asset','基金估值','7000','fund');save()
        add('liability','信用卡待还','4000','credit');page.locator('#wealth-due').fill('1500');save()
        expect(page.locator('[data-wealth-metric="net"]')).to_have_text('¥23,000.00')
        expect(page.locator('[data-wealth-metric="available"]')).to_have_text('待核对')
        verify()
        expect(page.locator('[data-wealth-metric="available"]')).to_have_text('¥7,500.00')
        report['checks'].append('assets, liabilities, reservations and 30-day dues yield correct totals')
        expect(page.locator('#toast')).not_to_be_visible(timeout=5000)
        page.evaluate('window.scrollTo(0,0)')
        page.wait_for_function('window.scrollY === 0')
        page.screenshot(path=str(OUT/'screenshots/wealth.png'))
        page.locator('[data-action="mask"]').click()
        expect(page.locator('[data-wealth-metric="net"]')).to_have_text('••••')
        assert all(v=='0%' for v in page.locator('.wealth-allocation .progress-bar span').evaluate_all('(els)=>els.map(e=>e.style.width)'))
        page.locator('[data-action="mask"]').click()
        report['checks'].append('privacy masks totals and allocation bars')
        page.locator('[data-action="wealth-filter"][data-value="asset"]').click()
        expect(page.locator('.wealth-account')).to_have_count(2)
        page.locator('[data-action="wealth-filter"][data-value="all"]').click()
        page.locator('.wealth-account').filter(has_text='生活账户').click()
        page.locator('#wealth-amount').fill('21000');save()
        expect(page.locator('[data-wealth-metric="available"]')).to_have_text('待核对')
        verify()
        report['checks'].append('editing invalidates completeness and filters remain usable')
        page.locator('[data-action="wealth-ask"]').click()
        expect(page.locator('#chat-input')).to_have_value('我的净资产和可动用资金是多少？')
        page.locator('#chat-form [type="submit"]').click()
        expect(page.locator('.chat-message:not(.user)').last).to_contain_text('24,000.00',timeout=15000)
        expect(page.locator('.chat-message:not(.user)').last).to_contain_text('7,500.00')
        page.locator('.chat-message:not(.user) [data-action="wealth-open"]').last.click()
        expect(page.locator('h1')).to_have_text('资产总览')
        report['checks'].append('assistant uses inventory facts and returns to wealth')
        route('compare')
        page.locator('[data-term="risk"]').click()
        expect(page.locator('#sheet')).to_contain_text('不是收益或保本承诺')
        page.screenshot(path=str(OUT/'screenshots/learning.png'))
        page.locator('#sheet [data-action="learn-scenario"]').click()
        page.locator('#wealth-scenario-form [type="submit"]').click()
        expect(page.locator('#scenario-result')).to_contain_text('¥8,955.00')
        expect(page.locator('#scenario-result')).to_contain_text('-¥1,045.00')
        page.screenshot(path=str(OUT/'screenshots/scenario.png'))
        page.locator('#scenario-change').fill('0')
        expect(page.locator('#scenario-result')).to_be_empty()
        page.locator('#wealth-scenario-form [type="submit"]').click()
        expect(page.locator('#scenario-result')).to_contain_text('¥9,950.00')
        report['checks'].append('risk explanation and fee scenario work; changes clear stale result')
        page.keyboard.press('Escape')
        page.locator('[data-action="learn-library"]').click()
        page.locator('#learn-search').fill('回撤')
        expect(page.locator('.learn-list button:visible')).to_have_count(1)
        page.locator('.learn-list button:visible').focus();page.keyboard.press('Enter')
        expect(page.locator('#sheet')).to_contain_text('25%')
        page.keyboard.press('Escape')
        report['checks'].append('glossary search and keyboard navigation')
        route('wealth')
        page.locator('.wealth-account').filter(has_text='基金估值').click()
        page.locator('[data-action="wealth-delete"]').click()
        page.locator('#wealth-delete-form [type="submit"]').click()
        expect(page.locator('#sheet')).not_to_be_visible()
        expect(page.locator('.wealth-account')).to_have_count(2)
        expect(page.locator('[data-wealth-metric="net"]')).to_have_text('¥17,000.00')
        report['checks'].append('delete requires explicit confirmation and recalculates')
        after=overview()
        for key in ['cash_minor','assets_minor','operations','products','investable_minor','planning']:
            assert after[key]==before[key],key
        assert get('/api/book/health')['bill_count']==6
        report['checks'].append('simulated balances, operations and ledger unchanged')
        routes=['home','ledger','visualization','assistant','investment','wealth','entry','insights','import','holdings','profile','compare','research','dashboard','risk','stock','bank','security','settings','improvement']
        for width in (360,393,1280):
            page.set_viewport_size({'width':width,'height':852})
            for name in routes:
                route(name)
                size=page.evaluate('({page:document.documentElement.scrollWidth,app:document.querySelector("#app").getBoundingClientRect().width})')
                assert size['page']<=width+1 and size['app']<=430.5,(name,width,size)
                report['layouts'].append({'route':name,'viewport':width,**size})
        page.set_viewport_size({'width':393,'height':852})
        for name in ['home','compare','investment']:
            route(name);page.screenshot(path=str(OUT/'screenshots'/f'{name}.png'))
        browser.close()
    assert not report['javascript_errors'],report['javascript_errors']
    report['passed']=True
except Exception as exc:
    report['passed']=False;report['failure']=str(exc);raise
finally:
    if process.poll() is None:
        os.killpg(process.pid,signal.SIGINT)
        try:process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)
    log.close();report['isolated_launcher_stopped']=process.poll() is not None
    (OUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'evidence':str(OUT),'passed':report.get('passed'),'failure':report.get('failure'),'checks':len(report['checks']),'layouts':len(report['layouts'])},ensure_ascii=False))
