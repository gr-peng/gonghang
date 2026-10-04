import {finance} from './finance-api.js';

export function createImprovement(ctx) {
  const {esc,card,btn,loading,modal,closeSheet,toast,go,render}=ctx;
  let snapshot;
  const labels={transfer:'转账',subscribe:'模拟申购',redeem:'模拟赎回',friend:'朋友',landlord:'房租收款方',reserve:'灵活现金',growth:'长期均衡',micro:'100 元以下',small:'100–999 元',medium:'1,000–9,999 元',large:'10,000 元及以上'};
  const status={candidate:'待审核',approved:'已审核，待训练',rejected:'已舍弃',consumed:'已用于训练'};
  const slots=s=>[s.kind,s.recipient,s.product_id,s.amount_band].filter(Boolean).map(x=>labels[x]||x).join(' · ');
  async function improvementPage(paint) {
    paint(loading());snapshot=await finance('/improvement');const s=snapshot;
    const evaluation=s.model.evaluation;
    paint(card(`<div class="row between"><h2>一起改进助手</h2><span class="tag">${s.enabled?'已加入':'未加入'}</span></div><div class="setting-row"><span>分享脱敏修正</span>${btn(s.enabled?'退出':'了解并加入',s.enabled?'improvement-optout':'improvement-optin','small glass')}</div>`)+
      card(`<h2 class="mb">我的反馈</h2>${s.samples.length?s.samples.map(sample=>`<article class="feedback-sample"><div class="row between mb"><strong>${status[sample.status]||sample.status}</strong>${btn('删除','improvement-delete','small',`data-id="${esc(sample.id)}"`)}</div><div class="feedback-change"><span>${esc(slots(sample.before))}</span><span aria-label="改为">↓</span><strong>${esc(slots(sample.after))}</strong></div>${sample.status==='candidate'?`<div class="actions">${btn('采用修正','improvement-approve','small primary',`data-id="${esc(sample.id)}"`)}${btn('舍弃','improvement-reject','small',`data-id="${esc(sample.id)}"`)}</div>`:''}</article>`).join(''):'<div class="empty"><p>还没有分享修正</p></div>'}`)+
      card(`<details><summary>当前模型与验证</summary><p class="model-version">${esc(s.model.version)}</p>${evaluation?Object.entries(evaluation).map(([key,value])=>`<div class="setting-row"><span>${esc({bank_intent:'银行意图',profile_reason:'画像与计划依据',extract:'记账提取',clarify:'缺失信息追问',summary:'统计摘要'}[key]||key)}</span><strong>${value.correct} / ${value.count}</strong></div>`).join(''):'<p class="dialog-message">当前版本尚未登记本轮发布评估。</p>'}${s.model.released_at?`<p class="period-stamp">${esc(s.model.released_at)}</p>`:''}</details>`));
  }
  const actions={
    'improvement-open':()=>{closeSheet();go('improvement');},
    'improvement-optin':()=>modal('加入助手改进计划',`<p class="dialog-message">你核对并改正聊天草稿后，只分享操作种类、方案和金额区间。原始聊天、姓名、账号、精确金额、验证码都不分享。反馈先由你审核，再供后续训练。</p><p class="dialog-message">可以随时退出并删除待训练反馈。</p><div class="actions">${btn('暂不加入','close-sheet')}${btn('同意并加入','improvement-enable','primary')}</div>`),
    'improvement-enable':async()=>{await finance('/improvement/consent',{method:'POST',body:{enabled:true}});closeSheet();toast('已加入改进计划');render();},
    'improvement-optout':()=>modal('退出改进计划',`<p class="dialog-message">停止分享，并删除尚未训练的反馈。已训练版本需重新构建才能移除样本影响。</p><div class="actions">${btn('取消','close-sheet')}${btn('确认退出','improvement-disable','primary')}</div>`),
    'improvement-disable':async()=>{await finance('/improvement/consent',{method:'POST',body:{enabled:false}});closeSheet();toast('已退出并清除待训练反馈');render();},
    'improvement-approve':async b=>{await finance(`/improvement/${b.dataset.id}/review`,{method:'POST',body:{approve:true}});render();},
    'improvement-reject':async b=>{await finance(`/improvement/${b.dataset.id}/review`,{method:'POST',body:{approve:false}});render();},
    'improvement-delete':async b=>{await finance(`/improvement/${b.dataset.id}`,{method:'DELETE'});render();}
  };
  return {pages:{improvement:improvementPage},actions};
}
