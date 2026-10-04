"""Offline model release registry. No UI/LLM can promote or roll back a model.

prepare validates hashes and all saved generations; promote changes local .env
and registry only. Restart the owned launcher separately, then verify its health.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

if __package__:
    from .model_validation import recheck,release_gate
else:
    from model_validation import recheck,release_gate

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/'.runtime/model-releases'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        os.chmod(handle.name,0o600);handle.write(text);temp=handle.name
    os.replace(temp,path)


def validate_artifact(artifact):
    artifact=artifact.resolve();adapter=artifact/'adapter'
    manifest=json.loads((artifact/'manifest.json').read_text())
    metadata=json.loads((adapter/'training-metadata.json').read_text())
    if sha(artifact/'manifest.json')!=metadata['dataset_manifest_sha256']:
        raise ValueError('Dataset manifest has changed since training')
    for split in ('train','validation','test'):
        path=artifact/f'{split}.jsonl'
        if sha(path)!=manifest['splits'][split]['sha256']:
            raise ValueError(f'{split} dataset has changed')
    rows=[json.loads(line) for line in (artifact/'test.jsonl').read_text().splitlines() if line.strip()]
    report_file=adapter/'full-test-evaluation.json'
    if not report_file.exists():report_file=adapter/'evaluation.json'
    report=json.loads(report_file.read_text());tasks=recheck(rows,report)
    if tasks!=metadata['full_test_evaluation']:
        raise ValueError('Saved evaluation does not pass current strict graders')
    baseline=json.loads((adapter/'baseline.json').read_text())['tasks']
    if not release_gate(metadata,tasks,baseline):raise ValueError('Model failed release gate')
    hashes={str(path.relative_to(artifact)):sha(path) for path in (
        artifact/'manifest.json',artifact/'train.jsonl',artifact/'validation.jsonl',artifact/'test.jsonl',
        adapter/'adapter_config.json',adapter/'adapter_model.safetensors',adapter/'training-metadata.json',report_file)}
    return manifest,metadata,tasks,hashes


def prepare(artifact,version):
    if not re.fullmatch(r'qingcai-qwen3-4b-[a-z0-9-]+',version):raise ValueError('Invalid model version')
    artifact=artifact.resolve();manifest,metadata,tasks,hashes=validate_artifact(artifact)
    record={'version':version,'artifact':str(artifact),'adapter':str(artifact/'adapter'),
            'base':metadata['base_model'],'prepared_at':datetime.now(timezone.utc).isoformat(),
            'evaluation':tasks,'training_source':manifest.get('training_source','authored_synthetic_accounting'),
            'feedback_rows':manifest.get('feedback_rows',0),'feedback_ids':manifest.get('feedback_ids',[]),
            'feedback_provenance':manifest.get('feedback_provenance','none'),'hashes':hashes,
            'passed_release_gate':True}
    path=REGISTRY/f'{version}.json'
    if path.exists():
        old=json.loads(path.read_text())
        if old['hashes']!=hashes:raise ValueError('Cannot overwrite a published version with different weights')
        return path
    atomic(path,json.dumps(record,ensure_ascii=False,indent=2)+'\n');return path


def verify_record(record):
    if record.get('passed_release_gate') is not True:raise ValueError('Unapproved release')
    artifact=Path(record['artifact'])
    for relative,digest in record['hashes'].items():
        path=artifact/relative
        if path.resolve().parent!=artifact.resolve() and artifact.resolve() not in path.resolve().parents:
            raise ValueError('Release artifact path escapes its directory')
        if sha(path)!=digest:raise ValueError(f'Release artifact has changed: {relative}')
    _,_,tasks,hashes=validate_artifact(artifact)
    if tasks!=record['evaluation'] or hashes!=record['hashes']:raise ValueError('Release record mismatch')
    return record


def activate(version,rollback=False,feedback_database=None):
    path=REGISTRY/f'{version}.json';record=verify_record(json.loads(path.read_text()))
    if record['feedback_rows'] and record['feedback_provenance']=='owner_reviewed_minimized' and not rollback:
        if feedback_database is None:raise ValueError('Reviewed feedback database is required before release')
        consume_feedback(feedback_database,version)
    current=REGISTRY/'current.json';previous=REGISTRY/'previous.json'
    env_path=ROOT/'.env';env_text=env_path.read_text() if env_path.exists() else ''
    if current.exists():atomic(previous,current.read_text())
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    atomic(REGISTRY/f'env-{stamp}.backup',env_text)
    replacements={'ACCOUNTING_ADAPTER':record['adapter'],'ACCOUNTING_MODEL_NAME':version,'LLM_MODEL':version,
                  'ACCOUNTING_RELEASE_RECORD':str(path)}
    lines=env_text.splitlines();found=set();updated=[]
    for line in lines:
        key=line.split('=',1)[0].strip()
        if not line.lstrip().startswith('#') and key in replacements:
            if key not in found:updated.append(f'{key}={replacements[key]}');found.add(key)
        else:updated.append(line)
    updated.extend(f'{key}={value}' for key,value in replacements.items() if key not in found)
    atomic(env_path,'\n'.join(updated)+'\n')
    record={**record,'released_at':datetime.now(timezone.utc).isoformat(),'activation':'rollback' if rollback else 'release'}
    atomic(current,json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    return {'version':version,'evaluation':record['evaluation'],'restart_required':True}


def consume_feedback(database,version):
    """Only matching still-consented reviewed samples used in this release."""
    record=verify_record(json.loads((REGISTRY/f'{version}.json').read_text()))
    if record['feedback_provenance']!='owner_reviewed_minimized':
        raise ValueError('Developer fixture cannot consume real user feedback')
    db=sqlite3.connect(database)
    try:
        with db:
            if not db.execute('SELECT enabled FROM app_learning WHERE id=1').fetchone()[0]:raise ValueError('Consent withdrawn')
            db.execute('CREATE TABLE IF NOT EXISTS app_feedback_training (feedback_id TEXT PRIMARY KEY, model_version TEXT NOT NULL, consumed_at TEXT NOT NULL)')
            for fid in record['feedback_ids']:
                row=db.execute('SELECT status,reviewed FROM app_feedback WHERE id=?',(fid,)).fetchone()
                if not row or row[0]!='approved' or row[1] is None:raise ValueError('Feedback was removed or review revoked')
                db.execute('UPDATE app_feedback SET status=? WHERE id=?',('consumed',fid))
                db.execute('INSERT INTO app_feedback_training VALUES (?,?,?)',(fid,version,datetime.now(timezone.utc).isoformat()))
    finally:db.close()


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--artifact',type=Path,required=True);prep.add_argument('--version',required=True)
    promote=sub.add_parser('promote');promote.add_argument('--version',required=True)
    promote.add_argument('--feedback-database',type=Path)
    sub.add_parser('rollback')
    consume=sub.add_parser('consume-feedback');consume.add_argument('--database',type=Path,required=True);consume.add_argument('--version',required=True)
    args=parser.parse_args()
    if args.command=='prepare':print(prepare(args.artifact,args.version))
    elif args.command=='promote':print(json.dumps(activate(args.version,feedback_database=args.feedback_database),ensure_ascii=False))
    elif args.command=='rollback':
        prior=json.loads((REGISTRY/'previous.json').read_text());print(json.dumps(activate(prior['version'],rollback=True),ensure_ascii=False))
    else:consume_feedback(args.database,args.version);print('Reviewed feedback linked to release')


if __name__=='__main__':main()
