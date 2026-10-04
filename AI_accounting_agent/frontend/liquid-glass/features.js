import {quickTemplates} from './templates.js';
import {createVisuals, maximumDrawdown, normalizeSeries} from './visuals.js';
import {adviceMatchesContext} from './advice-check.js';

export function createFeatures(ctx) {
  const {state, prefs, storage, book, trader, esc, money, number, card, btn, icon, loading, empty, range, monthPicker, overview, chart, segmented, modal, closeSheet, toast, render, go, ask, today} = ctx;
  const visual = createVisuals(ctx);
  const periodNames = {week:'周', month:'月', year:'年', custom:'自定义'};
  const benchmarkFiles = {month:'FinAgent_vs_SSE100_Month_11.png', quarter:'FinAgent_vs_SSE100_Quarter.png', year:'FinAgent_vs_SSE100_Year.png'};
  state.visualPeriod = 'month';
  state.visualType = '折线';
  state.visualBreakdown = '占比';
  state.visualRange = null;
  state.benchmarkPeriod = 'month';
  state.compareCodes = [];
  state.compareDays = 30;
  state.adviceResults = {};
  state.advicePending = new Set();
  state.chatContext = null;
  function fitCharts() {
    if(typeof document==='undefined')return;
    const cash=document.querySelector('.analytics-trend .cashflow-container');
    if(state.route==='visualization'&&cash&&state.visualReport){
      const width=Math.max(360,Math.round(cash.clientWidth));
      if(cash.querySelector('svg')?.viewBox.baseVal.width!==width)cash.outerHTML=visual.cashflow(state.visualReport,{type:state.visualType,width});
    }
    const research=document.querySelector('.comparison-card .chart-container');
    if(state.route==='dashboard'&&research&&state.compareSeries){
      const width=Math.max(360,Math.round(research.clientWidth));
      if(research.querySelector('svg')?.viewBox.baseVal.width!==width)research.outerHTML=visual.lines({...state.compareSeries,width});
    }
  }
  if(typeof window!=='undefined'){
    let resizeFrame;
    window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(fitCharts);},{passive:true});
  }
  async function rerenderLocal() {
    const scrollY=window.scrollY, nextEpoch=state.epoch+1;
    await render();
    if(state.epoch===nextEpoch)window.scrollTo(0,scrollY);
  }

  function selectedRange() { return state.visualPeriod === 'custom' ? (state.visualRange || range('month')) : range(state.visualPeriod); }
  function rangeControls() {
    const dates = selectedRange();
    return `<div class="analytics-controls"><div class="analytics-control-main"><label class="pill glass month-choice"><span>${esc(state.month.replace('-','年'))}月</span>${icon('calendar')}<input class="time-input" type="month" id="month-picker" value="${esc(state.month)}" aria-label="选择月份"></label><button class="icon-button glass" data-action="mask" aria-label="${prefs.mask?'显示金额':'隐藏金额'}">${icon('eye')}</button></div>${segmented(['周','月','年','自定义'],periodNames[state.visualPeriod],'visual-period')}</div>`+
      (state.visualPeriod==='custom' ? card(`<form id="visual-range-form"><div class="date-grid"><label class="field-label" for="visual-start">开始日期<input class="field" id="visual-start" type="date" required value="${esc(dates.start_date)}"></label><label class="field-label" for="visual-end">结束日期<input class="field" id="visual-end" type="date" required value="${esc(dates.end_date)}"></label></div><button class="btn primary mt" type="submit">应用区间</button><p id="visual-range-error" class="field-feedback" role="alert"></p></form>`,'analytics-date-panel') : '');
  }
  function visualSummary(report) {
    const diff=report.net.difference;
    const change=prefs.mask?'••••':`${diff>0?'+':''}${money(diff)}`;
    return card(`<div class="balance-heading"><span>本期结余</span><span class="balance-currency">CNY</span></div><strong class="balance-value money">${money(report.net.current)}</strong><div class="balance-comparison"><span>较上期</span><strong class="${diff>=0?'good':'bad'}">${change}</strong></div><div class="analytics-totals"><div class="metric"><span><i class="cashflow-dot income"></i>收入</span><strong class="money">${money(report.summary.income_total)}</strong></div><div class="metric"><span><i class="cashflow-dot expense"></i>支出</span><strong class="money">${money(report.summary.expense_total)}</strong></div></div>`,'analytics-balance');
  }
  function analyticsLoading() {
    return `<div class="analytics-workspace analytics-loading loading" aria-busy="true" aria-label="正在读取图表">${rangeControls()}<div class="analytics-top-grid"><div class="surface-card analytics-skeleton"><i></i><b></b><span></span></div><div class="surface-card analytics-skeleton plot-skeleton"><i></i><span></span></div></div></div>`;
  }
  async function visualizationPage(paint) {
    const epoch=state.epoch;
    paint(analyticsLoading());
    const dates = selectedRange();
    const [payload, sharedGoal] = await Promise.all([
      book('/reports/aggregate?'+new URLSearchParams(dates)),
      ctx.goalCard({compact:true})
    ]);
    if(epoch!==state.epoch)return;
    const report=payload.custom;
    state.visualReport=report;
    state.visualReportRange={...dates};
    const dateStart=dates.start_date.slice(0,4)===dates.end_date.slice(0,4)?5:2;
    const dateLabel=dates.start_date.slice(dateStart).replaceAll('-','.')+' — '+dates.end_date.slice(dateStart).replaceAll('-','.');
    const plot=visual.cashflow(report,{type:state.visualType});
    const trendCard=card(`<div class="analytics-card-heading"><h2>收支趋势</h2><span class="analytics-date" title="${esc(dates.start_date+' — '+dates.end_date)}">${esc(dateLabel)}</span></div><div class="chart-toolbar">${segmented(['折线','柱状'],state.visualType,'visual-type')}<div class="legend cashflow-legend"><span><b class="expense"></b>支出</span><span><b class="green"></b>收入</span></div></div>${plot}`,'analytics-trend');
    const breakdown=card(`<div class="analytics-card-heading"><h2>支出分布</h2>${segmented(['占比','对比'],state.visualBreakdown,'visual-breakdown')}</div>${state.visualBreakdown==='对比'?visual.comparison(report):visual.donut(report.pie,{action:'visual-category',title:'支出分类占比',center:'总支出',limit:5})}`,'analytics-breakdown');
    paint(`<div class="analytics-workspace">${rangeControls()}<div class="analytics-top-grid">${visualSummary(report)}${trendCard}</div><div class="analytics-bottom-grid">${breakdown}<aside class="analytics-next">${sharedGoal}<div class="analytics-actions">${btn(icon('assistant')+'分析这段收支','visual-ask','primary')}${btn('详细分析 '+icon('next'),'visual-insights','glass')}</div></aside></div></div>`);
    fitCharts();
  }
  function templateButtons(items) {
    return items.map(item=>`<button class="template-item" data-action="quick-template" data-id="${esc(item.id)}"><span>${esc(item.label)}</span>${icon('next')}</button>`).join('');
  }
  function renderTemplateList(query='') {
    const container=document.querySelector('#template-list');
    if(!container) return;
    const items=quickTemplates[state.mode].filter(t=>(t.label+t.text).includes(query.trim()));
    const groups=[...new Set(items.map(t=>t.group))];
    container.innerHTML=items.length?groups.map(group=>`<h3 class="template-group">${esc(group)}</h3>${templateButtons(items.filter(t=>t.group===group))}`).join(''):empty('没有匹配的快捷问题');
  }
  function quickMenu() {
    modal(state.mode==='book'?'记账快捷输入':'投研快捷输入',`<div class="quick-links">${state.mode==='book'?btn('文字记账','quick-entry','small glass')+btn('票据识别','ocr-entry','small glass')+btn('查看图表','visualization','small glass'):btn('投资可视化','dashboard','small glass')+btn('风险观察','risk','small glass')+btn('自选研究','watchlist','small glass')}</div><label class="sr-only" for="template-search">搜索快捷问题</label><input class="field mt" id="template-search" type="search" placeholder="搜索快捷问题"><div id="template-list"></div>`);
    renderTemplateList();
  }
  function insertTemplate(id) {
    if(state.chatBusy){toast('请先等待回复或停止等待');return;}
    const item=quickTemplates[state.mode].find(t=>t.id===id);
    if(!item) return;
    state.chatDraft=item.text;
    state.chatContext=null;
    closeSheet();
    const input=document.querySelector('#chat-input');
    if(input){input.value=item.text;input.focus();input.setSelectionRange(item.text.length,item.text.length);}
  }
  function chatTools() {
    return `<div class="row between chat-tools">${state.mode==='book'?btn('将输入解析为记账草稿','chat-parse-bill','small glass',state.chatBusy?'disabled':''):''}${btn('全部模板','chat-add','small glass')}</div>`;
  }
  function replyHTML(content) {
    const text=String(content||'');
    const labels={observation:'观察',observations:'观察',reason:'原因',reasons:'原因',suggestion:'建议',suggestions:'建议',summary:'解读',advice:'建议',risks:'风险',actions:'可采取的行动',conclusion:'结论',answer:'回答'};
    try {
      const parsed=JSON.parse(text.replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,''));
      if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)&&Object.keys(parsed).some(k=>labels[k])){
        const plain=value=>Array.isArray(value)?value.map(plain).join('\n'):value&&typeof value==='object'?Object.entries(value).map(([k,v])=>`${labels[k]||k}：${plain(v)}`).join('\n'):String(value??'');
        return Object.entries(parsed).map(([key,value])=>`<div class="reply-section"><strong>${esc(labels[key]||key)}</strong><p>${esc(plain(value))}</p></div>`).join('');
      }
    }catch{}
    // Escape first, then support only emphasis and headings; never insert model HTML.
    return esc(text).replace(/^#{1,4} (.+)$/gm,'<strong>$1</strong>').replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  }
  function askWithContext(text,data) {ask(text,'book',data);}
  async function insightsPage(paint) {
    const epoch=state.epoch;
    paint(monthPicker()+loading());
    const dates=state.insightRange||range(state.period);
    const [payload, context]=await Promise.all([book('/reports/aggregate?'+new URLSearchParams(dates)),book('/advice/context?'+new URLSearchParams({reference_date:dates.end_date}))]);
    if(epoch!==state.epoch)return;
    const report=payload.custom;
    state.currentReport=report;
    state.adviceContext=context;
    const key=dates.start_date+':'+dates.end_date+':'+state.insightTab+':'+(state.ledgerInfo?.ledger_scope||'');
    state.adviceKey=key;
    let content='';
    if(state.insightTab==='收支'){
      const o=context.overview;
      content=overview(report)+card(`<h2>近 30 天财务概览</h2><p class="period-stamp">${esc(o.period.start)} — ${esc(o.period.end)}</p><div class="goal-details"><span>收入</span><strong>${money(o.summary.income)}</strong><span>支出</span><strong>${money(o.summary.expense)}</strong><span>结余</span><strong>${money(o.summary.net)}</strong></div>`)+card(`<h2>大额支出</h2>${o.largest_transactions.length?o.largest_transactions.map(t=>`<div class="analysis-row"><span><strong>${esc(t.description)}</strong><small>${esc(t.date)} · ${esc(t.category)}</small></span><strong class="money">${money(t.amount)}</strong></div>`).join(''):empty('近 30 天暂无支出')}`)+btn('打开完整可视化','visualization','glass');
    }else if(state.insightTab==='习惯'){
      const b=context.behavior;
      content=card(`<h2>最近 180 天收支</h2><p class="period-stamp">${esc(b.period.start)} — ${esc(b.period.end)}</p>${chart(b.monthly_totals.map(x=>x.expense),b.monthly_totals.map(x=>x.month),b.monthly_totals.map(x=>x.income),'最近 180 天收支')}<div class="legend"><span><b></b>支出</span><span><b class="green"></b>收入</span></div>`)+card(`<h2>消费模式</h2>${b.category_patterns.length?b.category_patterns.map(p=>`<div class="analysis-row"><span><strong>${esc(p.category)}</strong><small>月均 ${money(p.average)} · 波动 ${money(p.volatility)}</small></span><span class="tag">${{up:'增加',down:'减少',stable:'平稳'}[p.trend]||'—'}</span></div>`).join(''):empty('暂无消费模式')}`)+card(`<h2>波动提示</h2><div class="report-text">${esc(prefs.mask?'金额已隐藏':ctx.habitText(b))}</div>`);
    }else{
      const a=context.advice;
      content=card(`<h2>储蓄与支出结构</h2><p class="period-stamp">${esc(a.period.start)} — ${esc(a.period.end)}</p><div class="metrics"><div class="metric"><small>净结余</small><strong>${money(a.net)}</strong></div><div class="metric"><small>储蓄率</small><strong>${prefs.mask?'••••':a.income_total>0?(a.savings_rate*100).toFixed(1)+'%':'无收入基数'}</strong></div></div>${visual.donut(a.category_share.map(x=>({name:x.category,value:x.amount})),{title:'近 90 天主要支出分类',center:'主要分类'})}`);
    }
    const result=state.adviceResults[key], pending=state.advicePending.has(key);
    content+=card(`<div class="row between"><h2>AI ${state.insightTab==='习惯'?'行为分析':state.insightTab==='建议'?'财务建议':'收支解读'}</h2>${btn(pending?'生成中…':result?'重新生成':'生成分析','advice-generate','small glass',pending?'disabled':'')}</div><div class="report-text">${prefs.mask?'内容已隐藏':result?replyHTML(result):'结合上面的账本数据生成解读。'}</div>`);
    paint(monthPicker()+segmented(['收支','习惯','建议'],state.insightTab,'insight-tab')+segmented(['周','月','年'],periodNames[state.period],'period')+`<p class="period-stamp center">${esc(dates.start_date)} — ${esc(dates.end_date)}</p>`+content);
  }
  async function generateAdvice(button) {
    const key=state.adviceKey;
    if(state.advicePending.has(key))return;
    const tab=state.insightTab;
    const context=tab==='习惯'?state.adviceContext.behavior:tab==='建议'?state.adviceContext.advice:{selected:state.currentReport,overview:state.adviceContext.overview};
    state.advicePending.add(key);
    button.disabled=true;button.textContent='生成中…';
    try{
      const question=`请根据这些账本统计生成${tab==='习惯'?'消费行为分析':tab==='建议'?'节省开支的定性建议':'收支解读'}，分为观察、原因和建议。仅解释已提供的数据。没有预算、负债、个人画像数据，不得声称预算执行率、超支或个人风险等级。不要提出新的具体金额或比例，不要编造分类占比。无法核对时用定性描述。统计：${JSON.stringify(context)}`;
      let accepted=false;
      for(let attempt=0;attempt<2;attempt++){
        const result=await book('/chat',{method:'POST',body:{messages:[{role:'user',content:question+(attempt?' 上次回答的数字未通过核对。本次完全不要写数字，只给出有依据的定性观察和建议。':'')}],include_bill_context:false,max_new_tokens:800},timeout:180000});
        if(adviceMatchesContext(result.reply,context)){state.adviceResults[key]=result.reply;accepted=true;break;}
      }
      if(!accepted)state.adviceResults[key]='本次分析包含无法从当前统计核对的内容，暂不展示。请依据上方数据，或重新生成分析。';
    }catch(error){state.adviceResults[key]='生成未完成：'+error.message;}
    finally{state.advicePending.delete(key);if(state.route==='insights'&&state.adviceKey===key)render();}
  }

  async function dashboardPage(paint) {
    const epoch=state.epoch;
    paint(loading());
    const [portfolio,watchlist]=await Promise.all([trader('/trader/portfolio'),trader('/trader/watchlist')]);
    if(epoch!==state.epoch)return;
    state.portfolio=portfolio;state.watchlist=watchlist;
    if(!state.compareCodes.length)state.compareCodes=watchlist.slice(0,3).map(s=>s.code);
    const selected=watchlist.filter(s=>state.compareCodes.includes(s.code));
    const quotes=await Promise.allSettled(selected.map(async s=>({...s,rows:await trader(`/trader/stock/${encodeURIComponent(s.code)}/kline?days=${state.compareDays}`)})));
    if(epoch!==state.epoch)return;
    const series=normalizeSeries(quotes.filter(r=>r.status==='fulfilled').map(r=>r.value));
    state.compareSeries=series;
    const p=portfolio.summary, sectors=Object.entries(portfolio.allocation?.by_sector||{}).map(([name,value])=>({name,value}));
    const holdings=portfolio.holdings.map(h=>({name:h.name,value:h.weight*100}));
    const listed=holdings.reduce((sum,h)=>sum+h.value,0);
    if(listed<99.99)holdings.push({name:'未列示部分',value:100-listed});
    const benchmark=`assets/${benchmarkFiles[state.benchmarkPeriod]}`;
    paint(`<div class="analytics-workspace"><div class="research-grid">`+
      card(`<div class="analytics-card-heading"><h2>历史组合总资产</h2><span class="tag">历史快照</span></div><p class="period-stamp">${esc((p.last_update||'').slice(0,10))}</p><span class="hero-amount money">${money(p.total_balance,p.base_currency)}</span><div class="actions">${btn('历史持仓','research-holdings','small glass')}${btn('风险观察','risk','small glass')}</div>`,'research-summary')+
      card(`<h2>行业配置</h2>${visual.donut(sectors,{title:'行业配置',center:'配置合计',percent:true,limit:5})}`,'allocation-card')+
      card(`<h2>持仓分布</h2>${visual.donut(holdings,{title:'持仓分布',center:'配置合计',percent:true,limit:5})}`,'allocation-card')+
      card(`<div class="analytics-card-heading"><h2>历史回测对比</h2>${btn('放大','benchmark-open','small')}</div><div class="mt">${segmented(['月','季','年'],{month:'月',quarter:'季',year:'年'}[state.benchmarkPeriod],'benchmark-period')}</div>${prefs.mask?'<div class="chart-hidden">收益图已隐藏</div>':`<button class="benchmark-button" data-action="benchmark-open" aria-label="放大历史回测对比图"><img class="benchmark-image" src="${benchmark}" alt="原项目 FinAgent 与 SSE100 的${{month:'月度',quarter:'季度',year:'年度'}[state.benchmarkPeriod]}历史回测对比"></button>`}`,'benchmark-card')+
      card(`<h2>自选走势</h2><div class="compare-select">${watchlist.map(s=>`<label><input type="checkbox" data-compare-code="${esc(s.code)}" ${state.compareCodes.includes(s.code)?'checked':''}>${esc(s.name)}</label>`).join('')}</div><div class="mt">${segmented(['10日','30日','90日'],state.compareDays+'日','compare-days')}</div>${visual.lines(series)}${quotes.some(r=>r.status==='rejected')?'<p class="error">部分证券行情读取失败，请刷新重试。</p>':''}`,'comparison-card')+
      card(`<h2>持仓收益</h2><div class="table-scroll"><table class="data-table"><thead><tr><th>证券</th><th>权重</th><th>持仓收益</th></tr></thead><tbody>${portfolio.holdings.map(h=>`<tr><td><button data-action="stock" data-code="${esc(h.code)}" data-name="${esc(h.name)}">${esc(h.name)}<small>${esc(h.code)}</small></button></td><td>${number(h.weight*100,1)}%</td><td class="${h.pnl_pct>=0?'good':'bad'}">${prefs.mask?'••••':`${h.pnl_pct>=0?'+':''}${number(h.pnl_pct)}%`}</td></tr>`).join('')}</tbody></table></div>`,'holdings-table')+`</div></div>`);
    fitCharts();
  }
  async function riskPage(paint) {
    const epoch=state.epoch;
    paint(loading());
    const portfolio=await trader('/trader/portfolio');
    if(epoch!==state.epoch)return;
    state.portfolio=portfolio;
    const holdings=[...portfolio.holdings].sort((a,b)=>b.weight-a.weight);
    const history=portfolio.nav_history||[], top=holdings[0], top3=holdings.slice(0,3).reduce((s,h)=>s+h.weight,0)*100;
    const drawdown=maximumDrawdown(history);
    state.riskContext={as_of:portfolio.summary.last_update,largest_holding:top,top_three_weight_pct:top3,observed_max_drawdown_pct:drawdown,nav_history:history,allocation:portfolio.allocation};
    paint(card(`<h2>集中度</h2><div class="risk-metrics"><div><small>最大单一持仓</small><strong>${top?number(top.weight*100,1)+'%':'—'}</strong><span>${esc(top?.name||'暂无持仓')}</span></div><div><small>前三大持仓合计</small><strong>${number(top3,1)}%</strong></div></div>${visual.donut(Object.entries(portfolio.allocation?.by_sector||{}).map(([name,value])=>({name,value})),{title:'行业风险敞口',percent:true})}`)+
      card(`<h2>可见区间最大回撤</h2><span class="hero-amount money">${history.length>1?number(drawdown)+'%':'—'}</span><p class="period-stamp">${esc(history[0]?.date||'—')} — ${esc(history.at(-1)?.date||'—')}</p>${chart(history.map(x=>x.nav),history.map(x=>x.date),null,'用于风险观察的历史净值')}`)+
      btn('让投研助手解读','risk-ask','primary')+btn('查看投资可视化','dashboard','glass'));
  }
  const pages={visualization:visualizationPage,dashboard:dashboardPage,risk:riskPage,insights:insightsPage};
  const actions={
    visualization:()=>{closeSheet();go('visualization');},
    dashboard:()=>{closeSheet();go('dashboard');},
    risk:()=>{closeSheet();go('risk');},
    investment:()=>{closeSheet();go('investment');},
    'research-holdings':()=>{state.investmentTab='持仓';go('research');},
    'visual-period':b=>{state.visualPeriod=Object.keys(periodNames).find(k=>periodNames[k]===b.dataset.value)||'month';render();},
    'visual-type':b=>{state.visualType=b.dataset.value;rerenderLocal();},
    'visual-breakdown':b=>{state.visualBreakdown=b.dataset.value;rerenderLocal();},
    'visual-insights':()=>{state.insightRange={...state.visualReportRange};state.period=state.visualPeriod;state.insightTab='收支';go('insights');},
    insights:()=>{state.insightRange=null;if(state.period==='custom')state.period='month';closeSheet();go('insights');},
    period:b=>{state.insightRange=null;state.period={'周':'week','月':'month','年':'year'}[b.dataset.value]||'month';render();},
    'visual-category':b=>{state.ledgerRange={...state.visualReportRange};state.filter={category:b.dataset.value,type:'expense',q:''};go('ledger');},
    'visual-ask':()=>askWithContext('请分析所选区间的收支结构、结余变化，并给出可核对的建议。',state.visualReport),
    'chat-add':quickMenu,
    'quick-template':b=>insertTemplate(b.dataset.id),
    'chat-parse-bill':b=>ctx.busy(b,async()=>{
      const text=document.querySelector('#chat-input').value.trim();
      if(!text)throw new Error('请先在输入框描述一笔收支。');
      const parsed=await book('/bills/parse',{method:'POST',body:{text,reference_date:today()},timeout:180000});
      ctx.applyParsed(parsed);
    }),
    'quick-entry':()=>{closeSheet();go('entry');},
    'advice-generate':generateAdvice,
    'benchmark-period':b=>{state.benchmarkPeriod={'月':'month','季':'quarter','年':'year'}[b.dataset.value];rerenderLocal();},
    'benchmark-open':()=>modal('历史回测对比',prefs.mask?'<p>收益图已隐藏</p>':`<img class="benchmark-image" src="assets/${benchmarkFiles[state.benchmarkPeriod]}" alt="历史回测完整图">`),
    'compare-days':b=>{state.compareDays=parseInt(b.dataset.value,10);rerenderLocal();},
    'risk-ask':()=>ask('请基于以下历史组合指标解读集中度和回撤风险，不要推断我的个人风险承受等级：'+JSON.stringify(state.riskContext),'trader')
  };
  function onInput(event){if(event.target.id==='template-search')renderTemplateList(event.target.value);}
  function onChange(event){
    if(event.target.id==='month-picker')state.insightRange=null;
    const code=event.target.dataset.compareCode;
    if(!code)return;
    if(event.target.checked){
      if(state.compareCodes.length>=5){event.target.checked=false;toast('最多同时比较 5 只股票');return;}
      state.compareCodes.push(code);
    }else{
      if(state.compareCodes.length<=1){event.target.checked=true;toast('至少保留 1 只股票');return;}
      state.compareCodes=state.compareCodes.filter(c=>c!==code);
    }
    rerenderLocal();
  }
  function onSubmit(event){
    const id=event.target.id;
    if(id!=='visual-range-form')return;
    event.preventDefault();
    if(id==='visual-range-form'){
      const start=document.querySelector('#visual-start').value,end=document.querySelector('#visual-end').value;
      if(!start||!end||start>end){document.querySelector('#visual-range-error').textContent='开始日期不能晚于结束日期。';return;}
      if((Date.parse(end)-Date.parse(start))/86400000>3660){document.querySelector('#visual-range-error').textContent='请选择不超过 10 年的区间。';return;}
      state.visualRange={start_date:start,end_date:end};render();return;
    }
  }
  return {pages,actions,onInput,onChange,onSubmit,chatTools,replyHTML};
}
