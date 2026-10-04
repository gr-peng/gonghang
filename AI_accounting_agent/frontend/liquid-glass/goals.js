import {finance} from './finance-api.js';

export function createGoals(ctx) {
  const {storage,prefs,esc,money,number,card,btn,modal,closeSheet,toast,go,render}=ctx;
  let snapshot, progressId, migration;
  const uid=()=>globalThis.crypto?.randomUUID?.()||`g-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const legacy=()=>storage.get('QINGCAI_SAVINGS_GOAL',storage.get('VISUALIZATION_GOAL',null));
  async function goalCard({compact=false}={}) {
    snapshot=await finance('/overview');const p=snapshot.profile,old=legacy();
    const legacyButton=old?.target>0?btn('接续本机旧目标','goal-migrate','small glass mt'):'';
    if(!p.goal_name||Number(p.goal_amount)<=0)return card(`<div class="row between"><h2>我的目标</h2><span class="goal-symbol" aria-hidden="true">${ctx.icon('investment')}</span></div><div class="goal-empty-action">${btn('设置目标','goal-edit','glass')}${legacyButton}</div>`,'analytics-goal');
    const recorded=Number(p.goal_recorded_amount),target=Number(p.goal_amount),percent=Math.min(100,recorded/target*100);
    if(compact)return card(`<div class="row between"><h2>我的目标</h2>${btn('调整','goal-edit','small')}</div><h3 class="compact-goal-name">${esc(p.goal_name)}</h3><div class="compact-goal-progress"><strong>${prefs.mask?'••••':percent.toFixed(1)+'%'}</strong><span class="money">${money(recorded)} / ${money(target)}</span></div><div class="progress-bar"><span style="width:${prefs.mask?0:percent}%"></span></div><div class="goal-details"><span>每月留出</span><strong>${money(snapshot.goal_monthly_minor/100)}</strong><span>计划期限</span><strong>${p.goal_months} 个月</strong></div><div class="actions">${btn('记录进度','goal-deposit','small glass')}${btn('进度记录','goal-history','small glass')}${btn('安排资金','investment','small glass')}</div>${legacyButton}`,'analytics-goal');
    return card(`<div class="row between"><h2>我的目标</h2>${btn('调整','goal-edit','small')}</div><div class="goal-ring" style="--progress:${prefs.mask?0:percent}%"><div><strong>${prefs.mask?'••••':percent.toFixed(1)+'%'}</strong><span>记录进度</span></div></div><h3 class="center">${esc(p.goal_name)}</h3><p class="center money">${money(recorded)} / ${money(target)}</p><div class="goal-details"><span>计划每月留出</span><strong>${money(snapshot.goal_monthly_minor/100)}</strong><span>计划期限</span><strong>${p.goal_months} 个月</strong></div><div class="actions">${btn('记录进度','goal-deposit','small glass')}${btn('进度记录','goal-history','small glass')}${btn('安排资金','investment','small glass')}</div>${legacyButton}`);
  }
  const actions={
    'goal-history':async()=>{
      const result=await finance('/goal/progress');
      modal('目标进度记录',result.records.length?result.records.map(r=>`<div class="setting-row"><div><strong>${money(Number(r.amount))}</strong><p>${r.created?esc(new Date(r.created*1000).toLocaleDateString('zh-CN')):'历史记录'}</p></div>${r.undone?'<span>已撤销</span>':btn('撤销','goal-undo','small',`data-id="${esc(r.request_id)}"`)}</div>`).join(''):'<div class="empty">暂无逐笔记录</div>');
    },
    'goal-undo':b=>modal('撤销这笔进度？',`<p class="dialog-message">只调整目标记录，不改变账户资金。</p><div class="actions">${btn('取消','goal-history')}${btn('确认撤销','goal-undo-confirm','primary',`data-id="${esc(b.dataset.id)}"`)}</div>`),
    'goal-undo-confirm':async b=>{if(b.disabled)return;b.disabled=true;try{await finance('/goal/progress/'+encodeURIComponent(b.dataset.id)+'/undo',{method:'POST',body:{}});closeSheet();toast('进度已撤销');render();}finally{if(b.isConnected)b.disabled=false;}},
    'goal-edit':()=>{closeSheet();go('profile');},
    'goal-deposit':async()=>{snapshot=await finance('/overview');progressId=uid();modal('记录目标进度',`<form id="shared-goal-progress-form"><label class="field-label" for="shared-goal-amount">本次增加金额（元）</label><input id="shared-goal-amount" class="field" inputmode="decimal" required pattern="[0-9]+(\\.[0-9]{1,2})?"><p class="dialog-message">记录用于跟踪目标，不会划转资金。</p><p class="field-feedback" role="alert"></p><button type="submit" class="btn primary finance-wide">记录进度</button></form>`);},
    'goal-migrate':async()=>{
      snapshot=await finance('/overview');migration=legacy();if(!migration?.target)return;
      modal('接续本机旧目标',`<h3>${esc(migration.name)}</h3><p class="hero-amount money">${money(migration.target)}</p><dl class="confirmation-details"><dt>已记录</dt><dd>${money(migration.manual||0)}</dd><dt>旧周期</dt><dd>${esc({week:'周',month:'月',year:'年'}[migration.cycle]||'月')}</dd></dl>${snapshot.profile.goal_name?`<p class="dialog-message">将替换当前目标「${esc(snapshot.profile.goal_name)}」。</p>`:''}<div class="actions">${btn('取消','close-sheet')}${btn('确认接续','goal-migrate-confirm','primary')}</div><p id="goal-migrate-error" class="field-feedback" role="alert"></p>`);
    },
    'goal-migrate-confirm':async button=>{
      if(button.disabled)return;button.disabled=true;
      try {
        const latest=await finance('/overview');
        await finance('/profile',{method:'POST',body:{...latest.profile,goal_action:'carry',goal_name:String(migration.name||'我的目标').slice(0,40),goal_amount:Number(migration.target).toFixed(2),goal_recorded_amount:Number(migration.manual||0).toFixed(2),goal_percent:Math.round(Number(migration.percent)||20),goal_cycle:['week','month','year'].includes(migration.cycle)?migration.cycle:'month'}});
        storage.set('QINGCAI_SAVINGS_GOAL',null);storage.set('VISUALIZATION_GOAL',null);closeSheet();toast('已接续目标与原记录');go('profile');
      } catch(error) {document.getElementById('goal-migrate-error').textContent=error.message;}
      finally {if(button.isConnected)button.disabled=false;}
    }
  };
  async function onSubmit(event) {
    const form=event.target;if(form.id!=='shared-goal-progress-form')return;
    event.preventDefault();const button=form.querySelector('button[type="submit"]');if(button.disabled)return;button.disabled=true;
    try {await finance('/goal/progress',{method:'POST',body:{amount:document.getElementById('shared-goal-amount').value,request_id:progressId,goal_id:snapshot.profile.goal_id}});closeSheet();toast('进度已记录');render();}
    catch(error) {form.querySelector('[role="alert"]').textContent=error.message;}
    finally {if(button.isConnected)button.disabled=false;}
  }
  return {goalCard,actions,onSubmit};
}
