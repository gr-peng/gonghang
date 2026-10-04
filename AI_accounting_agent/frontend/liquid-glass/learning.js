import {finance} from './finance-api.js';

const sources={
  fund:['证监会 · 基金基础知识','https://www.csrc.gov.cn/csrc/c100211/c1452112/content.shtml'],
  risk:['证监会 · 认识基金投资风险','https://www.csrc.gov.cn/tianjin/c105377/c05f4e3a5604e4c7cac5fd6ddd85c231c/content.shtml'],
  disclosure:['证监会 · 基金信息披露','https://www.csrc.gov.cn/csrc/c106256/c1653985/content.shtml'],
};
export const terms={
  net:{title:'净资产',plain:'把拥有的资产减去还欠的钱，才是净资产。',example:'有 5 万元资产、2 万元待还贷款，净资产就是 3 万元。',check:'这里汇总你登记的人民币估值。未登记的资产与负债不会自动补齐；净资产可以为负数。'},
  available:{title:'可动用资金',plain:'现在能使用，而且没有安排给其他用途的钱。',example:'现金 2 万元，留 6 千应急、2 千给目标、1 千付账单、近期还款 1 千，可动用的是 1 万元。',check:'只从现金和存款的“可立即使用”金额计算。房产、基金市值、每月结余、模拟持仓不直接当作可用现金。'},
  cashflow:{title:'结余与余额',plain:'结余是一段时间里赚的减去花的；余额是某一天还剩多少。',example:'本月结余 2 千元，不代表银行卡余额只有 2 千元；之前的积蓄、借款和转账也会影响余额。',check:'账本用于判断每月能积累多少，资产总览用于盘点已有多少，两者不能相加。'},
  emergency:{title:'应急金',plain:'专门留给失业、疾病等意外开销，随时能取用的一笔钱。',example:'每月生活和还款需要 3 千元，选择预留 3 个月，目标就是 9 千元。',check:'覆盖月数由你决定；这只是预算估算。应急金、目标和其他预留应互不重叠，目标进度不会自动变成账户余额。'},
  liquidity:{title:'流动性',plain:'需要用钱时，能否及时取出来，取出会付出多大代价。',example:'余额 1 万元的定期存款，如果现在只能取用 2 千元，就只登记 2 千元为可立即使用。',check:'赎回申请成功不代表已经到账。真实产品要看开放日、锁定期、到账时间及提前退出成本。',source:'risk'},
  risk:{title:'风险等级',plain:'提示可能承担多大的波动和损失，不是收益或保本承诺。',example:'如果这笔钱下个月要付房租，就要先考虑届时能否取出、金额是否可能减少。',check:'本应用 R1 / R3 是模拟方案标签，四题偏好仅辅助体验。真实产品评级和投资者适当性以销售机构资料及测评为准。',source:'risk'},
  nav:{title:'净值',plain:'可以把单位净值理解为一份基金在估值时点的价值。',example:'持有 1 千份、单位净值 1.20 元，估算市值为 1,200 元，实际赎回金额还可能受费用和价格变化影响。',check:'净值高低本身不能判断划算与否；看清估值日期及产品资料。',source:'fund'},
  drawdown:{title:'回撤',plain:'从之前的高点跌下来多少，帮助理解持有过程中的波动。',example:'从 1 万元跌到 8 千元，是 20% 回撤；从 8 千元回到 1 万元需要涨 25%。',check:'历史最大回撤不能保证未来最多只亏这么多。此处示例为算术演示。',source:'risk'},
  fee:{title:'费用',plain:'买入、持有、卖出都可能产生成本，不能只看收益数字。',example:'用 1 万元试算，先扣假设一次性费用 0.5%，就有 50 元费用，参与涨跌的是剩余 9,950 元。',check:'试算不套用真实基金费率。管理费、托管费、销售服务费等可能持续计提；实际计算方式和赎回费看产品文件。',source:'disclosure'},
  diversify:{title:'分散配置',plain:'让资金分布在不同资产中，减少对单一资产的依赖。',example:'买了三只都重仓同一行业的基金，并不一定实现了充分分散。',check:'资产分布图只展示登记结构，不自动给出买卖比例；分散也不能消除所有风险。',source:'fund'},
  valuation:{title:'估值与日期',plain:'资产金额对应某一个日期；市场变化以后，旧金额可能不再准确。',example:'房产填估算市值，房贷另记未还总额；不要先从房产值扣房贷，再在负债重复扣一次。',check:'所有登记为你手动填写的人民币金额。超过 30 天的记录需更新后再核算可动用资金；30 天是本应用的复核规则。'},
  due:{title:'负债与近期还款',plain:'负债记还欠的总额；未来 30 天待还只记这段时间实际要付的金额。',example:'房贷还欠 30 万元，下月还款 3 千元：总负债填 30 万元，近期待还填 3 千元。',check:'近期待还从现金中单独扣除，别再把它放进“其他预留”。总负债已经从净资产扣除，不能再重复减近期待还。'},
};

