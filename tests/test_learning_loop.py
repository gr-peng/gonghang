"""Consent, minimization, human review and actual corpus export boundaries."""
import json
import sys
from pathlib import Path

import pytest
from test_bank_import import workspace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from build_finance_corpus import reviewed_feedback, banking_rows, profile_rows


def assistant_draft(client, monkeypatch):
    import finance_workspace
    monkeypatch.setattr(finance_workspace,'api_chat',lambda *a,**k: '{"intent":"transfer","recipient":"小林","product_id":null,"amount":"86.75"}')
    response=client.post('/finance/assistant',json={'messages':[{'role':'user','content':'我的账号和姓名是敏感信息；请给小林转86.75元'}]})
    assert response.status_code==200
    return response.json()


def correction(client, result):
    return client.post('/finance/operations/review',json={'kind':'transfer','recipient_id':'acct-alice',
        'amount':'186.75','request_id':'corrected-once','feedback_ref':result['feedback_ref']})


def test_default_off_and_consent_are_enforced_without_affecting_bank_authority(workspace, monkeypatch):
    client,module=workspace
    assert client.get('/finance/improvement').json()['enabled'] is False
    result=assistant_draft(client,monkeypatch)
    assert result['feedback_ref'] is None
    assert correction(client,result).status_code==200
    assert client.get('/finance/improvement').json()['samples']==[]
    assert module.finance_router.workspace().bank.snapshot()['execution_count']==0


def test_only_structured_correction_is_retained_and_reviewed_export_is_real(workspace, monkeypatch):
    client,module=workspace
    assert client.post('/finance/improvement/consent',json={'enabled':True}).status_code==200
    result=assistant_draft(client,monkeypatch)
    assert result['feedback_ref']
    response=correction(client,result)
    assert response.status_code==200
    service=module.finance_router.workspace()
    database=service.store._db.execute('PRAGMA database_list').fetchone()[2]
    snapshot=client.get('/finance/improvement').json()
    assert len(snapshot['samples'])==1
    sample=snapshot['samples'][0]
    assert sample['status']=='candidate'
    assert sample['before']['amount_band']=='micro' and sample['after']['amount_band']=='small'
    encoded=json.dumps(sample,ensure_ascii=False)
    assert all(value not in encoded for value in ('86.75','186.75','小林','acct-alice','账号和姓名','13800000001','csrf','challenge'))
    assert reviewed_feedback(database)==[]  # merely submitting a correction does not train
    reviewed=client.post(f"/finance/improvement/{sample['id']}/review",json={'approve':True})
    assert reviewed.status_code==200
    rows=reviewed_feedback(database)
    assert len(rows)==1 and rows[0]['source']=='reviewed_structured_replay_not_original_chat'
    assert json.loads(rows[0]['messages'][-1]['content'])['amount']=='350.00'  # synthetic amount, not exact original
    assert service.bank.snapshot()['execution_count']==0
    assert client.delete(f"/finance/improvement/{sample['id']}").status_code==200
    assert reviewed_feedback(database)==[]


def test_optout_deletes_untrained_feedback_and_forged_provenance_is_ignored(workspace, monkeypatch):
    client,module=workspace
    client.post('/finance/improvement/consent',json={'enabled':True})
    result=assistant_draft(client,monkeypatch)
    correction(client,result)
    assert client.post('/finance/improvement/consent',json={'enabled':False}).json()['samples']==[]
    forged=client.post('/finance/operations/review',json={'kind':'transfer','recipient_id':'acct-alice','amount':'1',
        'request_id':'forged-feedback','feedback_ref':'invented-feedback-ref'})
    assert forged.status_code==200
    assert client.get('/finance/improvement').json()['samples']==[]
    assert client.post('/finance/improvement/consent',json={'enabled':True,'approved':True}).status_code==422


def test_template_holdouts_and_multi_action_clarification_do_not_overlap():
    sets=[]
    for split in ('train','validation','test'):
        rows=banking_rows(split)
        sets.append({json.dumps(r['messages'][:-1],sort_keys=True,ensure_ascii=False) for r in rows})
        multi=[r for r in rows if 'unsupported-' in r['group']]
        assert multi and all(json.loads(r['messages'][-1]['content'])['intent']=='unsupported' for r in multi)
    assert not sets[0]&sets[1] and not sets[0]&sets[2] and not sets[1]&sets[2]


