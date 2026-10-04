// Reject new numerical claims in data-grounded analysis. This is not a guarantee
// of semantic correctness; displayed statistics remain the source of truth.
export function adviceMatchesContext(reply, context) {
  if (/预算执行率|预算完成率|预算剩余|剩余预算|(?:当前|你的|您的)预算(?:为|是|额度|总额)/.test(reply)) return false;
  const allowed=new Set();
  const add=value=>{
    const number=Number(value);
    if(Number.isFinite(number)){
      allowed.add(String(number));
      allowed.add(String(Number(number.toFixed(2))));
      allowed.add(String(Number(number.toFixed(1))));
    }
  };
  function visit(value,key='') {
    if(typeof value==='number'){
      add(value);
      if(/rate|share|pct/.test(key))add(value*100);
    }else if(typeof value==='string'){
      for(const match of value.matchAll(/\d+(?:\.\d+)?/g))add(match[0]);
    }else if(Array.isArray(value))value.forEach(item=>visit(item,key));
    else if(value&&typeof value==='object')Object.entries(value).forEach(([k,v])=>visit(v,k));
  }
  visit(context);
  const text=reply.replace(/(?<=\d),(?=\d{3})/g,'').replace(/^\s*\d+[.)、]\s*/gm,'');
  return [...text.matchAll(/\d+(?:\.\d+)?/g)].every(match=>allowed.has(String(Number(match[0]))));
}
