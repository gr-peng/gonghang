import {finance} from './finance-api.js';
import {investmentNavigation} from './investment-shell.js';
import {helpButton} from './learning.js';

export function createWealth(ctx){
  const {state,prefs,esc,money,card,btn,icon,loading,modal,closeSheet,toast,render,go,ask}=ctx;
  let view,filter='all',editing=null,requestId;
  const cash=value=>value===null||value===undefined?'待核对':money(value/100);
  const uuid=()=>globalThis.crypto?.randomUUID?.()||`wealth-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const detail=(label,value)=>`<div><dt>${label}</dt><dd>${cash(value)}</dd></div>`;
  function status(){
    if(!view.accounts.length&&!view.confirmed)return '待登记';
    if(!view.current)return '待更新估值';
    if(view.missing_ids.length)return '待补齐可用与还款';
    return view.confirmed?'已核对':'待核对完整性';
  }
  async function page(paint){
    const epoch=state.epoch;paint(investmentNavigation('wealth')+loading());
    const next=await finance('/wealth');if(epoch!==state.epoch)return;view=next;
    const b=view.breakdown, p=view.planning, list=view.accounts.filter(a=>filter==='all'||a.kind===filter);
    const rows=list.map(a=>`<button class="finance-operation-row wealth-account" data-action="wealth-edit" data-id="${a.id}"><span class="operation-icon">${icon(a.kind==='asset'?'investment':'ledger')}</span><span class="grow"><strong>${esc(a.name)}</strong><span>${esc((a.kind==='asset'?view.asset_categories:view.liability_categories)[a.category])} · ${esc(a.as_of)}${view.stale_ids.includes(a.id)?' · 待更新':''}${view.missing_ids.includes(a.id)?' · 待补齐':''}</span></span><strong>${cash(Math.round(Number(a.amount)*100))}</strong></button>`).join('');
    const allocation=view.allocation.filter(a=>a.amount_minor>0).sort((a,b)=>b.amount_minor-a.amount_minor);
    paint(investmentNavigation('wealth')+
      card(`<div class="row between"><h2>已登记净资产 ${helpButton('net')}</h2><button class="icon-button" data-action="mask" aria-label="${prefs.mask?'显示金额':'隐藏金额'}">${icon('eye')}</button></div><div class="hero-amount money" data-wealth-metric="net">${cash(view.net_minor)}</div><div class="finance-metrics"><div><span>资产</span><strong data-wealth-metric="assets">${cash(view.assets_minor)}</strong></div><div><span>负债</span><strong data-wealth-metric="liabilities">${cash(view.liabilities_minor)}</strong></div></div><div class="row between mt"><span class="tag">手动登记 · ${status()}</span>${btn('核对','wealth-review','small glass')}</div>`,'wealth-summary')+
      card(`<div class="row between"><h2>可动用资金 ${helpButton('available')}</h2></div><div class="hero-amount money" data-wealth-metric="available">${cash(view.available_minor)}</div>${view.shortfall_minor?`<div class="planning-gap">近期资金缺口 <strong>${cash(view.shortfall_minor)}</strong></div>`:''}<details class="planning-basis"><summary>查看计算明细</summary><dl class="funding-breakdown">${detail('可立即使用的现金与存款',b.immediate_minor)}${detail('减：应急预留',b.emergency_minor)}${detail('减：目标预留',b.goal_minor)}${detail('减：其他预留',b.other_reserved_minor)}${detail('减：未来30天待还',b.due_minor)}</dl><p>按手动登记计算；基金、房产与模拟持仓不作为即时现金。未完整核对时，明细仅为已登记小计。</p></details>${!view.ready?`<div class="mt">${btn(view.stale_ids.length||view.missing_ids.length?'补齐登记':'核对完整性','wealth-review','small glass')}</div>`:''}`)+
      card(`<div class="row between"><h2>资产分布 ${helpButton('diversify')}</h2></div>${allocation.length?`<div class="wealth-allocation">${allocation.map(a=>`<div><div class="row between"><span>${esc(a.label)}</span><strong>${cash(a.amount_minor)}</strong></div><div class="progress-bar"><span style="width:${prefs.mask?0:100*a.amount_minor/view.assets_minor}%"></span></div></div>`).join('')}</div>`:'<div class="empty">登记资产后显示分布</div>'}`)+
      card(`<div class="row between"><h2>资产与负债</h2>${helpButton('valuation')}</div><div class="actions mt">${btn('登记资产','wealth-add','primary small','data-kind="asset"')}${btn('登记负债','wealth-add','glass small','data-kind="liability"')}</div><div class="segmented glass wealth-filter" role="group" aria-label="账户筛选">${[['all','全部'],['asset','资产'],['liability','负债']].map(([key,label])=>`<button data-action="wealth-filter" data-value="${key}" aria-pressed="${key===filter}">${label}</button>`).join('')}</div>${rows||'<div class="empty">还没有这类登记</div>'}`)+
      card(`<div class="row between"><h2>与账本一起看 ${helpButton('cashflow')}</h2>${btn('资金规划','investment-section','small','data-value="investment"')}</div>${p.comparable?`<dl class="funding-breakdown">${detail('账本估算的应急金目标',p.emergency_target_minor)}${detail('已登记应急预留',view.ready?b.emergency_minor:null)}${detail('应急金缺口',p.emergency_gap_minor)}${detail('目标预留后月均结余',p.monthly_after_goal_minor)}</dl><details class="planning-basis"><summary>收支依据</summary><p>${esc(p.basis.start_date)} — ${esc(p.basis.end_date)}，${p.basis.observed_months} 个有记录月份。月度结余不叠加到已有余额；预留用途需在账户中分别登记。</p></details>`:`<div class="mt">${btn(p.ledger_scope==='personal'?'补齐个人收支':'核对个人账本与计划','journey-profile','glass finance-wide')}</div>`}<div class="actions mt">${btn('问问我的资产','wealth-ask','small glass')}${btn('看懂术语','learn-library','small glass')}</div>`));
  }
  const field=(label,name,value='',extra='')=>`<label class="field-label" for="wealth-${name}">${label}</label><input class="field" id="wealth-${name}" name="${name}" value="${esc(value??'')}" ${extra}>`;
  const amountField=(label,name,value='',required=false)=>field(label,name,value,`inputmode="decimal" pattern="(0|[1-9][0-9]{0,9})(\\.[0-9]{1,2})?" ${required?'required':''} placeholder="${required?'0.00':'未知可留空'}"`);
  function categoryOptions(kind,selected){return Object.entries(kind==='asset'?view.asset_categories:view.liability_categories).map(([key,label])=>`<option value="${key}" ${selected===key?'selected':''}>${esc(label)}</option>`).join('');}
  function extraFields(kind,category,a={}){
    if(kind==='liability')return amountField('未来30天待还（元） '+helpButton('due'),'due',a.due)+`<p class="dialog-message">近期无需还款填 0，不确定可留空。</p>`;
    if(!['cash','deposit'].includes(category))return `<p class="dialog-message">按当前估值登记，不计入即时可用现金。</p>`;
    return amountField('可立即使用（元） '+helpButton('liquidity'),'available',a.available)+`<details class="planning-basis" ${editing?'open':''}><summary>预留用途</summary><p>各项互不重叠，未来30天待还另在负债中登记。</p>${amountField('应急预留（元）','emergency',a.emergency||'0',true)}${amountField('目标预留（元）','goal',a.goal||'0',true)}${amountField('其他预留（元）','other_reserved',a.other_reserved||'0',true)}</details>`;
  }
  function editor(a=null,kind='asset'){
    if(!view)return;
    editing=a;requestId=uuid();kind=a?.kind||kind;
    const category=a?.category||(kind==='asset'?'cash':'credit');
    modal(a?'编辑登记':kind==='asset'?'登记资产':'登记负债',`<form id="wealth-account-form" data-kind="${kind}">${field('账户简称','name',a?.name,'maxlength="40" required placeholder="如：日常银行卡"')}<label class="field-label" for="wealth-category">类别</label><select class="field" name="category" id="wealth-category">${categoryOptions(kind,category)}</select>${amountField(kind==='asset'?'当前余额或市值（元）':'当前未还总额（元）','amount',a?.amount,true)}${field('估值日期','as_of',a?.as_of||view.today,`type="date" max="${view.today}" required`)}<div id="wealth-extra">${extraFields(kind,category,a||{})}</div><p class="field-feedback" role="alert"></p><div class="actions">${a?btn('删除登记','wealth-delete','danger','type="button"'):btn('取消','close-sheet','glass','type="button"')}<button type="submit" class="btn primary">保存登记</button></div></form>`);
  }
  function review(){
    if(!view)return;
    const issues=view.accounts.filter(a=>view.stale_ids.includes(a.id)||view.missing_ids.includes(a.id));
    modal('核对资产与负债',`<form id="wealth-review-form"><p class="dialog-message">请检查所有账户是否登记、余额日期是否准确，避免把同一资产重复登记。未登记不等于没有。</p>${issues.map(a=>`<button type="button" class="btn glass finance-wide mb" data-action="wealth-edit" data-id="${a.id}">${esc(a.name)} · ${view.stale_ids.includes(a.id)?'更新日期与估值':'补齐可用或还款'}</button>`).join('')}${!view.accounts.length?'<p>当前没有登记。确认表示你的资产和负债都为零。</p>':''}<label class="cashflow-confirm"><input type="checkbox" name="complete" required><span>已核对资产、负债与预留，没有遗漏或重复</span></label><p class="field-feedback" role="alert"></p><button type="submit" class="btn primary finance-wide">确认核对</button></form>`);
  }
  async function submit(event){
    const form=event.target;if(!['wealth-account-form','wealth-review-form','wealth-delete-form'].includes(form.id))return;
    event.preventDefault();const button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;
    const error=form.querySelector('[role="alert"]');error.textContent='';
    try{
      if(form.id==='wealth-account-form'){
        const body={kind:form.dataset.kind,...Object.fromEntries(new FormData(form))};
        for(const key of ['available','due'])if(body[key]==='')body[key]=null;
        await finance('/wealth/accounts'+(editing?'/'+editing.id:''),{method:editing?'PUT':'POST',body:{...body,...(editing?{expected_version:editing.version}:{request_id:requestId})}});
      }else if(form.id==='wealth-review-form')await finance('/wealth/confirm',{method:'POST',body:{expected_revision:view.revision,complete:true}});
      else await finance('/wealth/accounts/'+editing.id,{method:'DELETE',body:{expected_version:editing.version}});
      closeSheet();await render();toast(form.id==='wealth-review-form'?'已记录核对结果':'登记已更新');
    }catch(e){if(form.isConnected)error.textContent=e.message;}
    finally{button.disabled=false;}
  }
  return {pages:{wealth:page},actions:{
    'wealth-open':()=>{closeSheet();go('wealth');},
    'wealth-add':b=>editor(null,b.dataset.kind),
    'wealth-edit':b=>{const a=view?.accounts.find(a=>a.id===b.dataset.id);if(a)editor(a);},
    'wealth-filter':b=>{if(['all','asset','liability'].includes(b.dataset.value)){filter=b.dataset.value;render();}},
    'wealth-review':review,
    'wealth-delete':()=>modal('删除登记',`<form id="wealth-delete-form"><p class="dialog-message">删除「${esc(editing.name)}」的登记？这不会改变实际账户余额。</p><p class="field-feedback" role="alert"></p><div class="actions">${btn('返回编辑','wealth-edit','glass',`type="button" data-id="${editing.id}"`)}<button class="btn danger" type="submit">删除登记</button></div></form>`),
    'wealth-ask':()=>ask('我的净资产和可动用资金是多少？','finance'),
  },onSubmit:submit,onChange:event=>{if(event.target.id==='wealth-category')document.getElementById('wealth-extra').innerHTML=extraFields(event.target.form.dataset.kind,event.target.value);}};
}
