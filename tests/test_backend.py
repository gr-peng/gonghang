import importlib
import json
from pathlib import Path
import sys
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'AI_accounting_agent' / 'backend'
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def bookkeeper(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_BOOKKEEPER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('LLM_PROVIDER', 'disabled')
    monkeypatch.delenv('AI_BOOKKEEPER_JSONL_PATH', raising=False)
    monkeypatch.delenv('QWEN_MODEL_DIR', raising=False)
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    with TestClient(module.app) as client:
        yield client
    module._db_conn.close()


@pytest.fixture()
def investment(monkeypatch):
    monkeypatch.setenv('AI_BOOKKEEPER_DATA_DIR', str(BACKEND / 'data'))
    monkeypatch.setenv('LLM_PROVIDER', 'disabled')
    monkeypatch.delenv('QWEN_MODEL_DIR', raising=False)
    sys.modules.pop('investment_app', None)
    module = importlib.import_module('investment_app')
    with TestClient(module.app) as client:
        yield client


def bill(**kwargs):
    return dict(event_date=date.today().isoformat(), category='餐饮', type='expense', amount=28.5, currency='CNY', description='回归测试午餐', **kwargs)


def test_health_and_empty_database_without_model(bookkeeper):
    health = bookkeeper.get('/health').json()
    assert health['status'] == 'ok'
    assert health['bill_count'] == 0
    assert health['llm']['configured'] is False
    assert bookkeeper.get('/bills').json() == []
    assert bookkeeper.get('/reports/aggregate').json()['month']['summary']['expense_total'] == 0


def test_create_changes_reports_and_delete_reverts(bookkeeper):
    result = bookkeeper.post('/bills', json=bill())
    assert result.status_code == 201
    row = result.json()
    assert bookkeeper.get('/bills/summary').json()['total_expense'] == 28.5
    assert bookkeeper.get('/reports/aggregate').json()['month']['summary']['expense_total'] == 28.5
    assert bookkeeper.get('/advice/context').json()['overview']['summary']['expense'] == 28.5
    assert bookkeeper.delete(f"/bills/{row['id']}").status_code == 200
    assert bookkeeper.get('/reports/aggregate').json()['month']['summary']['expense_total'] == 0
    assert bookkeeper.delete(f"/bills/{row['id']}").status_code == 404


def test_previous_period_comparison(bookkeeper):
    first = bill();first.update(event_date='2026-09-01', amount=20)
    prev = bill();prev.update(event_date='2026-08-31', amount=10)
    bookkeeper.post('/bills', json=first)
    bookkeeper.post('/bills', json=prev)
    report = bookkeeper.get('/reports/aggregate?start_date=2026-09-01&end_date=2026-09-01').json()['custom']
    assert report['net'] == {'current':-20,'previous':-10,'difference':-10}
    assert report['categories'][0]['previous'] == 10


def test_retry_is_idempotent_and_conflict_is_explicit(bookkeeper):
    data = bill(metadata={'client_request_id':'test-repeat'})
    first = bookkeeper.post('/bills', json=data)
    second = bookkeeper.post('/bills', json=data)
    assert first.json()['id'] == second.json()['id']
    assert len(bookkeeper.get('/bills').json()) == 1
    data['amount'] = 99
    assert bookkeeper.post('/bills', json=data).status_code == 409


@pytest.mark.parametrize('amount', [0,-1,0.001])
def test_invalid_amount_rejected(bookkeeper, amount):
    data=bill();data['amount']=amount
    assert bookkeeper.post('/bills', json=data).status_code == 422


def test_filters_pagination_and_income(bookkeeper):
    for idx in range(5):
        data=bill();data['description']=f'记录{idx}'
        if idx==4: data.update(type='income', category='其他', amount=12000)
        assert bookkeeper.post('/bills', json=data).status_code == 201
    assert len(bookkeeper.get('/bills?type=expense&category=餐饮&limit=2&offset=2').json()) == 2
    assert bookkeeper.get('/bills?type=income').json()[0]['amount'] == 12000
    assert bookkeeper.get('/bills?offset=-1').status_code == 422


def test_bad_dates_rejected(bookkeeper):
    assert bookkeeper.get('/reports/aggregate?start_date=2026-09-02&end_date=2026-09-01').status_code == 400
    assert bookkeeper.get('/reports/aggregate?start_date=2026-09-01').status_code == 400


def test_no_fake_ai_or_ocr_results(bookkeeper, investment):
    for client in (bookkeeper, investment):
        response=client.post('/chat', json={'messages':[{'role':'user','content':'你好'}]})
        assert response.status_code == 503
        assert 'detail' in response.json()
    response=bookkeeper.post('/bills/ocr', files={'file':('receipt.png',b'not an image','image/png')})
    assert response.status_code == 503
    assert bookkeeper.get('/bills').json() == []


def test_investment_snapshot_and_bom_dates(investment):
    p=investment.get('/trader/portfolio').json()
    assert p['summary']['total_balance'] == 1825000
    assert p['summary']['day_change_pct'] == .8
    assert p['summary']['last_update'].startswith('2025-12-05')
    watchlist=investment.get('/trader/watchlist').json()
    assert len(watchlist)==10
    for stock in watchlist:
        rows=investment.get(f"/trader/stock/{stock['code']}/kline?days=10").json()
        assert len(rows)==min(10, stock["quality"]["valid"])
        from research_quality import valid_quote
        assert all(valid_quote(r) for r in rows)
        assert all('date' in r and len(r['date'])==10 for r in rows)
    assert investment.get('/trader/stock/600036/news').json()['news']
    assert investment.get('/trader/stock/NVDA/kline').status_code == 404
    assert investment.post('/trader/stock/NVDA/daily_report').status_code == 404
    assert investment.get('/trader/stock/600036/kline?days=0').status_code == 422


def test_remote_transport_uses_actual_response(monkeypatch):
    import llm_runtime
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            data=json.dumps({'choices':[{'message':{'content':'集成测试响应'}}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('LLM_PROVIDER','api')
    monkeypatch.setenv('LLM_BASE_URL',f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('LLM_MODEL','transport-test')
    try:
        assert llm_runtime.api_chat([{'role':'user','content':'测试'}])=='集成测试响应'
        assert received[0][0]=='/v1/chat/completions'
        assert received[0][1]['model']=='transport-test'
    finally:server.shutdown();server.server_close();thread.join()
