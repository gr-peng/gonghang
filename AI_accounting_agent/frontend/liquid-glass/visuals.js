// Dependency-free charts; all values are supplied by the existing APIs.
export const palette = ['#4665b4', '#789889', '#b89874', '#8c83a9', '#82a2b1', '#b8868b', '#8d9c76', '#a2978c', '#6e879b', '#a28baf', '#80a5a0', '#bba186', '#8c95b7', '#9bab95', '#bb999d', '#808e9f', '#a3aaa1', '#a49bba'];

export function savingsProgress(goal, net) {
  const target = Number(goal.target);
  const projected = Math.round(Math.max(0, Number(net)) * Number(goal.percent)) / 100;
  const manual = Number(goal.manual) || 0;
  const total = Math.round((projected + manual) * 100) / 100;
  return {projected, manual, total, remaining: Math.max(0, Math.round((target - total) * 100) / 100), percent: target > 0 ? Math.min(100, total / target * 100) : 0};
}

export function maximumDrawdown(history) {
  let peak = 0, drawdown = 0;
  for (const point of [...history].sort((a, b) => a.date.localeCompare(b.date))) {
    const nav = Number(point.nav);
    if (!Number.isFinite(nav) || nav <= 0) continue;
    peak = Math.max(peak, nav);
    drawdown = Math.max(drawdown, (peak - nav) / peak);
  }
  return drawdown * 100;
}

export function normalizeSeries(stocks) {
  if (!stocks.length) return {labels: [], series: []};
  const maps = stocks.map(stock => new Map(stock.rows.filter(r => Number(r.close) > 0).map(r => [r.date, Number(r.close)])));
  const labels = [...maps[0].keys()].filter(date => maps.every(map => map.has(date))).sort();
  return {labels, series: stocks.map((stock, i) => ({name: stock.name, values: labels.map(date => maps[i].get(date) / maps[i].get(labels[0]) * 100)}))};
}