export const helpButton=(key)=>`<button type="button" class="btn glass term-button" data-action="learn-term" data-term="${key}" aria-label="解释${terms[key].title}" title="解释${terms[key].title}">?</button>`;

export function createLearning(ctx){
  const {esc,money,modal,btn,state}=ctx;
  let savedForm;
  function preserveForm(){
    if(document.getElementById('sheet').open&&document.querySelector('#sheet form:not(#wealth-scenario-form)'))savedForm={route:state.route,nodes:[...document.getElementById('sheet-content').childNodes]};
  }
  // Contextual help must not discard an in-progress account edit, including
  // fields the user has typed but has not saved yet.
  document.getElementById('sheet').addEventListener('close',()=>{
    const saved=savedForm;savedForm=null;if(!saved)return;
    queueMicrotask(()=>{if(state.route!==saved.route||document.getElementById('sheet').open)return;document.getElementById('sheet-content').replaceChildren(...saved.nodes);document.getElementById('sheet').showModal();});
  });
  function showTerm(key){
    const t=terms[key];if(!t)return;
    preserveForm();
    const source=sources[t.source];
    modal(t.title,`<div class="learn-body"><p class="learn-definition">${esc(t.plain)}</p><div class="learn-example"><h3>举个例子</h3><p>${esc(t.example)}</p></div><p>${esc(t.check)}</p>${source?`<a class="btn small glass" href="${source[1]}" target="_blank" rel="noopener noreferrer">${esc(source[0])}</a>`:''}<div class="actions">${['fee','risk','drawdown','nav'].includes(key)?btn('试算涨跌与费用','learn-scenario','primary'):btn('所有词条','learn-library','glass')}${btn('知道了','close-sheet','glass')}</div></div>`);
  }
  function library(){
    modal('看懂财富管理',`<label class="field-label" for="learn-search">找一个术语</label><input id="learn-search" class="field" type="search" placeholder="净资产、回撤、费用…"><div class="learn-list">${Object.entries(terms).map(([key,t])=>`<button class="btn glass" data-action="learn-term" data-term="${key}">${esc(t.title)}</button>`).join('')}</div><p id="learn-empty" class="hidden">没有匹配的词条</p>${btn('试算涨跌与费用','learn-scenario','primary finance-wide')}`);
  }
  function scenario(){
    modal('涨跌与费用试算',`<form id="wealth-scenario-form"><p class="dialog-message">自己设定一个涨跌情景，看看费用后还剩多少。</p><label class="field-label" for="scenario-principal">投入金额（元）</label><input class="field" id="scenario-principal" name="principal" inputmode="decimal" required value="10000"><label class="field-label" for="scenario-change">整个持有期间的假设涨跌（%）</label><input class="field" id="scenario-change" name="change_percent" type="number" min="-100" max="100" step="0.01" required value="-10"><label class="field-label" for="scenario-fee">假设一次性费用（%）</label><input class="field" id="scenario-fee" name="fee_percent" type="number" min="0" max="100" step="0.01" required value="0.5"><p class="field-feedback" role="alert"></p><button class="btn primary finance-wide" type="submit">算一算</button><div id="scenario-result" aria-live="polite"></div></form>`);
  }
  async function submit(event){
    const form=event.target;if(form.id!=='wealth-scenario-form')return;
    event.preventDefault();const button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;
    const output=form.querySelector('#scenario-result'),error=form.querySelector('[role="alert"]');error.textContent='';output.innerHTML='';
    try{
      const r=await finance('/wealth/scenario',{method:'POST',body:Object.fromEntries(new FormData(form))});
      if(!form.isConnected)return;
      if(JSON.stringify(Object.fromEntries(new FormData(form)))!==JSON.stringify(r.assumptions)){error.textContent='输入已改变，请重新试算';return;}
      output.innerHTML=`<div class="learn-example"><h3>在这个假设下</h3><dl class="funding-breakdown">${[['一次性费用',r.fee_minor],['参与涨跌的金额',r.invested_minor],['期末金额',r.final_minor],['相对投入的变化',r.change_minor]].map(([label,value])=>`<div><dt>${label}</dt><dd>${money(value/100)}</dd></div>`).join('')}</dl><p>${esc(r.method)}</p><p>这是你设定的情景，不是收益预测，也不执行投资。</p></div>`;
    }catch(e){if(form.isConnected)error.textContent=e.message;}
    finally{button.disabled=false;}
  }
  function input(event){
    if(event.target.id==='learn-search'){
      const query=event.target.value.trim();let count=0;
      document.querySelectorAll('.learn-list button').forEach(button=>{const show=terms[button.dataset.term].title.includes(query)||terms[button.dataset.term].plain.includes(query);button.classList.toggle('hidden',!show);if(show)count++;});
      document.getElementById('learn-empty').classList.toggle('hidden',count>0);
    }
    if(event.target.closest('#wealth-scenario-form'))document.getElementById('scenario-result').innerHTML='';
  }
  return {actions:{'learn-term':b=>showTerm(b.dataset.term),'learn-library':library,'learn-scenario':scenario},onSubmit:submit,onInput:input};
}
