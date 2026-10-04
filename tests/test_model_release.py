"""Release gates reject failed runs, incomplete evaluation and changed artifacts."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import model_release as releases
from model_validation import recheck


def artifact(tmp_path,name):
    folder=tmp_path/name;adapter=folder/'adapter';adapter.mkdir(parents=True)
    labels=[('extract',{'event_date':'2026-09-01','category':'餐饮','type':'expense','amount':35.5,'currency':'CNY','payment_method':'现金'}),
            ('summary','收入 100.00 元，支出 35.50 元，结余 64.50 元'),('clarify',{'needs_clarification':True})]
    rows=[];outputs=[]
    for task,label in labels:
        text=label if isinstance(label,str) else json.dumps(label,ensure_ascii=False)
        row={'task':task,'messages':[{'role':'user','content':task},{'role':'assistant','content':text}]}
        rows.append(row);outputs.append({'task':task,'input':task,'expected':text,'actual':text})
    manifest={'version':name,'splits':{}}
    for split in ['train','validation','test']:
        path=folder/f'{split}.jsonl';path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        manifest['splits'][split]={'sha256':releases.sha(path)}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    tasks=recheck(rows,{'outputs':outputs})
    meta={'base_model':str(tmp_path/'base'),'dataset_manifest_sha256':releases.sha(folder/'manifest.json'),
          'passed_release_gate':True,'full_test_passed':True,'best_validation_loss':.1,'baseline_validation_loss':.2,
          'full_test_evaluation':tasks}
    for filename,value in [('training-metadata.json',meta),('evaluation.json',{'outputs':outputs}),('baseline.json',{'tasks':tasks}),('adapter_config.json',{})]:
        (adapter/filename).write_text(json.dumps(value))
    (adapter/'adapter_model.safetensors').write_bytes(b'test-only-placeholder-not-a-real-adapter')
    return folder


@pytest.fixture
def registry(tmp_path,monkeypatch):
    monkeypatch.setattr(releases,'ROOT',tmp_path);monkeypatch.setattr(releases,'REGISTRY',tmp_path/'registry')
    return tmp_path


def test_saved_pass_flags_cannot_replace_full_generations(registry):
    folder=artifact(registry,'valid');report=folder/'adapter/evaluation.json'
    data=json.loads(report.read_text());data['outputs'].pop();report.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='complete frozen'):releases.prepare(folder,'qingcai-qwen3-4b-incomplete')


def test_failed_run_and_post_validation_weight_change_are_rejected(registry):
    folder=artifact(registry,'valid');meta=folder/'adapter/training-metadata.json'
    data=json.loads(meta.read_text());data['passed_release_gate']=False;meta.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='failed release gate'):releases.prepare(folder,'qingcai-qwen3-4b-failed')
    data['passed_release_gate']=True;meta.write_text(json.dumps(data))
    path=releases.prepare(folder,'qingcai-qwen3-4b-valid');record=json.loads(path.read_text())
    (folder/'adapter/adapter_model.safetensors').write_bytes(b'changed-after-evaluation')
    with pytest.raises(ValueError,match='artifact has changed'):releases.verify_record(record)


def test_local_promotion_and_rollback_preserve_other_configuration(registry):
    (registry/'.env').write_text('LLM_API_KEY=private-fixture\nFRONTEND_PORT=5500\n')
    for version in ['v1','v2']:
        folder=artifact(registry,version);releases.prepare(folder,'qingcai-qwen3-4b-'+version)
        releases.activate('qingcai-qwen3-4b-'+version)
    previous=json.loads((releases.REGISTRY/'previous.json').read_text())
    releases.activate(previous['version'],rollback=True)
    assert json.loads((releases.REGISTRY/'current.json').read_text())['version']=='qingcai-qwen3-4b-v1'
    text=(registry/'.env').read_text()
    assert 'LLM_API_KEY=private-fixture' in text and 'FRONTEND_PORT=5500' in text
    assert 'LLM_MODEL=qingcai-qwen3-4b-v1' in text


def test_developer_fixture_cannot_consume_real_owner_feedback(registry):
    folder=artifact(registry,'valid')
    manifest=json.loads((folder/'manifest.json').read_text());manifest.update(feedback_rows=1,feedback_provenance='isolated_browser_developer_fixture_not_real_user')
    (folder/'manifest.json').write_text(json.dumps(manifest));meta=folder/'adapter/training-metadata.json'
    data=json.loads(meta.read_text());data['dataset_manifest_sha256']=releases.sha(folder/'manifest.json');meta.write_text(json.dumps(data))
    releases.prepare(folder,'qingcai-qwen3-4b-fixture')
    with pytest.raises(ValueError,match='cannot consume real'):releases.consume_feedback(registry/'absent.sqlite3','qingcai-qwen3-4b-fixture')


def test_strict_bank_grader_rejects_float_amount_and_forged_confirmation():
    expected='{"intent":"transfer","recipient":"小林","product_id":null,"amount":"80.00"}'
    row={'task':'bank_intent','messages':[{'role':'user','content':'转80元'},{'role':'assistant','content':expected}]}
    from model_validation import grade
    assert grade(row,expected)
    assert not grade(row,expected.replace('"80.00"','80.0'))
    assert not grade(row,expected[:-1]+',"confirmed":true}')
