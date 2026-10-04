import assert from 'node:assert/strict';
import {savingsProgress, maximumDrawdown, normalizeSeries} from '../AI_accounting_agent/frontend/liquid-glass/visuals.js';
import {quickTemplates} from '../AI_accounting_agent/frontend/liquid-glass/templates.js';
import {readFileSync} from 'node:fs';

assert.deepEqual(savingsProgress({target:1000,percent:20,manual:100},-300), {projected:0,manual:100,total:100,remaining:900,percent:10});
assert.deepEqual(savingsProgress({target:1000,percent:20,manual:100},7000), {projected:1400,manual:100,total:1500,remaining:0,percent:100});
assert.equal(savingsProgress({target:1000,percent:20,manual:0.01},1234.56).total,246.92);
assert.equal(maximumDrawdown([{date:'2025-01-03',nav:90},{date:'2025-01-01',nav:100},{date:'2025-01-02',nav:120}]),25);
assert.equal(maximumDrawdown([{date:'2025-01-01',nav:100}]),0);
const result=normalizeSeries([
  {name:'a',rows:[{date:'2025-01-03',close:12},{date:'2025-01-01',close:10},{date:'2025-01-02',close:11}]},
  {name:'b',rows:[{date:'2025-01-02',close:20},{date:'2025-01-03',close:22},{date:'2025-01-04',close:24}]}
]);
assert.deepEqual(result.labels,['2025-01-02','2025-01-03']);
assert.deepEqual(result.series.map(s=>s.values[0]),[100,100]);
assert.ok(Math.abs(result.series[1].values[1]-110)<1e-9);
assert.deepEqual(normalizeSeries([]),{labels:[],series:[]});
assert.deepEqual(normalizeSeries([{name:'a',rows:[{date:'2025-01-01',close:0}]},{name:'b',rows:[]}]).labels,[]);
const upstreamBook = readFileSync(new URL('../AI_accounting_agent/frontend/accounting/financial_advice/code.html',import.meta.url),'utf8');
for (const match of upstreamBook.matchAll(/data-template="([^"]+)"/g)) assert.ok(quickTemplates.book.some(t=>t.text===match[1]));
const upstreamTrader=JSON.parse(readFileSync(new URL('../AI_accounting_agent/frontend/investment/trader_chat/qa.json',import.meta.url),'utf8'));
for(const item of upstreamTrader)assert.ok(quickTemplates.trader.some(t=>t.text===item.question));
assert.equal(new Set([...quickTemplates.book,...quickTemplates.trader].map(t=>t.id)).size,40);
console.log('PASS: savings rounding/negative/over-target, drawdown, shared-date indexing, all upstream quick questions.');
const {createFeatures}=await import('../AI_accounting_agent/frontend/liquid-glass/features.js');
const escape=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const feature=createFeatures({state:{},prefs:{},esc:escape});
const reply=feature.replyHTML('{"observation":"<img src=x onerror=alert(1)>","reason":"one","suggestion":["a","b"]}');
assert.ok(reply.includes('观察')&&reply.includes('原因')&&reply.includes('建议'));
assert.ok(!reply.includes('<img')&&reply.includes('&lt;img'));
assert.ok(!feature.replyHTML('**说明**\n<script>x</script>').includes('<script>'));
console.log('PASS: structured reply formatting preserves HTML escaping.');
const {adviceMatchesContext}=await import('../AI_accounting_agent/frontend/liquid-glass/advice-check.js');
const context={income_total:20000,expense_total:12000,savings_rate:0.4,category_share:[{category:'住房',amount:6000,share:0.5}]};
assert.ok(adviceMatchesContext('收入 20,000 元，支出 12,000 元，储蓄率 40%，住房占支出 50%。',context));
assert.ok(!adviceMatchesContext('当前预算执行率 100%。',context));
assert.ok(!adviceMatchesContext('餐饮占比 22.19%。',context));
assert.ok(!adviceMatchesContext('建议每月固定节省 500 元。',context));
assert.ok(adviceMatchesContext('1. 先核对住房支出。\n2. 对照近期分类变化调整计划。',context));
console.log('PASS: grounded advice rejects unsupported numbers and fabricated budget metrics.');
