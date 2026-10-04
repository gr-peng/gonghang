import assert from 'node:assert/strict';
import {createVisuals} from '../AI_accounting_agent/frontend/liquid-glass/visuals.js';

const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const number = (value, digits = 2) => Number(value).toLocaleString('zh-CN', {maximumFractionDigits: digits});
const money = value => `¥${Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const prefs = {mask: false};
const visuals = createVisuals({esc, money, number, prefs, empty: text => `<p class="empty">${esc(text)}</p>`});
const attrs = markup => Object.fromEntries([...markup.matchAll(/([\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
const elements = (html, tag, className) => [...html.matchAll(new RegExp(`<${tag}\\b[^>]*class="[^"]*\\b${className}\\b[^"]*"[^>]*>`, 'g'))].map(match => attrs(match[0]));
const source = length => ({bars: {labels: Array.from({length}, (_, i) => `2026-09-${String(i + 1).padStart(2, '0')}`), income: Array.from({length}, (_, i) => i === 0 ? 100 : 0), expense: Array.from({length}, (_, i) => i + 1)}});

// All 31 days (and all 12 months) fit in the same SVG, with every value operable.
for (const length of [1, 12, 31]) {
  const report = source(length);
  if (length === 12) report.bars.labels = report.bars.labels.map((_, i) => `2026-${String(i + 1).padStart(2, '0')}`);
  for (const type of ['柱状', '折线']) {
    const html = visuals.cashflow(report, {type});
    assert.match(html, /viewBox="0 0 360 220"/);
    assert.doesNotMatch(html, /min-width|chart-scroll|NaN|Infinity/);
    assert.equal(elements(html, 'g', 'chart-point').length, length);
    assert.equal(elements(html, 'text', 'chart-date').length, Math.min(5, length));
    for (const point of elements(html, 'g', 'chart-point')) {
      assert.equal(point.tabindex, '0'); assert.equal(point.role, 'button');
      assert.equal(point['data-action'], 'chart-point'); assert.ok(point['data-value'].includes('收入'));
    }
    for (const hit of elements(html, 'rect', 'chart-hit')) {
      assert.ok(Number(hit.x) >= 48); assert.ok(Number(hit.x) + Number(hit.width) <= 350.002);
    }
    if (type === '柱状') {
      assert.equal(elements(html, 'rect', 'cashflow-bar').length, length * 2);
      for (const bar of elements(html, 'rect', 'cashflow-bar')) {
        assert.ok(Number(bar.x) >= 48); assert.ok(Number(bar.x) + Number(bar.width) <= 350);
        assert.ok(Number(bar.y) >= 18); assert.ok(Number(bar.y) + Number(bar.height) <= 178.002);
      }
    } else {
      assert.equal(elements(html, 'polyline', 'cashflow-line').length, 2);
      assert.ok(elements(html, 'circle', 'chart-hover-dot').every(dot => dot.opacity === (length === 1 ? '1' : '0')));
    }
  }
}
const known = visuals.cashflow({bars: {labels: ['A', 'B'], income: [100, 25], expense: [0, 50]}}, {type: '折线'});
const knownIncome = elements(known, 'polyline', 'cashflow-line').find(line => line['data-series'] === 'income');
assert.equal(knownIncome.points, '123.5,18 274.5,138'); // quarter value is precisely a quarter of the vertical scale.
// Desktop width expands the data domain itself, preserving unscaled axis text and 220-unit height.
const wide = visuals.cashflow(source(31), {type: '折线', width: 640});
assert.match(wide, /viewBox="0 0 640 220"/);
assert.doesNotMatch(wide, /preserveAspectRatio="none"/);
const wideHits = elements(wide, 'rect', 'chart-hit');
assert.equal(wideHits.length, 31);
assert.equal(Number(wideHits[0].x), 48);
assert.ok(Math.abs(Number(wideHits.at(-1).x) + Number(wideHits.at(-1).width) - 630) < .002);
assert.match(wide, /x1="48" x2="630"/);
assert.equal(elements(wide, 'text', 'chart-date').length, 5);
const wideResearch = visuals.lines({labels: ['A', 'B'], series: [{name: '样本', values: [100, 120]}], width: 640});
assert.match(wideResearch, /viewBox="0 0 640 220"/);
assert.match(wideResearch, /points="48,178 630,18"/);
assert.match(visuals.cashflow(source(1), {width: 100}), /viewBox="0 0 360 220"/);
const thousands = visuals.cashflow({bars: {labels: ['A'], income: [20000], expense: [0]}});
assert.match(thousands, />0<\/text>/);
assert.doesNotMatch(thousands, />0万<\/text>/);
assert.match(visuals.bars({bars: {labels: ['A'], income: [.01], expense: [.02]}}), />0\.005<|>0\.01</);
assert.match(visuals.cashflow({bars: {labels: ['A'], income: [0], expense: [0]}}), /暂无收支数据/);
assert.doesNotMatch(visuals.cashflow({bars: {labels: ['A', 'B'], income: [10, -10], expense: [0, 5]}}), /NaN|height="-/);

// Donut ordering, exact percentages and totals, stable category colors, and all drilldowns survive folding.
const categories = ['餐饮', '出行', '购物', '娱乐', '住房', '旅行', '保险'].map((name, i) => ({name, value: (i + 1) * 10}));
const donut = visuals.donut(categories, {action: 'visual-category', limit: 5});
assert.equal(elements(donut, 'circle', 'donut-arc').length, 7);
assert.equal(elements(donut, 'button', 'donut-legend-row').length, 7);
assert.equal(elements(donut.split('<details')[0], 'button', 'donut-legend-row').length, 5);
assert.match(donut, /<summary>全部分类（7）<\/summary>/);
assert.match(donut, /¥280\.00/);
assert.ok(Math.abs(elements(donut, 'circle', 'donut-arc').reduce((sum, arc) => sum + Number(arc['data-share']), 0) - 100) < 1e-9);
for (const arc of elements(donut, 'circle', 'donut-arc')) {
  const [length, rest] = arc['stroke-dasharray'].split(' ').map(Number);
  assert.ok(length > 0 && length < Number(arc['data-share']));
  assert.ok(Math.abs(length + rest - 100) < 1e-9);
}
const colors = html => Object.fromEntries(elements(html, 'circle', 'donut-arc').map(arc => [arc['data-value'], arc.stroke]));
assert.deepEqual(colors(donut), colors(visuals.donut(categories.map(item => ({...item, value: 100 - item.value})), {action: 'visual-category'})));
const only = elements(visuals.donut([{name: '餐饮', value: .01}]), 'circle', 'donut-arc')[0];
assert.equal(only['stroke-dasharray'], '100 0');
assert.match(visuals.donut([{name: '巨额', value: 9e18}]), /textLength="126"/);

// Comparison bars share one numeric denominator and never truncate the 100% bar.
const comparison = visuals.comparison({categories: [{name: '住房', current: 100, previous: 10}, {name: '餐饮', current: 50, previous: 100}]});
assert.deepEqual([...comparison.matchAll(/style="width:([\d.]+)%;max-width:none;min-width:0"/g)].map(match => Number(match[1])), [100, 10, 50, 100]);
const cents = visuals.comparison({categories: [{name: 'A', current: .5, previous: .1}]});
assert.deepEqual([...cents.matchAll(/style="width:([\d.]+)%;max-width:none;min-width:0"/g)].map(match => Number(match[1])), [100, 20]);
const many = visuals.comparison({categories: categories.map(item => ({name: item.name, current: item.value, previous: 0}))});
assert.equal(elements(many, 'button', 'comparison-row').length, 7);
assert.equal(elements(many.split('<details')[0], 'button', 'comparison-row').length, 5);

// Missing research samples break the line; a single legitimate point stays visible.
const sparse = visuals.lines({labels: ['A', 'B', 'C'], series: [{name: '样本', values: [100, null, 120]}]});
assert.doesNotMatch(sparse, /<polyline/);
assert.match(sparse, /暂无数据/);
assert.doesNotMatch(visuals.lines({labels: ['A'], series: [{name: '样本', values: [100]}]}), /class="empty"|NaN|Infinity/);
assert.match(visuals.lines({labels: [], series: []}), /无法比较/);

// Values remain escaped in text, attributes and labels; masking releases no amount or category data.
const hostile = '\"><img src=x onerror=alert(1)>';
for (const html of [
  visuals.cashflow({bars: {labels: [hostile], income: [123.45], expense: [0]}}),
  visuals.donut([{name: hostile, value: 123.45}], {action: hostile, title: hostile, center: hostile}),
  visuals.comparison({categories: [{name: hostile, current: 123.45, previous: 10}]}),
  visuals.lines({labels: [hostile], series: [{name: hostile, values: [123.45]}]}),
]) { assert.doesNotMatch(html, /<img|=""/); assert.match(html, /&lt;img/); }
prefs.mask = true;
for (const html of [visuals.bars(source(31)), visuals.cashflow(source(31), {type: '折线'}), visuals.donut(categories), visuals.comparison({categories: [{name: '私密分类', current: 123.45}]}), visuals.lines({labels: ['私密日期'], series: [{name: '私密证券', values: [123.45]}]})]) {
  assert.equal(html, '<div class="chart-hidden">金额已隐藏</div>');
}
console.log('PASS: responsive 1/12/31-point geometry, exact shared scales, donut proportions and stable colors, accessible folding, single/missing samples, escaping and masking.');
