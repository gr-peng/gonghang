"""Exercise judge regressions against isolated app services; never production writes.
Start an isolated book/trader/frontend with a copied ledger before running.
"""
from pathlib import Path
import json, os, re, time
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.runtime/judge-fixes-20261004';BASE=os.getenv('JUDGE_TEST_BASE','http://127.0.0.1:25506')
assert BASE=='http://127.0.0.1:25506', 'This script writes fixtures; use the reserved isolated endpoint.'
OUT.mkdir(parents=True,exist_ok=True)
report={'checks':[],'layouts':[],'javascript_errors':[],'real_model_used':True,'screenshots':[]}
with sync_playwright() as pw:
 b=pw.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH','/home/pgr/.cache/ms-playwright/chromium-1181/chrome-linux/chrome'))
 c=b.new_context(viewport={'width':393,'height':852},locale='zh-CN',timezone_id='Asia/Shanghai');p=c.new_page();p.on('pageerror',lambda e:report['javascript_errors'].append(str(e)))
 def ready():
  p.wait_for_function('document.querySelector("main")&&!document.querySelector("main .loading")');p.wait_for_timeout(120)
 def visit(r):p.goto(BASE+'/#/'+r,wait_until='networkidle');ready();assert not p.locator('main .error').count(),p.locator('main').inner_text()
 def act(a):p.locator('[data-action="'+a+'"]').first.click();ready()
 def get(path):
  r=c.request.get(BASE+'/api/book'+path);assert r.ok,r.text();return r.json()
 def overview():return get('/finance/overview')
 def shot(name):p.screenshot(path=str(OUT/(name+'.png')),full_page=False);report['screenshots'].append(name+'.png')
 def check(name):report['checks'].append(name);print('PASS',name,flush=True);(OUT/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 def setgoal(name,amount,change=False):
  visit('profile');p.locator('#finance-goal').fill(name);p.locator('#finance-goal-amount').fill(str(amount));p.locator('#finance-months').fill('12');p.locator('#finance-profile-form button[type=submit]').click()
  if change:expect(p.locator('#sheet')).to_contain_text('目标已更换');act('journey-goal-replace')
  expect(p.locator('h1')).to_have_text('投资');ready()
 def progress(amount):
  act('goal-deposit');p.locator('#shared-goal-amount').fill(str(amount));p.locator('#shared-goal-progress-form button').click();expect(p.locator('#sheet')).not_to_be_visible();ready()
 def chat(text=None):
  if text:p.locator('#chat-input').fill(text)
  n=p.locator('.chat-message').count();p.locator('#chat-form button[type=submit]').click()
  p.wait_for_function(f'document.querySelectorAll(".chat-message").length>={n+2}&&!document.querySelector("#chat-messages .loading")',timeout=180000)
  return p.locator('.chat-message').last.inner_text()
 try:
  # All current routes at portrait and desktop widths use the same narrow app.
  routes=['home','ledger','visualization','assistant','investment','entry','insights','import','holdings','profile','compare','research','dashboard','risk','stock','bank','security','settings','improvement']
  for w in (360,393,1280):
   p.set_viewport_size({'width':w,'height':852})
   for route in routes:
    visit(route)
    size=p.evaluate('({page:document.documentElement.scrollWidth,app:document.querySelector("#app").getBoundingClientRect().width})')
    assert size['page']<=w+1 and size['app']<=430.5,(route,w,size)
    report['layouts'].append({'route':route,'viewport':w,**size})
  p.set_viewport_size({'width':393,'height':852});visit('home');shot('home');check('19 routes at 360/393/1280 preserve portrait glass layout, no overflow')
  visit('entry');assert p.locator('[data-action=ocr-entry]').count()==0
  act('parse-entry');p.locator('#parse-text').fill('今天午饭35元，打车18元，帮我记两笔。');act('parse-confirm')
  expect(p.locator('#sheet')).to_contain_text('多笔');assert p.locator('#entry-amount').input_value()=='';shot('multi-entry');act('close-sheet')
  check('Multi-entry clarifies without partial draft; unsupported OCR is absent')
  p.locator('#entry-amount').fill('38.60');p.locator('#entry-description').fill('评委回归午餐');p.locator('#entry-date').fill('2026-09-13');p.locator('#save-entry').click();expect(p.locator('h1')).to_have_text('账本');ready()
  row=p.locator('.bill-row').filter(has_text='评委回归午餐');bill_id=row.get_attribute('data-id');row.click();act('edit-bill');p.locator('#edit-amount').fill('48.60');p.locator('#edit-description').fill('评委回归午餐已校正');p.locator('#bill-edit-form button[type=submit]').click();expect(p.locator('#sheet')).not_to_be_visible();ready()
  expect(p.locator(f'.bill-row[data-id="{bill_id}"]')).to_contain_text('48.60');shot('bill-edited');check('Saved bill editable in place and reflected in ledger')
  act('filter');p.locator('#filter-category').select_option('餐饮');p.locator('#filter-type').select_option('expense');act('apply-filter')
  filtered=get('/bills?start_date=2026-09-01&end_date=2026-09-30&category=餐饮&type=expense&limit=500')
  total=sum(round(x['amount']*100) for x in filtered)/100
  expect(p.locator('#ledger-filter-summary')).to_contain_text(f'{total:,.2f}');shot('filter-subtotal')
  p.locator('#bill-search').fill('评委回归');expect(p.locator('#ledger-filter-summary')).to_contain_text('48.60')
  visit('investment');p.locator('.planning-basis summary').click();act('investment-basis-ledger');expect(p.locator('.range-caption')).to_contain_text('2026-04-01');expect(p.locator('.range-caption')).to_contain_text('2026-09-30');shot('ledger-range');check('Filtered subtotal follows search and category; planning ledger displays actual range')
  visit('home');p.locator('#month-picker').fill('2026-09');ready();act('ask');reply=chat()
  totals=get('/reports/aggregate?start_date=2026-09-01&end_date=2026-09-30')['custom']
  normalized=reply.replace(',','').replace('，','')
  for value in [totals['summary']['income_total'],totals['summary']['expense_total'],totals['net']['current']]:assert f'{value:.2f}' in normalized or str(value) in normalized,(value,reply)
  report['selected_month_reply']=reply;shot('september-assistant');check('Home September context produces matching real-model income, expense and balance')
  setgoal('旅行',12000);progress(3000);old=overview()['profile'];setgoal('电脑',3000,True)
  assert overview()['profile']['goal_recorded_amount']=='0' and overview()['profile']['goal_id']!=old['goal_id']
  progress(200);act('goal-history');act('goal-undo');act('goal-undo-confirm');assert overview()['profile']['goal_recorded_amount']=='0.00';check('New goal resets explicitly; progress history supports undo without changing bank')
  setgoal('旅行',12000,True);progress(3000);act('journey-ask-plan');reply=chat('目标每月需要预留多少？');assert '750' in reply,reply
  before=overview();reply=chat('如果改为6个月，每月留多少？只测算，不修改计划。');assert '1,500.00' in reply and '未改变' in reply,reply
  assert overview()['profile']==before['profile'] and overview()['cash_minor']==before['cash_minor'];shot('goal-scenario');check('Six-month what-if returns 1500 without changing twelve-month plan')
  visit('stock?code=600036&name=招商银行');expect(p.locator('main')).to_contain_text('行情待核对');assert p.locator('.quote-grid').count()==0
  act('stock-report');p.wait_for_function('document.querySelector(".report-section")',timeout=180000)
  report_text=p.locator('.report-preview').all_inner_texts();assert all('36.12' not in t for t in report_text)
  assert any('251' in t for t in report_text) and any('成交额' in t for t in report_text);shot('research-quality')
  visit('stock?code=600900&name=长江电力');expect(p.locator('.quote-grid')).to_be_visible();act('stock-report');p.wait_for_function('document.querySelector(".report-section")',timeout=180000);shot('research-valid');check('Bad OHLC hidden; report only uses verified dates, prices and explicit limitations')
  # Salary-only imported personal ledger must remain unqualified even with high risk answers.
  visit('import');content='日期,收支,金额,摘要,分类,流水号\n2026-07-05,收入,6800,工资,工资,JF-JUL\n2026-08-05,收入,6800,工资,工资,JF-AUG\n2026-09-05,收入,6800,工资,工资,JF-SEP\n'
  p.locator('#statement-file').set_input_files({'name':'salary-only.csv','mimeType':'text/csv','buffer':content.encode()});p.locator('#statement-account').fill('工资卡');p.locator('#statement-file-form button[type=submit]').click();ready();shot('import-preview');p.locator('#statement-review-form button[type=submit]').click();act('statement-commit');ready()
  visit('profile');p.locator('.profile-section').filter(has=p.locator('.risk-questions')).locator('summary').click()
  for i in range(4):p.locator(f'[name="risk-{i}"][value="2"]').check()
  p.locator('#finance-confirmed').check();p.locator('#finance-profile-form button[type=submit]').click();expect(p.locator('h1')).to_have_text('投资');ready();shot('incomplete-cashflow')
  s=overview();assert not s['planning']['ready'] and s['investable_minor']==0 and not s['products'][1]['eligible']
  visit('holdings');expect(p.locator('[data-holding-product="growth"] [data-action="journey-subscribe"]')).to_be_disabled();shot('holdings-incomplete');check('Salary-only import cannot unlock long-term product or display available funding')
  visit('bank');act('journey-transfer');p.locator('#finance-amount').fill('80');p.locator('#finance-operation-form button[type=submit]').click();expect(p.locator('#sheet')).to_contain_text('青财体验账户');expect(p.locator('#sheet')).to_contain_text('小林');assert 'acct-' not in p.locator('#sheet').inner_text();shot('confirmation');act('journey-cancel');check('Confirmation uses understandable account names; cancellation preserves money')
  assert not report['javascript_errors'],report['javascript_errors']
  report['final_bill_count']=get('/health')['bill_count'];report['cash_minor']=overview()['cash_minor'];assert report['cash_minor']==5000000
  check('Isolated fixture writes only, no bank movement, no JavaScript errors')
 except Exception as e:
  report['failure']=repr(e);shot('failure');raise
 finally:
  (OUT/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));b.close()
