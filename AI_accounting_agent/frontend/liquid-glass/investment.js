import {helpButton} from './learning.js';
import {finance} from './finance-api.js';
import {investmentNavigation} from './investment-shell.js';

export function createInvestment(ctx) {
  const {state,prefs,book,esc,money,number,card,btn,icon,loading,range,go,closeSheet}=ctx;
  const cash=value=>money(value/100);
  const amount=(key,value)=>`<strong class="money" data-plan-metric="${key}">${cash(value)}</strong>`;
  const detail=(label,value)=>`<div><dt>${label}</dt><dd>${cash(value)}</dd></div>`;
  const datesLabel=dates=>`${esc(dates.start_date)} — ${esc(dates.end_date)}`;

  function monthControls() {
    return `<div class="row between investment-month"><label class="pill glass month-choice"><span>${esc(state.month.replace('-','年'))}月</span>${icon('calendar')}<input id="month-picker" class="time-input" type="month" value="${esc(state.month)}" aria-label="选择月份"></label><button class="icon-button glass" data-action="mask" aria-label="${prefs.mask?'显示金额':'隐藏金额'}">${icon('eye')}</button></div>`;
  }

  function ledgerCard(report) {
    return card(`<div class="row between"><h2>账本结余 ${helpButton('cashflow')}</h2><span class="period-stamp">${esc(state.month)}</span></div><div class="hero-amount" data-plan-metric="month-net">${money(report.net.current)}</div><div class="finance-metrics"><div><span>收入</span><strong data-plan-metric="month-income">${money(report.summary.income_total)}</strong></div><div><span>支出</span><strong data-plan-metric="month-expense">${money(report.summary.expense_total)}</strong></div></div><div class="actions mt">${btn('查看账单','investment-ledger','small glass')}${btn('收支图表','investment-chart','small glass')}</div>`,'planning-summary');
  }

  function fundingCard(s) {
    const p=s.planning;
    if(!p) return card(`<h2>结余规划</h2><div class="empty">规划暂不可用</div>${btn('刷新','refresh','small glass')}`,'funding-plan');
    const available=p.ready===true;
    const status=!available?(p.basis.observed_months<3?'补充月度收支':'核对开销与负债'):p.monthly.net_minor<=0?'先平衡收支':p.goal.shortfall_minor>0?'调整目标计划':!s.risk_completed?'完善风险偏好':'比较资金方案';
    const action=!available?(p.basis.observed_months<3?'statement-import':'journey-profile'):p.monthly.net_minor<=0?'investment-basis-ledger':p.goal.shortfall_minor>0||!s.risk_completed?'journey-profile':'journey-compare';
    return card(`<div class="row between"><h2>月均资金规划</h2>${btn('调整','journey-profile','small')}</div><div class="plan-preview"><span>目标预留后月均结余</span>${available?amount('after-goal',p.after_goal_minor):'<strong data-plan-metric="after-goal">待核对</strong>'}</div><dl class="funding-breakdown">${detail('月均结余',p.monthly.net_minor)}${p.monthly.commitment_extra_minor?detail('额外固定预留',p.monthly.commitment_extra_minor):''}${p.monthly.debt_minor?detail('月还款预留',p.monthly.debt_minor):''}${detail('目标每月预留',p.goal.monthly_required_minor)}</dl>${p.goal.shortfall_minor?`<div class="planning-gap"><span>目标每月缺口</span>${amount('goal-shortfall',p.goal.shortfall_minor)}</div>`:''}<details class="planning-basis"><summary>计算依据 · ${p.basis.observed_months} 个有记录月份</summary><p class="period-stamp">${datesLabel(p.basis)}</p><dl class="funding-breakdown">${detail('月均收入',p.monthly.income_minor)}${detail('月均净支出',p.monthly.net_expense_minor)}${detail('固定开销补足',p.monthly.commitment_extra_minor)}${detail('目标剩余金额',p.goal.remaining_minor)}<div><dt>规划月数</dt><dd>${p.goal.months} 个月</dd></div></dl>${btn('查看依据账单','investment-basis-ledger','small glass')}</details><div class="mt">${btn(status,action,'primary finance-wide')}</div>`,'funding-plan');
  }

  function goalsCard(s) {
    const p=s.planning;
    if(!p)return '';
    const g=p.goal, percent=g.target_minor?Math.min(100,g.recorded_minor/g.target_minor*100):0;
    return card(`<div class="row between"><h2>目标与预留</h2>${btn('设置','journey-profile','small')}</div>${g.target_minor?`<h3 class="mt">${esc(s.profile.goal_name)}</h3><div class="goal-progress-line"><span>已记录</span><strong>${cash(g.recorded_minor)} / ${cash(g.target_minor)}</strong></div><div class="progress-bar"><span style="width:${prefs.mask?0:percent}%"></span></div><dl class="funding-breakdown">${detail('目标剩余',g.remaining_minor)}${detail('每月计划',g.monthly_required_minor)}</dl><div class="actions">${btn('记录目标进度','goal-deposit','small glass')}${btn('进度记录','goal-history','small glass')}</div>`:`<div class="goal-empty-action">${btn('设置储蓄目标','journey-profile','small glass')}</div>`}<div class="reserve-target"><div><strong>应急金目标 ${helpButton('emergency')}</strong><span>${s.profile.reserve_months} 个月生活开销</span></div><strong class="money">${p.quality?.has_expenses?cash(p.emergency.target_minor):'待补充开销'}</strong></div>${s.wealth?.planning?.comparable?`<dl class="funding-breakdown">${detail('已登记应急预留',s.wealth.ready?s.wealth.breakdown.emergency_minor:NaN)}${detail('应急金缺口',s.wealth.planning.emergency_gap_minor??NaN)}</dl>`:''}<div class="actions mt">${btn('核对应急预留','wealth-open','small glass')}</div><div class="row between planning-preference"><span>风险偏好</span>${btn(s.risk_completed?'查看偏好':'待完善','journey-profile','small')}</div>`,'investment-goal');
  }

  async function investmentPage(paint) {
    const epoch=state.epoch, dates=range('month');
    paint(investmentNavigation('investment')+loading());
    const [s,payload]=await Promise.all([finance('/overview'),book('/reports/aggregate?'+new URLSearchParams(dates))]);
    if(epoch!==state.epoch)return;
    state.investmentMonthRange={...dates};
    state.investmentBasis=s.planning?.basis;
    paint(investmentNavigation('investment')+monthControls()+ledgerCard(payload.custom)+fundingCard(s)+goalsCard(s)+`<div class="investment-next">${btn(icon('assistant')+'讨论资金规划','journey-ask-plan','glass')}${btn('进入投资研究 '+icon('next'),'journey-research','glass')}</div>`);
  }

  async function homeBlock() {
    const s=await finance('/overview'), p=s.planning;
    if(!p)return '';
    const observed=p.ready===true;
    return card(`<div class="row between"><h2>结余规划</h2>${btn('投资 '+icon('next'),'investment','small')}</div><div class="plan-preview"><span>目标预留后月均结余</span><strong>${observed?cash(p.after_goal_minor):'待核对收支'}</strong></div><div class="feature-links"><button data-action="journey-profile">${icon('calendar')}目标与偏好</button><button data-action="wealth-open">${icon('investment')}资产总览</button></div>`,'plan-card');
  }

  function openLedger(dates) {
    if(!dates)return;
    state.monthInitialized=true;
    state.ledgerRange={start_date:dates.start_date,end_date:dates.end_date};
    state.filter={category:'',type:'',q:''};go('ledger');
  }
  return {pages:{investment:investmentPage},homeBlock,actions:{
    'investment-section':button=>{const target=button.dataset.value;if(!['wealth','investment','research','holdings'].includes(target))return;closeSheet();go(target);},
    'investment-ledger':()=>openLedger(state.investmentMonthRange),
    'investment-basis-ledger':()=>openLedger(state.investmentBasis),
    'investment-chart':()=>{state.visualPeriod='month';state.visualRange=null;go('visualization');},
    'investment-holdings':()=>{closeSheet();go('holdings');}
  }};
}