export function createVisuals({esc, money, number, prefs, empty}) {
  const hidden = () => '<div class="chart-hidden">金额已隐藏</div>';
  const numeric = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const ink = {income: '#789889', expense: '#4665b4'};
  const frame = {left: 48, right: 350, top: 18, bottom: 178};
  const categoryNames = ['餐饮', '出行', '购物', '生活缴费', '娱乐', '住房', '医疗健康', '教育学习', '保险', '人情往来', '旅行', '其他', '工资', '奖金', '副业', '理财收益', '退款', '其他收入'];
  const colorFor = name => {
    const known = categoryNames.indexOf(name);
    const hash = [...String(name)].reduce((sum, ch) => (sum * 31 + ch.codePointAt(0)) >>> 0, 0);
    return palette[(known < 0 ? hash : known) % palette.length];
  };
  const rounded = value => Number(value.toFixed(3));
  const dateLabel = label => {
    const date = String(label);
    if (/^\d{4}-\d{2}-\d{2}$/.test(date)) return `${Number(date.slice(5, 7))}/${Number(date.slice(8))}`;
    if (/^\d{4}-\d{2}$/.test(date)) return `${Number(date.slice(5))}月`;
    return date;
  };
  const tickIndices = length => [...new Set(Array.from({length: Math.min(5, length)}, (_, i) => Math.round(i * (length - 1) / Math.max(1, Math.min(5, length) - 1))))];
  function scale(values, zero = true, plotFrame = frame) {
    let low = Math.min(...values, ...(zero ? [0] : [])), high = Math.max(...values, ...(zero ? [0] : []));
    if (low === high) { const pad = Math.max(Math.abs(low) * .02, 1); low = zero ? Math.min(low, 0) : low - pad; high += pad; }
    const raw = (high - low) / 4, power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].find(unit => unit * power >= raw) * power;
    low = Math.floor(low / step) * step; high = Math.ceil(high / step) * step;
    const ticks = Array.from({length: Math.round((high - low) / step) + 1}, (_, i) => low + i * step);
    return {ticks, y: value => plotFrame.bottom - (value - low) / (high - low) * (plotFrame.bottom - plotFrame.top)};
  }
  function grid(axis, {currency = false, plotFrame = frame} = {}) {
    const max = Math.max(...axis.ticks.map(Math.abs));
    const unit = currency && max >= 1e8 ? 1e8 : currency && max >= 1e4 ? 1e4 : 1;
    const label = value => {
      if (value === 0) return '0';
      const scaled = value / unit;
      let digits = 0;
      while (digits < 8 && Math.abs(Math.round(scaled * 10 ** digits) - scaled * 10 ** digits) > 1e-7) digits++;
      return `${number(scaled, digits)}${unit === 1e8 ? '亿' : unit === 1e4 ? '万' : ''}`;
    };
    return axis.ticks.map(value => `<g class="chart-grid"><line x1="${plotFrame.left}" x2="${plotFrame.right}" y1="${rounded(axis.y(value))}" y2="${rounded(axis.y(value))}" stroke="${value === 0 ? '#dce3ec' : '#edf0f5'}" stroke-width="1"/><text class="chart-axis svg-label" x="${plotFrame.left - 9}" y="${rounded(axis.y(value) + 3)}" text-anchor="end">${esc(label(value))}</text></g>`).join('');
  }
  function dates(labels, x) {
    return tickIndices(labels.length).map(i => `<text class="chart-axis chart-date svg-label" x="${rounded(x(i))}" y="205" text-anchor="${i === 0 && labels.length > 1 ? 'start' : i === labels.length - 1 && labels.length > 1 ? 'end' : 'middle'}">${esc(dateLabel(labels[i]))}</text>`).join('');
  }
  function cashflow(report, {type = '柱状', width = 360} = {}) {
    if (prefs.mask) return hidden();
    const chartWidth = Math.max(360, Math.round(numeric(width)) || 360), plotFrame = {...frame, right: chartWidth - 10};
    const {labels = [], income = [], expense = []} = report.bars || {};
    const revenues = labels.map((_, i) => numeric(income[i])), costs = labels.map((_, i) => numeric(expense[i]));
    if (!labels.length || ![...revenues, ...costs].some(value => value !== 0)) return empty('本期暂无收支数据');
    const axis = scale([...revenues, ...costs], true, plotFrame), span = plotFrame.right - plotFrame.left, step = span / labels.length;
    const x = i => plotFrame.left + step * (i + .5), baseline = axis.y(0), line = type === '折线';
    const plot = line ? [revenues, costs].map((values, index) => `<polyline class="cashflow-line" data-series="${index ? 'expense' : 'income'}" points="${values.map((value, i) => `${rounded(x(i))},${rounded(axis.y(value))}`).join(' ')}" fill="none" stroke="${index ? ink.expense : ink.income}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`).join('') : '';
    const points = labels.map((label, i) => {
      const detail = `${label}：收入 ${money(revenues[i])}；支出 ${money(costs[i])}`;
      const width = Math.min(12, step * .31), gap = Math.min(3, step * .10);
      const marks = [revenues[i], costs[i]].map((value, index) => line
        ? `<circle class="chart-hover-dot${labels.length === 1 ? ' chart-single-dot' : ''}" cx="${rounded(x(i))}" cy="${rounded(axis.y(value))}" r="3.2" fill="${index ? ink.expense : ink.income}" stroke="#fff" stroke-width="1.5" opacity="${labels.length === 1 ? 1 : 0}"/>`
        : `<rect class="cashflow-bar" data-series="${index ? 'expense' : 'income'}" x="${rounded(x(i) + (index ? gap / 2 : -width - gap / 2))}" y="${rounded(Math.min(baseline, axis.y(value)))}" width="${rounded(width)}" height="${rounded(Math.abs(baseline - axis.y(value)))}" rx="${Math.min(2, width / 2)}" fill="${index ? ink.expense : ink.income}"/>`).join('');
      return `<g class="chart-point" tabindex="0" role="button" data-action="chart-point" data-value="${esc(detail)}" aria-label="${esc(detail)}"><title>${esc(detail)}</title><rect class="chart-hit" x="${rounded(plotFrame.left + step * i)}" y="${plotFrame.top}" width="${rounded(step)}" height="${plotFrame.bottom - plotFrame.top}" fill="transparent"/><line class="chart-hover-guide" x1="${rounded(x(i))}" x2="${rounded(x(i))}" y1="${plotFrame.top}" y2="${plotFrame.bottom}" stroke="#b8c6d8" stroke-dasharray="3 4" opacity="0"/>${marks}</g>`;
    }).join('');
    return `<div class="chart-container cashflow-container"><svg class="cashflow-chart" viewBox="0 0 ${chartWidth} 220" role="group" aria-label="收入和支出${line ? '折线' : '柱状'}图">${grid(axis, {currency: true, plotFrame})}${plot}${points}${dates(labels, x)}</svg><p class="chart-value" aria-live="polite" aria-atomic="true"></p></div>`;
  }
  function bars(report) { return cashflow(report); }
  function donut(items, {title = '分类占比', action = '', center = '合计', percent = false, limit = Infinity} = {}) {
    if (prefs.mask) return hidden();
    const data = items.filter(item => Number.isFinite(Number(item.value)) && Number(item.value) > 0).map(item => ({...item, value: Number(item.value)})).sort((a, b) => b.value - a.value);
    const total = data.reduce((sum, item) => sum + item.value, 0);
    if (!total) return empty('暂无分布数据');
    let offset = 0;
    const arcs = data.map(item => {
      const share = item.value / total * 100, gap = data.length === 1 ? 0 : Math.min(.85, share * .24);
      const detail = `${item.name}：${percent ? number(item.value, 1) + '%' : money(item.value)}，占比 ${share.toFixed(1)}%`;
      const arc = `<circle class="donut-arc${action ? ' chart-point' : ''}" cx="100" cy="100" r="77" fill="none" stroke="${colorFor(item.name)}" stroke-width="18" pathLength="100" data-share="${share}" stroke-dasharray="${share - gap} ${100 - share + gap}" stroke-dashoffset="${-offset - gap / 2}" transform="rotate(-90 100 100)" ${action ? `tabindex="0" role="button" data-action="${esc(action)}" data-value="${esc(item.name)}"` : ''} aria-label="${esc(detail)}"><title>${esc(detail)}</title></circle>`;
      offset += share;
      return arc;
    }).join('');
    const totalText = percent ? number(total, 1) + '%' : money(total), size = Math.min(24, Math.max(10, 192 / Math.max(8, String(totalText).length)));
    const rows = data.map(item => `<${action ? 'button' : 'div'} class="donut-legend-row" ${action ? `type="button" data-action="${esc(action)}" data-value="${esc(item.name)}"` : ''}><span class="legend-name"><i style="background:${colorFor(item.name)}"></i>${esc(item.name)}</span><span class="legend-amount">${esc(percent ? number(item.value, 1) + '%' : money(item.value))}${percent ? '' : `<small>${(item.value / total * 100).toFixed(1)}%</small>`}</span></${action ? 'button' : 'div'}>`);
    const count = Number.isFinite(limit) ? Math.max(1, Math.floor(limit)) : rows.length;
    const extra = rows.length > count ? `<details class="chart-more donut-more"><summary>全部分类（${rows.length}）</summary>${rows.slice(count).join('')}</details>` : '';
    return `<div class="donut-layout"><svg class="donut-chart" viewBox="0 0 200 200" role="${action ? 'group' : 'img'}" aria-label="${esc(title)}，${esc(totalText)}"><circle cx="100" cy="100" r="77" fill="none" stroke="#f0f3f7" stroke-width="18"/>${arcs}<text x="100" y="91" text-anchor="middle" class="donut-caption">${esc(center)}</text><text x="100" y="118" text-anchor="middle" class="donut-total" style="font-size:${size}px" ${String(totalText).length > 18 ? 'textLength="126" lengthAdjust="spacingAndGlyphs"' : ''}>${esc(totalText)}</text></svg><div class="donut-legend">${rows.slice(0, count).join('')}${extra}</div></div>`;
  }
  function comparison(report) {
    if (prefs.mask) return hidden();
    const rows = report.categories.map(row => ({...row, current: numeric(row.current), previous: numeric(row.previous)})).filter(row => row.current || row.previous).sort((a, b) => b.current - a.current);
    if (!rows.length) return empty('本期和上一周期均暂无支出');
    const max = Math.max(...rows.flatMap(row => [Math.abs(row.current), Math.abs(row.previous)]), Number.EPSILON);
    const content = rows.map(row => {
      const change = row.previous ? (row.current - row.previous) / Math.abs(row.previous) * 100 : null;
      const changeText = change === null ? '上期无支出' : change > 999 ? '增长超过 999%' : `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`;
      const detail = `${row.name}：本期 ${money(row.current)}，上一周期 ${money(row.previous)}`;
      return `<button type="button" class="comparison-row" data-action="visual-category" data-value="${esc(row.name)}" aria-label="${esc(detail)}"><span class="comparison-heading"><strong>${esc(row.name)}</strong><small>${esc(changeText)}</small></span>${[row.current, row.previous].map((value, i) => `<span class="comparison-measure${i ? ' previous' : ''}"><span class="comparison-track"><i style="width:${Math.abs(value) / max * 100}%;max-width:none;min-width:0"></i></span><span class="comparison-amount">${esc(money(value))}</span></span>`).join('')}</button>`;
    });
    return `<div class="legend comparison-legend"><span><b></b>本期</span><span><b class="previous"></b>上一周期</span></div><div class="comparison-list">${content.slice(0, 5).join('')}${content.length > 5 ? `<details class="chart-more comparison-more"><summary>全部分类（${content.length}）</summary>${content.slice(5).join('')}</details>` : ''}</div>`;
  }
  function lines({labels, series, width = 360}) {
    if (prefs.mask) return hidden();
    const chartWidth = Math.max(360, Math.round(numeric(width)) || 360), plotFrame = {...frame, right: chartWidth - 10};
    const available = value => value !== null && value !== '' && Number.isFinite(Number(value));
    const data = series.filter(item => item.values.slice(0, labels.length).some(available));
    if (!labels.length || !data.length) return empty('所选证券缺少共同日期的数据，无法比较');
    const values = data.flatMap(item => item.values.slice(0, labels.length)).filter(available).map(Number), axis = scale(values, false, plotFrame);
    const x = i => labels.length === 1 ? (plotFrame.left + plotFrame.right) / 2 : plotFrame.left + i * (plotFrame.right - plotFrame.left) / (labels.length - 1);
    const plot = data.map((item, index) => {
      const segments = []; let segment = [];
      labels.forEach((_, i) => { const value = Number(item.values[i]); if (available(item.values[i])) segment.push(`${rounded(x(i))},${rounded(axis.y(value))}`); else { if (segment.length) segments.push(segment); segment = []; } });
      if (segment.length) segments.push(segment);
      return segments.map(points => points.length === 1 ? `<circle cx="${points[0].split(',')[0]}" cy="${points[0].split(',')[1]}" r="3" fill="${palette[index % palette.length]}"/>` : `<polyline points="${points.join(' ')}" fill="none" stroke="${palette[index % palette.length]}" stroke-width="2.2" stroke-linejoin="round"/>`).join('');
    }).join('');
    const hitStep = labels.length > 1 ? (plotFrame.right - plotFrame.left) / (labels.length - 1) : plotFrame.right - plotFrame.left;
    const points = labels.map((label, i) => {
      const detail = `${label} · ${data.map(item => `${item.name} ${available(item.values[i]) ? number(item.values[i], 2) : '暂无数据'}`).join('；')}`;
      const left = Math.max(plotFrame.left, x(i) - hitStep / 2), right = Math.min(plotFrame.right, x(i) + hitStep / 2);
      return `<g class="chart-point" tabindex="0" role="button" data-action="chart-point" data-value="${esc(detail)}" aria-label="${esc(detail)}"><title>${esc(detail)}</title><rect class="chart-hit" x="${rounded(left)}" y="${plotFrame.top}" width="${rounded(right - left)}" height="${plotFrame.bottom - plotFrame.top}" fill="transparent"/><line class="chart-hover-guide" x1="${rounded(x(i))}" x2="${rounded(x(i))}" y1="${plotFrame.top}" y2="${plotFrame.bottom}" stroke="#b8c6d8" stroke-dasharray="3 4" opacity="0"/>${data.map((item, index) => available(item.values[i]) ? `<circle class="chart-hover-dot" cx="${rounded(x(i))}" cy="${rounded(axis.y(Number(item.values[i])))}" r="3.2" fill="${palette[index % palette.length]}" stroke="#fff" stroke-width="1.5" opacity="0"/>` : '').join('')}</g>`;
    }).join('');
    return `<div class="chart-container"><svg class="cashflow-chart comparison-line-chart" viewBox="0 0 ${chartWidth} 220" role="group" aria-label="自选股归一化历史走势">${grid(axis, {plotFrame})}${plot}${points}${dates(labels, x)}</svg><p class="chart-value" aria-live="polite" aria-atomic="true"></p><div class="series-legend">${data.map((item, i) => `<span><i style="background:${palette[i % palette.length]}"></i>${esc(item.name)}</span>`).join('')}</div></div>`;
  }
  return {bars, cashflow, donut, comparison, lines};
}

