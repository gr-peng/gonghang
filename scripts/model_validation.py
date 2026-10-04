"""Recheck saved full-test generations without trusting recorded pass flags."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'AI_accounting_agent/backend'))
from finance_schema import validate_plan_selection


def grade(row, text):
    task=row['task'];expected=row['messages'][-1]['content']
    if task=='summary':
        numbers=[Decimal(n) for n in re.findall(r'-?\d+\.\d{2}',expected)]
        actual={Decimal(n) for n in re.findall(r'-?\d+(?:\.\d+)?',text.replace(',',''))}
        return bool(numbers) and all(n in actual for n in numbers)
    try:
        target,actual=json.loads(expected),json.loads(text)
        if not isinstance(actual,dict):return False
        if task=='extract':
            return all(actual.get(k)==target[k] for k in ('event_date','category','type','amount','currency','payment_method'))
        if task=='clarify':return actual.get('needs_clarification') is True
        if task=='profile_reason':
            return bool(validate_plan_selection(actual,json.loads(row['messages'][-2]['content'])))
        if task=='bank_intent':
            fields=('intent','recipient','product_id','amount')
            if set(actual)-set(fields):return False
            aliases={'朋友':'小林','13800000001':'小林','房租':'房东','13800000002':'房东'}
            for key in fields:
                value,desired=actual.get(key),target.get(key)
                if key=='recipient':value,desired=aliases.get(value,value),aliases.get(desired,desired)
                if key=='amount' and desired is not None:
                    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9]\d{0,7})(\.\d{1,2})?',value):return False
                    if Decimal(value)!=Decimal(desired):return False
                elif value!=desired:return False
            return True
        return False
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):return False


def recheck(rows, report):
    outputs=report['outputs']
    if len(rows)!=len(outputs):raise ValueError('Evaluation is not the complete frozen test split')
    totals,correct=Counter(),Counter()
    for row,result in zip(rows,outputs):
        if (row['task']!=result['task'] or row['messages'][-2]['content']!=result['input']
                or row['messages'][-1]['content']!=result['expected']):
            raise ValueError('Evaluation rows do not match the frozen test split')
        totals[row['task']]+=1;correct[row['task']]+=bool(grade(row,result['actual']))
    return {k:{'count':n,'correct':correct[k],'accuracy':correct[k]/n} for k,n in totals.items()}


def release_gate(metadata,tasks,baseline_tasks):
    if not (metadata.get('passed_release_gate') is True and metadata.get('full_test_passed') is True
            and metadata['best_validation_loss']<metadata['baseline_validation_loss']):return False
    for task in ('extract','summary','clarify'):
        if task not in tasks or tasks[task]['accuracy']<(.90 if task=='extract' else .875):return False
        baseline=baseline_tasks.get(task,{})
        if baseline.get('count')==tasks[task]['count'] and tasks[task]['correct']<baseline['correct']:return False
    return all(tasks[task]['accuracy']>=.95 for task in ('bank_intent','profile_reason') if task in tasks)
