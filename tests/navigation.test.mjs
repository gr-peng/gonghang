import assert from 'node:assert/strict';
import {createNavigation,parseRoute,routes,primaryTabs} from '../AI_accounting_agent/frontend/liquid-glass/navigation.js';

function browser(start='#/home') {
  const listeners=new Map(),entries=[{hash:'#external',state:null},{hash:start,state:null}];
  let cursor=1;
  const win={location:{hash:start},scrollY:0,
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},
    removeEventListener(name,fn){listeners.get(name)?.delete(fn);},
    emit(name){for(const fn of listeners.get(name)||[])fn();},
  };
  win.history={
    get state(){return entries[cursor].state;},get length(){return entries.length;},
    replaceState(state,_,href){entries[cursor]={state:structuredClone(state),hash:href};win.location.hash=href;},
    pushState(state,_,href){entries.splice(cursor+1);entries.push({state:structuredClone(state),hash:href});cursor++;win.location.hash=href;},
    go(delta){const next=cursor+delta;if(next<0||next>=entries.length)return;cursor=next;win.location.hash=entries[cursor].hash;win.emit('popstate');win.emit('hashchange');},
    back(){this.go(-1);},forward(){this.go(1);},
  };
  win.hash=(hash)=>{entries.splice(cursor+1);entries.push({hash,state:null});cursor++;win.location.hash=hash;win.emit('popstate');win.emit('hashchange');};
  return win;
}
function setup(start) {
  const win=browser(start),changes=[],left=[];
  const nav=createNavigation({window:win,onChange:change=>{changes.push(change);win.scrollY=change.scrollY;nav.restored();},beforeLeave:()=>left.push(win.location.hash)});
  return {win,nav,changes,left};
}

assert.equal(Object.keys(routes).length,19);
assert.equal(primaryTabs.length,5);
assert.equal(parseRoute('#/unknown?code=123').href,'#/home');
assert.equal(parseRoute('stock?code=600036&name=%E6%8B%9B%E5%95%86').query,'code=600036&name=%E6%8B%9B%E5%95%86');
for(const [route,info] of Object.entries(routes)) {
  const {win,nav}=setup('#/'+route);
  assert.equal(nav.activeTab,info.tab||'home',route);
  if(route==='home')assert.equal(nav.backTarget,null);
  else {nav.back();assert.equal(win.location.hash,'#/'+info.parent,route);assert.notEqual(win.location.hash,'#external');}
}
console.log('PASS: all 19 routes have consistent owners and safe direct-link return paths.');

{
  const {nav,win,changes}=setup();
  nav.go('visualization',{root:true});win.scrollY=530;win.emit('scroll');
  nav.go('ledger');assert.equal(nav.activeTab,'ledger');assert.equal(nav.backTarget,'#/visualization');
  nav.go('settings');assert.equal(nav.activeTab,'ledger');assert.equal(nav.backTarget,'#/ledger');
  nav.back();assert.equal(win.location.hash,'#/ledger');
  nav.back();assert.equal(win.location.hash,'#/visualization');assert.equal(changes.at(-1).scrollY,530);
  const count=changes.length;win.history.forward();assert.equal(win.location.hash,'#/ledger');assert.equal(changes.length,count+1,'popstate/hashchange must not paint twice');
  win.history.forward();assert.equal(win.location.hash,'#/settings');assert.equal(nav.backTarget,'#/ledger');
}
console.log('PASS: chart-to-ledger, settings origin, native back/forward and scroll restoration.');

{
  const {nav,win}=setup();
  nav.go('investment',{root:true});nav.go('research');
  nav.go('stock?code=600036&name=招商银行');nav.go('assistant');
  assert.equal(nav.activeTab,'assistant');nav.back();assert.equal(win.location.hash,'#/stock?code=600036&name=招商银行');
  nav.go('settings');nav.go('improvement');assert.equal(nav.activeTab,'investment');
  nav.back();assert.equal(win.location.hash,'#/settings');nav.back();assert.match(win.location.hash,/stock\?code=600036/);
  nav.back();assert.equal(win.location.hash,'#/research');nav.back();assert.equal(win.location.hash,'#/investment');
}
console.log('PASS: assistant restores its source, query parameters survive, nested settings preserve tab ownership.');

{
  const {nav,win}=setup();
  nav.go('visualization');nav.go('insights');nav.go('ledger');nav.go('visualization');
  assert.equal(nav.backTarget,'#/home');nav.back();assert.equal(win.location.hash,'#/home');
  win.history.forward();assert.equal(win.location.hash,'#/visualization');
  nav.go('investment',{root:true});assert.equal(nav.backTarget,'#/home');
  nav.back();assert.equal(win.location.hash,'#/home');
  win.history.back();assert.equal(win.location.hash,'#/visualization');
}
console.log('PASS: ancestor cross-links do not create return loops; primary tabs have stable home fallbacks.');

{
  const {nav,win}=setup();
  nav.go('ledger');nav.go('import');
  nav.destroy();const resumed=createNavigation({window:win});
  assert.equal(resumed.backTarget,'#/ledger');resumed.back();assert.equal(win.location.hash,'#/ledger');
  win.hash('#/entry');assert.equal(resumed.backTarget,'#/ledger');resumed.back();assert.equal(win.location.hash,'#/ledger');
}
console.log('PASS: reload preserves navigation history; hash links join the same return stack.');
