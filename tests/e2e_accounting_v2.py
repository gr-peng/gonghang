"""Live acceptance of Liquid Glass, shared API proxy and the real fine-tuned model.

Creates a uniquely tagged test bill and deletes only that record in finally.
"""
import json
import os
from pathlib import Path
import uuid
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
FRONT = 'http://127.0.0.1:25500'
OUT = ROOT / '.runtime/accounting-v2-validation'
OUT.mkdir(parents=True, exist_ok=True)


def main():
    checks = []
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH'))
        page = browser.new_page(viewport={'width':1280,'height':900})
        errors = []
        page.on('pageerror',lambda e:errors.append(str(e)))
        created_id = None
        def visit(route):
            page.goto(FRONT + '/#/' + route)
            page.wait_for_selector('main')
            page.wait_for_function("!document.querySelector('main .loading')")
            assert page.locator('main .error').count()==0, route
        try:
            health = page.request.get(FRONT+'/api/book/health').json()
            assert health['bill_count']==1155
            assert health['llm']['loaded'] and health['llm']['fine_tuned']
            assert len(health['category_schema']['expense'])==12
            assert len(health['category_schema']['income'])==6
            for route in ['home','ledger','entry','assistant','insights','investment','stock','settings']:
                visit(route)
            checks.append('八个页面无 JS 错误，两个 API 通过网站同源代理连接')
            visit('home')
            expect(page.locator('#month-picker')).to_have_value('2026-10')
            page.screenshot(path=str(OUT/'home-desktop.png'),full_page=True)
            page.set_viewport_size({'width':393,'height':852})
            page.screenshot(path=str(OUT/'home-mobile.png'),full_page=True)
            visit('entry')
            page.locator('[data-action="entry-type"][data-value="收入"]').click()
            expect(page.locator('[data-action="entry-category"][data-value="工资"]')).to_be_visible()
            expect(page.locator('[data-action="entry-category"][data-value="餐饮"]')).to_have_count(0)
            page.locator('[data-action="parse-entry"]').click()
            page.locator('#parse-text').fill('2026年10月3日银行卡收到税后工资22500元')
            page.locator('[data-action="parse-confirm"]').click()
            expect(page.locator('#entry-amount')).to_have_value('22500.00',timeout=180000)
            expect(page.locator('[data-action="entry-category"][data-value="工资"]')).to_have_class('selected')
            page.screenshot(path=str(OUT/'salary-parsed.png'),full_page=True)
            checks.append('真实微调模型将自然语言工资解析为收入草稿，分类与金额正确')
            tag='自动验收-'+uuid.uuid4().hex
            page.locator('#entry-amount').fill('0.01')
            page.locator('#entry-description').fill(tag)
            with page.expect_response(lambda r:r.url.endswith('/api/book/bills') and r.request.method=='POST') as saved:
                page.locator('#save-entry').click()
            response=saved.value
            assert response.status==201
            created_id=response.json()['id']
            actual=page.request.get(FRONT+'/api/book/bills?limit=500').json()
            assert any(r['id']==created_id and r['amount']==0.01 and r['category']=='工资' for r in actual)
            page.request.delete(FRONT+f'/api/book/bills/{created_id}')
            created_id=None
            checks.append('页面保存草稿、账本读取与删除回滚成功')
            missing=page.request.post(FRONT+'/api/book/bills/parse',data={'text':'今天买了午餐，帮我记一下','reference_date':'2026-10-03'},timeout=180000).json()
            assert missing.get('needs_clarification') is True,missing
            results['missing_amount']=missing
            transfer=page.request.post(FRONT+'/api/book/bills/parse',data={'text':'今天从自己的银行卡转到自己的支付宝500元','reference_date':'2026-10-03'},timeout=180000).json()
            assert transfer.get('needs_clarification') is True,transfer
            checks.append('缺少金额和内部转账不会自动变成收支账单')
            visit('assistant')
            page.locator('#chat-input').fill('请解释这组2026年9月的合成统计：收入22500.00元，支出15800.00元，结余6700.00元。只解释这一组，不引用其他区间。')
            with page.expect_response(lambda r:r.url.endswith('/api/book/chat'),timeout=180000) as chat:
                page.locator('button[data-action="send"]').click()
            reply=chat.value.json()['reply']
            assert '6700' in reply.replace(',',''),reply
            results['bookkeeper_reply']=reply
            page.screenshot(path=str(OUT/'assistant.png'),full_page=True)
            trader=page.request.post(FRONT+'/api/trader/chat',data={'messages':[{'role':'user','content':'这些是2025年12月5日的历史组合数据。可以把它说成今天的实时行情吗？请简短回答。'}],'max_new_tokens':300,'temperature':0},timeout=180000)
            assert trader.status==200
            results['trader_reply']=trader.json()['reply']
            checks.append('记账与投研助手均得到真实微调模型响应')
            visit('settings')
            page.locator('[data-action="health"]').click()
            expect(page.locator('#service-status')).to_contain_text('新版微调模型已加载',timeout=10000)
            page.screenshot(path=str(OUT/'settings.png'),full_page=True)
            assert page.request.get(FRONT+'/api/book/health').json()['bill_count']==health['bill_count']
            assert not errors,errors
        finally:
            if created_id:
                page.request.delete(FRONT+f'/api/book/bills/{created_id}')
            browser.close()
    report={'checks':checks,'results':results,'javascript_errors':errors}
    (OUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