def test_plan_model_receives_only_aggregates_and_cannot_invent_facts_or_authority(workspace,monkeypatch):
    client,module=workspace
    client.post('/finance/profile',json={'stage':'student','goal_name':'账号 123456 敏感目标','goal_amount':'900','goal_months':2})
    import finance_workspace
    from finance_schema import plan_target
    calls=[]
    def model(messages,**kwargs):
        calls.append(messages)
        if len(calls)==1:return '{"intent":"plan","recipient":null,"product_id":null,"amount":null}'
        facts=json.loads(messages[-1]['content'])
        assert '123456' not in messages[-1]['content'] and 'goal_name' not in facts
        assert 'account' not in facts and 'csrf' not in facts
        return json.dumps(plan_target(facts))
    monkeypatch.setattr(finance_workspace,'api_chat',model)
    response=client.post('/finance/assistant',json={'messages':[{'role':'user','content':'看看我的个人计划'}]}).json()
    assert response['explanation_status']=='model_grounded' and response['plan_next']=='import'
    assert '先补齐三个月' in response['reply']
    assert module.finance_router.workspace().bank.snapshot()['execution_count']==0
    calls.clear()
    def forged(messages,**kwargs):
        calls.append(messages)
        return ('{"intent":"plan"}' if len(calls)==1 else
                '{"focus":"longterm","reason_ids":["longterm"],"next_step":"compare","confirmed":true,"investable_minor":99999999}')
    monkeypatch.setattr(finance_workspace,'api_chat',forged)
    blocked=client.post('/finance/assistant',json={'messages':[{'role':'user','content':'看看计划'}]}).json()
    assert blocked['explanation_status']=='verified_fallback' and blocked['plan_next']=='import'
    assert '99999999' not in blocked['reply'] and '0.00 元' in blocked['reply']


def test_profile_holdouts_are_independent_aggregate_scenarios():
    from finance_schema import validate_plan_selection
    groups=[];inputs=[]
    for split in ('train','validation','test'):
        rows=profile_rows(split)
        assert rows and all(validate_plan_selection(json.loads(r['messages'][-1]['content']),json.loads(r['messages'][-2]['content'])) for r in rows)
        groups.append({r['group'] for r in rows});inputs.append({r['messages'][-2]['content'] for r in rows})
    for a,b in ((0,1),(0,2),(1,2)):
        assert not groups[a]&groups[b] and not inputs[a]&inputs[b]


def test_registered_phone_is_resolved_without_exposing_unregistered_phone(workspace,monkeypatch):
    client,_=workspace
    import finance_workspace
    def model(messages,**kwargs):
        assert '小林' in messages[-1]['content']
        assert '13800000001' not in messages[-1]['content'] and '13999999999' not in messages[-1]['content']
        return '{"intent":"transfer","recipient":"小林","product_id":null,"amount":"20.00"}'
    monkeypatch.setattr(finance_workspace,'api_chat',model)
    output=client.post('/finance/assistant',json={'messages':[{'role':'user','content':'给13800000001转20元。我的手机号是13999999999'}]}).json()
    assert output['draft']['recipient_id']=='acct-alice'


def test_foreign_currency_and_bare_followup_never_become_unconfirmed_cny(workspace,monkeypatch):
    client,module=workspace
    import finance_workspace
    monkeypatch.setattr(finance_workspace,'api_chat',lambda *a,**k:'{"intent":"transfer","recipient":"小林","product_id":null,"amount":"80.00"}')
    foreign=[{'role':'user','content':'给小林转80美元'}]
    for messages in [foreign,foreign+[{'role':'assistant','content':'请确认人民币金额'},{'role':'user','content':'80'}]]:
        result=client.post('/finance/assistant',json={'messages':messages}).json()
        assert result['draft'] is None and result['planner_status']=='needs_clarification'
    confirmed=client.post('/finance/assistant',json={'messages':foreign+[{'role':'user','content':'转人民币80元给小林'}]}).json()
    assert confirmed['draft']['amount']=='80.00'
    assert module.finance_router.workspace().bank.snapshot()['execution_count']==0


def test_explicit_short_horizon_overrides_high_questionnaire_total(workspace):
    client,_=workspace
    data={'risk_answers':[0,2,2,2],'stage':'student','goal_name':'学费','goal_amount':'30000','goal_months':3}
    result=client.post('/finance/profile',json=data).json()
    assert result['risk_level']==1 and result['products'][1]['eligible'] is False
    assert any(r['id']=='stage' for r in result['reasons'])
    assert result['goal_feasible'] is False


def test_unusual_amount_is_a_review_candidate_not_a_fraud_verdict(workspace):
    client,_=workspace
    for index,amount in enumerate([35,36,40,38,37,900]):
        client.post('/bills',json={'event_date':f'2026-09-{index+1:02d}','category':'餐饮','type':'expense',
            'amount':amount,'description':'午餐'})
    before=client.get('/health').json()['bill_count']
    candidates=client.get('/finance/overview').json()['bill_checks']
    assert len(candidates)==1 and candidates[0]['amount_minor']==90000
    assert '核对' in candidates[0]['reason'] and '欺诈' not in candidates[0]['reason']
    assert client.get('/health').json()['bill_count']==before
