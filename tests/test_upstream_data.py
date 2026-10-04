"""Regression checks against the original repository's structured bill dataset."""
import importlib
import json
from pathlib import Path
import shutil
import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1] / 'AI_accounting_agent' / 'backend'
sys.path.insert(0, str(BACKEND))
DATASET = BACKEND / 'data' / 'synthetic_bank_bills.jsonl'


def original_rows():
    return [json.loads(message['content'])
            for line in DATASET.read_text(encoding='utf-8').splitlines() if line.strip()
            for message in json.loads(line)['messages'] if message['role'] == 'assistant']


@pytest.fixture()
def imported(tmp_path, monkeypatch):
    shutil.copyfile(DATASET, tmp_path / DATASET.name)
    monkeypatch.setenv('AI_BOOKKEEPER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('LLM_PROVIDER', 'disabled')
    monkeypatch.delenv('AI_BOOKKEEPER_JSONL_PATH', raising=False)
    monkeypatch.delenv('AI_BOOKKEEPER_IMPORT_JSONL', raising=False)
    sys.modules.pop('app', None)
    module = importlib.import_module('app')
    yield module
    module._db_conn.close()


def test_packaged_dataset_imports_before_health_with_original_fields(imported):
    source = original_rows()
    with TestClient(imported.app) as client:
        rows = []
        for offset in range(0, len(source), 500):
            rows.extend(client.get(f'/bills?limit=500&offset={offset}').json())
        assert len(rows) == len(source) == 1090
        for row, original in zip(sorted(rows, key=lambda r: r['id']), source):
            assert row['event_date'] == original['日期']
            assert row['category'] == original['类别']
            assert row['type'] == original['type']
            assert Decimal(str(row['amount'])) == Decimal(str(original['金额']))
            assert row['description'] == original['描述']
            assert row['metadata']['raw'] == original
            assert row['metadata']['payment_method'] == original['支付方式']
        assert sum(r['type'] == 'income' for r in rows) == 29
        health = client.get('/health').json()
        assert health['date_range'] == {'start': '2025-01-01', 'end': '2025-12-28'}
        assert health['sources']['jsonl'] == 1090
        assert {'工资', '奖金', '副业', '理财收益'} <= set(health['categories'])
        assert health['llm']['configured'] is False


def test_original_totals_latest_period_and_historical_advice(imported):
    source = original_rows()
    with TestClient(imported.app) as client:
        reports = client.get('/reports/aggregate').json()
        for period, selected in [('year', source), ('month', [r for r in source if r['日期'].startswith('2025-12')])]:
            for kind in ['income', 'expense']:
                expected = sum(Decimal(str(r['金额'])) for r in selected if r['type'] == kind)
                assert Decimal(str(reports[period]['summary'][kind + '_total'])) == expected
        december = client.get('/reports/aggregate?start_date=2025-12-01&end_date=2025-12-31').json()['custom']
        assert december['summary'] == reports['month']['summary']
        habit = client.get('/advice/context?reference_date=2025-06-30').json()['behavior']
        assert habit['period']['end'] == '2025-06-30'
        assert client.get('/bills?type=income&category=工资').json()


def test_restart_and_health_do_not_duplicate_or_restore_deleted_bills(imported):
    with TestClient(imported.app) as client:
        row = client.get('/bills?limit=1').json()[0]
        assert client.delete(f"/bills/{row['id']}").status_code == 200
    imported._db_conn.close()
    importlib.reload(imported)
    assert imported.health()['bill_count'] == 1089
    with imported._db_lock:
        imported._db_conn.execute('DELETE FROM bills')
        imported._db_conn.commit()
    assert imported.health()['bill_count'] == 0
    imported._db_conn.close()
    importlib.reload(imported)
    assert imported.health()['bill_count'] == 0


def test_existing_nonempty_ledger_is_preserved(imported):
    with imported._db_lock:
        imported._db_conn.execute('DELETE FROM bills WHERE id != 1')
        imported._db_conn.execute('DELETE FROM data_imports')
        imported._db_conn.commit()
    imported._db_conn.close()
    importlib.reload(imported)
    assert imported.health()['bill_count'] == 1
