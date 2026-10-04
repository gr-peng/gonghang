"""Fresh natural-language smoke tests through the live API, without inserting bills."""
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
cases=json.loads((ROOT/'tests/model_challenges.json').read_text())
results=[]
for case in cases:
    req=Request('http://127.0.0.1:25500/api/book/bills/parse',method='POST',headers={'Content-Type':'application/json'},data=json.dumps({'text':case['text'],'reference_date':'2026-10-03'}).encode())
    try:
        with urlopen(req,timeout=180) as response: actual=json.load(response)
        passed=actual.get('needs_clarification') is True if case.get('clarify') else all(actual.get(k)==v for k,v in case['expected'].items())
    except Exception as exc:
        passed=False; actual={'error':str(exc)}
    results.append({**case,'actual':actual,'passed':passed})
    print(json.dumps(results[-1],ensure_ascii=False),flush=True)
out=ROOT/'.runtime/accounting-v2-validation'
out.mkdir(exist_ok=True)
(out/'challenges.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
if not all(row['passed'] for row in results):raise SystemExit('Some natural-language challenges failed')
