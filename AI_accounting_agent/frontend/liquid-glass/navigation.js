// One route map owns titles, primary-tab membership and direct-link fallbacks.
export const routes = Object.freeze({
  home:{title:'首页',tab:'home',parent:null},
  ledger:{title:'账本',tab:'ledger',parent:'home'},
  visualization:{title:'图表',tab:'visualization',parent:'home'},
  assistant:{title:'助手',tab:'assistant',parent:'home'},
  investment:{title:'投资',tab:'investment',parent:'home'},
  entry:{title:'记一笔',tab:'ledger',parent:'ledger'},
  insights:{title:'财务分析',tab:'visualization',parent:'visualization'},
  import:{title:'导入流水',tab:'ledger',parent:'ledger'},
  holdings:{title:'模拟持仓',tab:'investment',parent:'investment'},
  profile:{title:'我的计划',tab:'investment',parent:'investment'},
  compare:{title:'方案比较',tab:'investment',parent:'investment'},
  research:{title:'投资研究',tab:'investment',parent:'investment'},
  dashboard:{title:'投资可视化',tab:'investment',parent:'research'},
  risk:{title:'风险观察',tab:'investment',parent:'research'},
  stock:{title:'个股研究',tab:'investment',parent:'research'},
  bank:{title:'体验账户',tab:'home',parent:'home'},
  security:{title:'操作保护',tab:'home',parent:'bank'},
  settings:{title:'设置',tab:null,parent:'home'},
  improvement:{title:'一起改进',tab:null,parent:'settings'},
});
export const primaryTabs = Object.freeze(['home','ledger','visualization','assistant','investment']);

export function parseRoute(value='home') {
  const raw=String(value).replace(/^#\/?/,'').replace(/^\//,'');
  const split=raw.indexOf('?'), name=split<0?raw:raw.slice(0,split);
  const route=Object.hasOwn(routes,name)?name:'home';
  const query=route===name&&split>=0?raw.slice(split+1):'';
  return {route,query,href:`#/${route}${query?'?'+query:''}`};
}

const KEY='qingcaiNavigation';
const VERSION=1;
const cleanEntry=(value)=>value&&typeof value.id==='string'&&Number.isInteger(value.index)&&value.index>=0&&parseRoute(value.href).href===value.href;
const validState=value=>cleanEntry(value)&&value.version===VERSION&&Array.isArray(value.trail)&&value.trail.length<=32&&value.trail.every(cleanEntry);

export function createNavigation({window:win=window,onChange=()=>{},beforeLeave=()=>{}}={}) {
  const {history,location}=win;
  const id=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`;
  const point=entry=>({id:entry.id,index:entry.index,href:entry.href});
  const read=()=>history.state?.[KEY];
  const write=(entry,replace)=>history[replace?'replaceState':'pushState']({...history.state,[KEY]:entry},'',entry.href);
  const initial=parseRoute(location.hash);
  let current=validState(read())&&read().href===initial.href?read():{version:VERSION,id:id(),index:0,href:initial.href,trail:[],scrollY:0};
  let moving=false, restoring=false,scrollTimer;
  write(current,true);
  history.scrollRestoration='manual';

  function saveScroll() {
    if(restoring||parseRoute(location.hash).href!==current.href||read()?.id!==current.id)return;
    current={...current,scrollY:Math.max(0,Number(win.scrollY)||0)};
    write(current,true);
  }
  function notify(reason) {moving=false;restoring=true;onChange({...parseRoute(current.href),scrollY:current.scrollY||0,reason});}
  function transition(target,{root=false,replace=false}={}) {
    const parsed=parseRoute(target);
    if(parsed.href===current.href){beforeLeave();if(root&&current.trail.length){current={...current,trail:[]};write(current,true);}onChange({...parsed,scrollY:win.scrollY||0,reason:'refresh'});return;}
    beforeLeave();saveScroll();
    let trail=root?[]:[...current.trail,point(current)];
    // Cross-links to an ancestor shorten the return path instead of creating a loop.
    const ancestor=trail.findIndex(entry=>entry.href===parsed.href);
    if(ancestor>=0)trail=trail.slice(0,ancestor);
    current={version:VERSION,id:id(),index:current.index+(replace?0:1),href:parsed.href,trail:trail.slice(-32),scrollY:0};
    write(current,replace);notify('navigate');
  }
  function target() {
    const route=parseRoute(current.href).route;
    if(route==='home')return null;
    return current.trail.at(-1)?.href||`#/${routes[route].parent||'home'}`;
  }
  function back() {
    if(moving)return;
    const href=target();if(!href)return;
    const previous=current.trail.at(-1);
    if(previous&&previous.index<current.index&&history.length>current.index-previous.index) {
      beforeLeave();saveScroll();moving=true;history.go(previous.index-current.index);
    } else transition(href,{root:true,replace:true});
  }
  function receive() {
    const parsed=parseRoute(location.hash), existing=read();
    if(existing?.id===current.id&&parsed.href===current.href)return;
    beforeLeave();
    if(validState(existing)&&existing.href===parsed.href)current=existing;
    else {
      let trail=[...current.trail,point(current)];
      const ancestor=trail.findIndex(entry=>entry.href===parsed.href);
      if(ancestor>=0)trail=trail.slice(0,ancestor);
      current={version:VERSION,id:id(),index:current.index+1,href:parsed.href,trail:trail.slice(-32),scrollY:0};
      write(current,true);
    }
    notify('history');
  }
  function recordScroll() {
    // Avoid history.replaceState browser rate limits during long touch scrolls.
    if(scrollTimer||restoring)return;
    scrollTimer=setTimeout(()=>{scrollTimer=null;saveScroll();},500);
  }
  win.addEventListener('popstate',receive);
  win.addEventListener('hashchange',receive);
  win.addEventListener('scroll',recordScroll,{passive:true});
  return {
    go:transition,back,
    get current(){return {...parseRoute(current.href),scrollY:current.scrollY||0};},
    get backTarget(){return target();},
    get activeTab(){
      const route=parseRoute(current.href).route;
      return routes[route].tab||[...current.trail].reverse().map(item=>routes[parseRoute(item.href).route].tab).find(Boolean)||'home';
    },
    restored(){restoring=false;saveScroll();},
    destroy(){clearTimeout(scrollTimer);win.removeEventListener('popstate',receive);win.removeEventListener('hashchange',receive);win.removeEventListener('scroll',recordScroll);},
  };
}
