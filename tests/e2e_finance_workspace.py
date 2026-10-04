"""Browser acceptance through both real proxies, with an isolated ledger/bank.

Never changes the running app's bank state, profile, MFA key, or user ledger.
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
from pathlib import Path
import secrets
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.serve_remote_access import make_handler

REAL_MODEL = os.getenv('E2E_FINANCE_REAL_MODEL') == '1'
if REAL_MODEL:
    from run import read_env
    read_env()

OUT = Path(os.getenv('E2E_FINANCE_OUTPUT', str(ROOT / '.runtime/competition-iteration/browser')))
OUT.mkdir(parents=True, exist_ok=True)
config = json.loads((ROOT / '.runtime/remote-access.json').read_text())
config.update(port=25505, upstream_port=25504, token=secrets.token_urlsafe(32))
base = f"http://{config['host']}:{config['port']}"
checks = []


def check(text):
    checks.append(text)
    print('PASS', text, flush=True)


def totp(secret):
    count = int(time.time() // 30)
    digest = hmac.new(base64.b32decode(secret), struct.pack('>Q', count), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return f'{(struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff) % 1000000:06d}'


with tempfile.TemporaryDirectory(prefix='qingcai-finance-e2e-', dir=ROOT / '.runtime') as folder:
    directory = Path(folder)
    source = sqlite3.connect(f"file:{ROOT / 'artifacts/accounting-v2/ledger/bills.db'}?mode=ro", uri=True)
    copy = sqlite3.connect(directory / 'bills.db')
    source.backup(copy)
    before = source.execute('SELECT COUNT(*) FROM bills').fetchone()[0]
    copy.close()
    source.close()
    env = dict(os.environ, AI_BOOKKEEPER_DATA_DIR=str(directory), AI_BOOKKEEPER_IMPORT_JSONL='false',
               BOOKKEEPER_PORT='28011', TRADER_PORT=os.getenv('E2E_TRADER_PORT','28020'), FRONTEND_PORT='25504',
               FINANCE_PUBLIC_ORIGINS=base, LLM_PROVIDER='api' if REAL_MODEL else 'disabled')
    env.pop('FINANCE_STATE_DIR', None)
    logs, processes = [], []
    entry = None
    try:
        for name, command in [('book', [sys.executable, '-m', 'uvicorn', 'app:app', '--app-dir',
                str(ROOT / 'AI_accounting_agent/backend'), '--host', '127.0.0.1', '--port', '28011']),
                ('front', [sys.executable, str(ROOT / 'scripts/serve_frontend.py')])]:
            log = open(OUT / f'{name}.log', 'w')
            logs.append(log)
            processes.append(subprocess.Popen(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
        deadline = time.time() + 25
        while True:
            connection = http.client.HTTPConnection('127.0.0.1', 25504, timeout=1)
            try:
                connection.request('GET', '/api/book/health')
                response = connection.getresponse()
                if response.status == 200:
                    response.read()
                    break
            except OSError:
                pass
            finally:
                connection.close()
            if any(p.poll() is not None for p in processes) or time.time() > deadline:
                raise RuntimeError('Isolated app failed to become healthy')
            time.sleep(.25)
        entry = ThreadingHTTPServer((config['host'], config['port']), make_handler(config))
        thread = threading.Thread(target=entry.serve_forever, daemon=True)
        thread.start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH'))
            page = browser.new_page(viewport={'width':393, 'height':852}, locale='zh-CN')
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base + '/__open/' + config['token'], wait_until='networkidle')

            def visit(route):
                page.goto(base + '/#/' + route, wait_until='networkidle')
                page.wait_for_function("!document.querySelector('main .loading')")
                assert not page.locator('main .error').count(), (route, page.locator('main').inner_text())

            def action(name):
                page.locator(f'[data-action="{name}"]').first.click()

            def overview():
                return page.request.get(base + '/api/book/finance/overview').json()

            def make_transfer(amount):
                action('journey-transfer')
                page.locator('#finance-amount').fill(amount)
                page.locator('#finance-operation-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text(re.compile('等待确认|需要验证码'))

            try:
                visit('home')
                expect(page.locator('nav a')).to_have_count(5)
                expect(page.locator('main')).to_contain_text('结余规划')
                page.screenshot(path=str(OUT / 'home.png'), full_page=True)
                visit('profile')
                page.locator('#finance-stage').select_option('early_career')
                page.locator('#finance-goal').fill('毕业旅行 <核对>')
                page.locator('#finance-goal-amount').fill('3000.00')
                page.locator('#finance-months').fill('12')
                page.locator('.profile-section').filter(has=page.locator('.risk-questions')).locator('summary').click()
                page.locator('#finance-confirmed').check()
                for i in range(4):
                    page.locator(f'input[name="risk-{i}"][value="0"]').check()
                page.locator('#finance-profile-form button[type="submit"]').click()
                expect(page.locator('h1')).to_have_text('投资')
                expect(page.locator('main')).to_contain_text('毕业旅行 <核对>')
                assert overview()['profile']['goal_amount'] == '3000.00'
                page.screenshot(path=str(OUT / 'investment.png'), full_page=True)
                check('现金流、生活阶段、风险偏好与目标计划相连，保存后投资页使用相同数据')

                page.evaluate("localStorage.setItem('QINGCAI_SAVINGS_GOAL',JSON.stringify({name:'旧旅行 <安全>',target:4500,cycle:'week',percent:30,manual:350.5}))")
                visit('visualization')
                action('goal-migrate')
                expect(page.locator('#sheet')).to_contain_text('旧旅行 <安全>')
                action('goal-migrate-confirm')
                expect(page.locator('#finance-goal')).to_have_value('旧旅行 <安全>')
                assert overview()['profile']['goal_recorded_amount']=='350.50'
                visit('visualization')
                action('goal-deposit')
                page.locator('#shared-goal-amount').fill('49.50')
                page.locator('#shared-goal-progress-form button[type="submit"]').click()
                expect(page.locator('#sheet')).not_to_be_visible()
                assert overview()['profile']['goal_recorded_amount']=='400.00'
                check('图表与个人计划使用同一目标，旧周期/比例/记录迁移后保留，进度不伪造银行资产')

                visit('bank')
                balance = overview()['cash_minor']
                make_transfer('10.00')
                expect(page.locator('#sheet')).to_contain_text('等待确认')
                expect(page.locator('#sheet')).to_contain_text('小林')
                assert overview()['cash_minor'] == balance
                page.screenshot(path=str(OUT / 'confirmation.png'), full_page=True)
                page.locator('#finance-confirm-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text('已完成')
                assert overview()['cash_minor'] == balance - 1000
                action('journey-done')
                page.reload(wait_until='networkidle')
                expect(page.locator('main')).to_contain_text('已完成')
                assert overview()['cash_minor'] == balance - 1000
                check('双层代理传递会话和 CSRF，核对完整参数后真实模拟划转；刷新不重复扣款')

                visit('holdings')
                action('journey-subscribe')
                page.locator('#finance-amount').fill('80.00')
                page.locator('#finance-operation-form button[type="submit"]').click()
                page.locator('#finance-confirm-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text('已完成')
                action('journey-done')
                page.wait_for_function("!document.querySelector('main .loading')")
                assert overview()['products'][0]['holding_minor'] == 8000
                action('journey-redeem')
                page.locator('#finance-amount').fill('80.00')
                page.locator('#finance-operation-form button[type="submit"]').click()
                page.locator('#finance-confirm-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text('已完成')
                action('journey-done')
                assert overview()['products'][0]['holding_minor'] == 0
                assert overview()['cash_minor'] == balance - 1000
                assert overview()['assets_minor'] == balance - 1000
                check('模拟申购和赎回实际划转本金，现金/持仓/总资产一致，未计成收入或消费')

                extra_cash = 0
                if REAL_MODEL:
                    visit('assistant')
                    page.locator('[data-action="chat-mode"][data-value="规划"]').click()
                    page.locator('#chat-input').fill('看看我的个人计划')
                    with page.expect_response(lambda r:r.url.endswith('/api/book/finance/assistant'),timeout=180000) as planned:
                        page.locator('#chat-form button[type="submit"]').click()
                    grounded=planned.value.json()
                    assert grounded.get('explanation_status')=='model_grounded',grounded
                    assert grounded['plan_next']=='compare'
                    expect(page.locator('#chat-messages [data-action="journey-compare"]')).to_be_visible()
                    page.locator('#chat-messages [data-action="journey-compare"]').click()
                    expect(page.locator('h1')).to_have_text('方案比较')
                    check('真实模型读取画像与账单汇总组织可信依据，下一步按钮进入对应比较页面')
                    visit('improvement')
                    expect(page.locator('main')).to_contain_text('未加入')
                    action('improvement-optin')
                    action('improvement-enable')
                    expect(page.locator('main')).to_contain_text('已加入')
                    visit('assistant')
                    page.locator('[data-action="chat-mode"][data-value="规划"]').click()
                    action('chat-add')
                    expect(page.locator('.template-item')).to_have_count(5)
                    page.locator('.template-item').first.click()
                    expect(page.locator('#chat-input')).to_have_value('转给小林 80 元')
                    before_ai = overview()['cash_minor']
                    with page.expect_response(lambda r:r.url.endswith('/api/book/finance/assistant'),timeout=180000) as reply:
                        page.locator('#chat-form button[type="submit"]').click()
                    assert reply.value.status == 200
                    expect(page.locator('[data-action="journey-chat-draft"]')).to_be_visible()
                    assert overview()['cash_minor'] == before_ai
                    action('journey-chat-draft')
                    expect(page.locator('#finance-amount')).to_have_value('80.00')
                    page.locator('#finance-amount').fill('180.00')
                    page.locator('#finance-operation-form button[type="submit"]').click()
                    page.locator('#finance-confirm-form button[type="submit"]').click()
                    expect(page.locator('#sheet')).to_contain_text('已完成')
                    action('journey-done')
                    assert overview()['cash_minor'] == before_ai - 18000
                    # The same chat draft keeps its original id after refresh/reopen.
                    action('journey-chat-draft')
                    page.locator('#finance-operation-form button[type="submit"]').click()
                    expect(page.locator('#sheet')).to_contain_text('已完成')
                    action('journey-done')
                    assert overview()['cash_minor'] == before_ai - 18000
                    extra_cash = 18000
                    page.screenshot(path=str(OUT / 'finance-assistant.png'),full_page=True)
                    check('真实微调模型→可编辑草稿→参数确认→模拟执行，重复打开同一聊天草稿不再扣款')
                    visit('improvement')
                    expect(page.locator('.feedback-sample')).to_have_count(1)
                    expect(page.locator('.feedback-sample')).to_contain_text('待审核')
                    assert '180.00' not in page.locator('.feedback-sample').inner_text()
                    action('improvement-approve')
                    expect(page.locator('.feedback-sample')).to_contain_text('已审核，待训练')
                    from scripts.build_finance_corpus import reviewed_feedback
                    fixtures=reviewed_feedback(directory / '.qingcai-workspace/runtime.sqlite3')
                    assert len(fixtures)==1
                    (OUT/'approved-feedback-fixture.json').write_text(json.dumps({'provenance':'isolated_browser_developer_fixture_not_real_user','rows':fixtures},ensure_ascii=False,indent=2))
                    check('默认关闭→同意→改正模型草稿→脱敏候选→显式审核→真实训练出口，未包含原文或精确金额')

                visit('bank')
                make_transfer('1000.00')
                expect(page.locator('#finance-confirm-form button[type="submit"]')).to_be_disabled()
                action('journey-mfa')
                page.locator('#sheet details summary').click()
                secret = page.locator('.mfa-secret').inner_text()
                enrollment_code = totp(secret)
                page.locator('#finance-activation-code').fill(enrollment_code)
                page.locator('#finance-mfa-form button[type="submit"]').click()
                expect(page.locator('#finance-code')).to_be_visible()
                while totp(secret) == enrollment_code:
                    page.wait_for_timeout(400)
                page.locator('#finance-code').fill(totp(secret))
                page.locator('#finance-confirm-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text('已完成')
                action('journey-done')
                assert overview()['cash_minor'] == balance - 101000 - extra_cash
                visit('security')
                expect(page.locator('main')).to_contain_text('已开启')
                expect(page.locator('.finance-operation-row')).to_have_count(5 if REAL_MODEL else 4)
                page.screenshot(path=str(OUT / 'security.png'), full_page=True)
                check('累计大额阻止直接提交，二维码注册/动态码验证/保护回执实际生效')

                for width in [360,393,1280]:
                    page.set_viewport_size({'width':width, 'height':900})
                    for route in ['home','ledger','entry','assistant','visualization','insights','investment','holdings',
                                  'stock','dashboard','risk','settings','profile','bank','security','compare','research','import','improvement']:
                        visit(route)
                        assert page.evaluate('document.documentElement.scrollWidth') <= width, (route,width)
                visit('compare')
                page.screenshot(path=str(OUT / 'compare-desktop.png'), full_page=True)
                check('19 个页面在 360/393/1280 宽度可用，无页面溢出或 JavaScript 错误')

                page.set_viewport_size({'width':393,'height':852})
                visit('import')
                statement='日期,收支,金额,摘要,分类,流水号,资金性质\n2026-09-01,收入,6800.00,工资,工资,A,收支\n2026-09-02,支出,35.50,午餐,餐饮,B,收支\n2026-09-03,支出,500.00,自己的另一账户,其他,C,内部划转\n'
                page.locator('#statement-file').set_input_files({'name':'bank.csv','mimeType':'text/csv','buffer':statement.encode('utf-8-sig')})
                page.locator('#statement-file-form button[type="submit"]').click()
                expect(page.locator('.statement-row')).to_have_count(3)
                assert page.request.get(base+'/api/book/health').json()['bill_count']==before
                page.locator('[data-import-row="2"][data-import-field="category"]').select_option('其他')
                page.screenshot(path=str(OUT/'import-preview.png'),full_page=True)
                page.locator('#statement-review-form button[type="submit"]').click()
                expect(page.locator('#sheet')).to_contain_text('本金 / 内转')
                action('statement-commit')
                expect(page.locator('h1')).to_have_text('账本')
                expect(page.locator('header')).to_contain_text('个人账本')
                assert overview()['profile']['ledger_scope']=='personal'
                assert page.request.get(base+'/api/book/health').json()['bill_count']==before+3
                report=page.request.get(base+'/api/book/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30').json()['custom']
                # Preserved original user rows may also occur in this month; verify
                # imported internal transfer cannot contribute to the category total.
                other=next((p['value'] for p in report['pie'] if p['name']=='其他'),0)
                assert other==35.5
                visit('import')
                page.locator('#statement-file').set_input_files({'name':'same.csv','mimeType':'text/csv','buffer':statement.encode('utf-8-sig')})
                page.locator('#statement-file-form button[type="submit"]').click()
                expect(page.locator('.statement-row')).to_have_count(3)
                page.locator('[data-import-row="2"][data-import-field="category"]').select_option('其他')
                page.locator('#statement-review-form button[type="submit"]').click()
                action('statement-commit')
                expect(page.locator('h1')).to_have_text('账本')
                assert page.request.get(base+'/api/book/health').json()['bill_count']==before+3
                check('CSV 预览与分类修正明确确认后才入账；再次导入去重，个人报表隔离合成收入和内部划转')
                visit('settings')
                page.locator('#pref-mask').check()
                visit('investment')
                assert '¥' not in page.locator('main').inner_text()
                visit('bank')
                make_transfer('10.00')
                expect(page.locator('#finance-confirm-form button[type="submit"]')).to_be_disabled()
                action('journey-reveal')
                expect(page.locator('#sheet')).to_contain_text('小林')
                action('journey-cancel')
                expect(page.locator('#sheet')).to_contain_text('已取消')
                check('隐藏金额覆盖新页面，参数隐藏时不能确认，取消不产生资金变动')
                assert not errors, errors
                count = page.request.get(base + '/api/book/health').json()['bill_count']
                assert count == before+3
                result={'checks': checks, 'javascript_errors':errors, 'isolated_ledger_count':count,
                        'bank_mode':'durable_mock_only','production_bank_unchanged':True,
                        'mfa_secret_logged':False, 'real_model_used': REAL_MODEL}
                (OUT / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            finally:
                browser.close()
    finally:
        if entry:
            entry.shutdown()
            entry.server_close()
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
