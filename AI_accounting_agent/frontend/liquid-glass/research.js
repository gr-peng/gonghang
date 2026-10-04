import {finance} from './finance-api.js';
import {investmentNavigation} from './investment-shell.js';

// Historical research and ledger-based planning remain distinct from the
// simulator's cash and product balances. This page performs only read calls.
export function createResearch(ctx) {
  const {state, prefs, trader, esc, money, number, card, btn, icon, loading, empty, chart, modal} = ctx;
  const dateOf = portfolio => String(portfolio?.summary?.last_update || '').slice(0, 10);
  const percentage = value => {
    if (prefs.mask) return '••••';
    const amount = Number(value);
    return Number.isFinite(amount) ? `${amount > 0 ? '+' : ''}${number(amount, 2)}%` : '—';
  };
  const weight = value => prefs.mask ? '••••' : `${number(Number(value) * 100, 1)}%`;
  const retry = error => `<div class="error" role="alert">${esc(error?.message || '研究数据暂未加载')}${btn('重试', 'refresh', 'small glass')}</div>`;

  function planningLink(overview) {
    const planning = overview?.planning;
    const observed = Number(planning?.basis?.observed_months ?? overview?.cashflow?.observed_months ?? 0);
    const ready = observed >= 3 && planning?.ready !== false && Number.isSafeInteger(planning?.after_goal_minor);
    return card(`<div class="research-plan-summary"><div><span class="research-plan-label">${ready ? '目标预留后月均结余' : '月度规划'}</span>${ready ? `<strong class="money">${money(planning.after_goal_minor / 100)}</strong>` : '<strong>先补齐月度收支</strong>'}</div>${ready ? btn('资金规划', 'investment-section', 'small glass', 'data-value="investment"') : btn('核对收支', 'journey-profile', 'small glass')}</div>`, 'research-planning-link');
  }

  function researchTabs() {
    const holdings = state.investmentTab === '持仓';
    return `<div class="segmented research-tabs" role="tablist" aria-label="研究内容"><button type="button" role="tab" aria-selected="${!holdings}" data-action="investment-tab" data-value="自选">自选研究</button><button type="button" role="tab" aria-selected="${holdings}" data-action="investment-tab" data-value="持仓">历史组合</button></div>`;
  }

  function researchActions() {
    return `<div class="feature-links research-actions"><button type="button" data-action="dashboard">${icon('visualization')}投资可视化</button><button type="button" data-action="risk">${icon('shield')}风险观察</button></div><div class="actions research-actions">${btn('组合解读', 'portfolio-report', 'glass')}${btn('问投研助手', 'investment-ask', 'glass')}</div>`;
  }

  function watchlistContent(watchlist) {
    const favorites = state.favorites || [];
    const sorted = [...watchlist].sort((a, b) => Number(favorites.includes(b.code)) - Number(favorites.includes(a.code)));
    const rows = sorted.map(stock => `<button type="button" class="stock-row research-stock-row" data-action="stock" data-code="${esc(stock.code)}" data-name="${esc(stock.name)}"><span class="stock-symbol">${esc(String(stock.name).slice(0, 1))}</span><span class="grow"><strong>${esc(stock.name)}</strong><span class="research-symbol-code">${esc(stock.code)}${stock.quality?.status==='unavailable'?' · 行情待核对':stock.quality?.status==='partial'?' · 部分行情':''}</span></span>${favorites.includes(stock.code) ? `<span class="research-favorite" aria-label="本机已关注">${icon('star')}</span>` : ''}${icon('next')}</button>`).join('');
    return card(`<div class="row between mb"><h2>自选研究</h2><span class="tag">历史行情</span></div>${rows || empty('暂无研究列表')}`, 'list-card research-watchlist');
  }

  function historicalContent(portfolio) {
    const summary = portfolio.summary || {}, history = portfolio.nav_history || [], holdings = portfolio.holdings || [];
    const sum = holdings.reduce((total, holding) => total + Number(holding.weight || 0), 0);
    const remainder = Math.max(0, 1 - sum);
    const currency = summary.base_currency || 'CNY';
    const rows = holdings.map(holding => `<button type="button" class="stock-row research-holding-row" data-action="research-holding" data-code="${esc(holding.code)}"><span class="stock-symbol">${esc(String(holding.name).slice(0, 1))}</span><span class="grow"><strong>${esc(holding.name)}</strong><span class="research-symbol-code">${esc(holding.code)} · ${weight(holding.weight)}</span></span><span class="right research-holding-value"><strong class="money">${money(holding.market_value, currency)}</strong><span class="${Number(holding.pnl_pct) >= 0 ? 'good' : 'bad'}">${percentage(holding.pnl_pct)}</span></span></button>`).join('');
    const remainderRow = remainder > .0001 ? `<div class="stock-row research-unlisted"><span class="grow"><strong>未列示部分</strong></span><span>${weight(remainder)}</span></div>` : '';
    return card(`<div class="row between wrap"><h2>历史组合资产</h2><span class="tag">历史快照</span></div><p class="hero-amount money">${money(summary.total_balance, currency)}</p><div class="finance-metrics"><div><span>快照日涨跌</span><strong class="${Number(summary.day_change_pct) >= 0 ? 'good' : 'bad'}">${percentage(summary.day_change_pct)}</strong></div><div><span>年内收益</span><strong class="${Number(summary.ytd_return_pct) >= 0 ? 'good' : 'bad'}">${percentage(summary.ytd_return_pct)}</strong></div></div><div class="research-snapshot-date">${esc(dateOf(portfolio) || '日期未提供')} · ${esc(currency)}</div>`, 'research-history-summary') +
      card(`<div class="row between"><h2>历史净值</h2></div>${chart(history.map(point => point.nav), history.map(point => point.date), null, '历史组合净值')}${history.length ? `<div class="research-history-range">${esc(history[0].date)} — ${esc(history.at(-1).date)}</div>` : ''}`, 'research-history-chart') +
      card(`<div class="row between mb"><h2>历史持仓</h2><span class="tag">${holdings.length} 项</span></div>${rows || empty('暂无持仓记录')}${remainderRow}`, 'list-card research-holdings');
  }

  async function researchPage(paint) {
    const epoch = state.epoch;
    paint(investmentNavigation('research') + loading());
    const [planning, portfolio, watchlist] = await Promise.allSettled([
      finance('/overview'), trader('/trader/portfolio'), trader('/trader/watchlist'),
    ]);
    if (epoch !== state.epoch) return;
    if (portfolio.status === 'fulfilled') state.portfolio = portfolio.value;
    if (watchlist.status === 'fulfilled') state.watchlist = watchlist.value;
    const selected = state.investmentTab === '持仓' ? portfolio : watchlist;
    const content = selected.status === 'rejected' ? retry(selected.reason) : state.investmentTab === '持仓' ? historicalContent(portfolio.value) : watchlistContent(watchlist.value);
    paint(investmentNavigation('research') + `<div class="research-page-stack">${planningLink(planning.status === 'fulfilled' ? planning.value : null)}${researchTabs()}${content}${researchActions()}</div>`);
  }

  const actions = {
    'research-holding': button => {
      const portfolio = state.portfolio;
      const holding = portfolio?.holdings?.find(item => String(item.code) === button.dataset.code);
      if (!holding) return;
      const currency = portfolio.summary.base_currency || 'CNY';
      modal(holding.name, `<div class="row between mb"><span class="tag">历史快照</span><span class="research-snapshot-date">${esc(dateOf(portfolio))}</span></div><p class="hero-amount money">${money(holding.market_value, currency)}</p><dl class="confirmation-details research-holding-details"><dt>证券</dt><dd>${esc(holding.code)}</dd><dt>组合占比</dt><dd>${weight(holding.weight)}</dd><dt>持仓收益</dt><dd>${percentage(holding.pnl_pct)}</dd><dt>计价币种</dt><dd>${esc(currency)}</dd>${holding.concept && holding.concept !== '--' ? `<dt>主题</dt><dd>${esc(holding.concept)}</dd>` : ''}</dl>`);
    },
  };
  return {pages: {research: researchPage}, actions};
}
