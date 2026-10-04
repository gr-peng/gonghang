import {finance} from './finance-api.js';

export function createStatements(ctx) {
  const {prefs,esc,money,card,btn,icon,modal,closeSheet,toast,go,render,resetLedgerContext}=ctx;
  let preview, page=0, pending, revealed=false;
  const size=20;
  const feedback=()=>'<p id="statement-error" class="field-feedback" role="alert"></p>';
  const categories={income:['工资','奖金','副业','理财收益','退款','其他收入'],expense:['餐饮','出行','购物','生活缴费','娱乐','住房','医疗健康','教育学习','保险','人情往来','旅行','其他']};
  const rowInput=(r,key,extra='')=>`<input class="field" data-import-row="${r.row}" data-import-field="${key}" value="${esc(r[key])}" aria-label="第 ${r.row} 笔${{event_date:'日期',amount:'金额',description:'摘要'}[key]}" ${extra}>`;
  const select=(r,key,items)=>`<select class="field" data-import-row="${r.row}" data-import-field="${key}" aria-label="第 ${r.row} 笔${{type:'收支',category:'分类',movement:'资金性质'}[key]}">${items.map(([value,label])=>`<option value="${value}" ${r[key]===value?'selected':''}>${label}</option>`).join('')}</select>`;
  function cards() {
    return preview.rows.slice(page*size,(page+1)*size).map(r=>`<article class="statement-row"><div class="row between mb"><strong>第 ${r.row} 笔</strong><label class="row"><input type="checkbox" data-import-row="${r.row}" data-import-field="enabled" ${r.enabled?'checked':''}>导入</label></div><div class="statement-grid">${rowInput(r,'event_date','type="date" required')}${rowInput(r,'amount','inputmode="decimal" placeholder="金额" required')}${select(r,'type',[['','选择收支'],['expense','支出'],['income','收入']])}${select(r,'category',categories[r.type||'expense'].map(x=>[x,x]))}</div>${rowInput(r,'description','maxlength="512" required')}${select(r,'movement',[['cashflow','计入收支'],['internal_transfer','内部账户划转'],['principal','借款 / 投资本金划转']])}${r.errors.length?`<p class="field-feedback">${esc(r.errors.join('；'))}</p>`:''}</article>`).join('');
  }
  async function importPage(paint) {
    if(!preview) {
      paint(card(`<form id="statement-file-form"><div class="row between mb"><h2>导入银行流水</h2>${icon('upload')}</div><label class="statement-upload" for="statement-file">${icon('upload')}<span>选择 CSV 文件</span><input id="statement-file" type="file" accept=".csv,text/csv,text/tab-separated-values" required></label><label class="field-label mt" for="statement-account">账户别名</label><input class="field" id="statement-account" value="默认账户" maxlength="40" required>${feedback()}<button type="submit" class="btn primary finance-wide">预览流水</button></form>`)+card(`<details><summary>支持的格式</summary><p class="dialog-message">最多 500 笔、1 MB。支持日期、收支、金额、摘要、分类、流水号，也支持分别列出收入与支出金额。人民币金额保留两位小数。</p>${btn('下载 CSV 示例','statement-template','glass mt')}</details>`));
      return;
    }
    if(prefs.mask&&!revealed) {
      paint(card(`<h2>流水已读取</h2><p class="hero-amount">${preview.rows.length} 笔</p>${btn('查看并核对流水','statement-reveal','primary finance-wide')}`));return;
    }
    paint(card(`<div class="row between"><h2>核对流水</h2><span class="tag">${preview.rows.length} 笔</span></div><div class="row between mt">${btn('换个文件','statement-reset','small')}${btn('全部按收支计入','statement-cashflow','small glass')}</div>`)+`<form id="statement-review-form">${card(cards())}<div class="row between statement-pager">${btn('上一页','statement-prev','small',page?'':'disabled')}<span>${page+1} / ${Math.ceil(preview.rows.length/size)}</span>${btn('下一页','statement-next','small',(page+1)*size<preview.rows.length?'':'disabled')}</div>${feedback()}<button type="submit" class="btn primary finance-wide">确认选中流水</button></form>`);
  }
  function onChange(event) {
    const el=event.target, row=preview?.rows.find(r=>r.row===Number(el.dataset.importRow));if(!row)return;
    row[el.dataset.importField]=el.type==='checkbox'?el.checked:el.value;
    row.errors=[];
    if(el.dataset.importField==='type') {
      const choices=categories[row.type||'expense'];if(!choices.includes(row.category))row.category=row.type==='income'?'其他收入':'其他';
      const field=document.querySelector(`[data-import-row="${row.row}"][data-import-field="category"]`);
      if(field)field.innerHTML=choices.map(x=>`<option ${x===row.category?'selected':''}>${x}</option>`).join('');
    }
  }
  async function onSubmit(event) {
    const form=event.target;if(!['statement-file-form','statement-review-form'].includes(form.id))return;
    event.preventDefault();const button=form.querySelector('button[type="submit"]');if(button.disabled)return;button.disabled=true;
    try {
      if(form.id==='statement-file-form') {
        const file=document.getElementById('statement-file').files[0];if(!file||file.size>1_000_000)throw new Error('请选择不超过 1 MB 的 CSV 文件');
        const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件无法读取'));reader.readAsDataURL(file);});
        preview=await finance('/imports/preview',{method:'POST',body:{content,account_label:document.getElementById('statement-account').value.trim()}});page=0;revealed=false;await render();
      } else {
        const rows=preview.rows.filter(r=>r.enabled);if(!rows.length)throw new Error('请至少选择一笔流水');
        if(rows.some(r=>!r.event_date||!['income','expense'].includes(r.type)||!/^\d+(\.\d{1,2})?$/.test(r.amount)||Number(r.amount)<=0||!r.description.trim()))throw new Error('请先补齐日期、收支方向、金额和摘要');
        pending={challenge:preview.challenge,rows:rows.map(({row,event_date,type,amount,category,description,movement})=>({row,event_date,type,amount,category,description,movement}))};
        const total=kind=>rows.filter(r=>r.type===kind&&r.movement==='cashflow').reduce((sum,r)=>sum+Math.round(Number(r.amount)*100),0)/100;
        modal('确认导入',`<p class="hero-amount center">${rows.length} 笔</p><dl class="confirmation-details"><dt>收入</dt><dd>${money(total('income'))}</dd><dt>支出</dt><dd>${money(total('expense'))}</dd><dt>本金 / 内转</dt><dd>${rows.filter(r=>r.movement!=='cashflow').length} 笔</dd></dl><p class="dialog-message">导入后切换到个人账本，现有账本保留。重复流水会跳过。</p><div class="actions">${btn('继续核对','close-sheet')}${btn('确认入账','statement-commit','primary')}</div><p id="statement-commit-error" class="field-feedback" role="alert"></p>`);
      }
    } catch(error) {document.getElementById('statement-error').textContent=error.message;}
    finally {if(button.isConnected)button.disabled=false;}
  }
  const actions={
    'statement-import':()=>{closeSheet();go('import');},
    'statement-reset':()=>{preview=null;pending=null;render();},
    'statement-prev':()=>{page=Math.max(0,page-1);render();},
    'statement-next':()=>{page=Math.min(Math.ceil(preview.rows.length/size)-1,page+1);render();},
    'statement-reveal':()=>{revealed=true;render();},
    'statement-cashflow':()=>{preview.rows.forEach(r=>r.movement='cashflow');render();},
    'statement-commit':async button=>{
      if(button.disabled)return;button.disabled=true;
      try {const result=await finance(`/imports/${preview.id}/commit`,{method:'POST',body:pending,timeout:30000});
        preview=null;pending=null;resetLedgerContext();closeSheet();toast(`已入账 ${result.created} 笔，跳过重复 ${result.duplicates} 笔`);go('ledger');
      } catch(error) {document.getElementById('statement-commit-error').textContent=error.message;}
      finally {if(button.isConnected)button.disabled=false;}
    },
    'statement-template':()=>{
      const text='\ufeff日期,收支,金额,摘要,分类,流水号,资金性质\n2026-09-01,收入,6800.00,税后工资,工资,EXAMPLE-001,收支\n2026-09-02,支出,36.50,午餐,餐饮,EXAMPLE-002,收支\n2026-09-03,支出,500.00,转入自己的储蓄账户,其他,EXAMPLE-003,内部划转\n';
      const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='FinPilot流水示例.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  };
  return {pages:{import:importPage},actions,onChange,onInput:onChange,onSubmit};
}
