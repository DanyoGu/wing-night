const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dist/lobby.js'),'utf8');
const shared={judges:[]};
function browser(host=false,delayFirst=false){
 const elements=new Map(),timers=[],events=new Map();let member=host?{id:'host',name:'Daniel',role:'host'}:null,release,calls=0;
 const element=()=>({value:'',hidden:false,disabled:false,textContent:'',children:[],listeners:{},addEventListener(event,fn){this.listeners[event]=fn},setAttribute(){},replaceChildren(){this.children=[]},append(...items){this.children.push(...items)},select(){}});
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const document={hidden:false,activeElement:null,getElementById:get,createElement:element,addEventListener(){}};
 const state=()=>({room:'ROOM01',phase:'waiting',judgeCount:shared.judges.length,judges:member?shared.judges:undefined,me:member,rosterChanges:[]});
 const response=data=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>data});
 const context={document,URL,URLSearchParams,Event,AbortSignal,location:{search:'?room=ROOM01',href:'http://localhost:8040/?room=ROOM01'},history:{replaceState(){}},navigator:{},setTimeout(fn){timers.push(fn)},window:{scrollTo(){},addEventListener(event,fn){events.set(event,fn)},dispatchEvent(){},confirm(){return true}},fetch:async(url,options={})=>{
  if(options.method==='POST'){
   const data=JSON.parse(options.body);assert.equal(data.room,'ROOM01');member={id:'judge',name:data.name,role:'judge'};shared.judges.push({id:member.id,name:member.name});return response({room:'ROOM01'});
  }
  const snapshot=structuredClone(state());calls++;
  if(delayFirst&&calls===1)await new Promise(resolve=>{release=resolve});
  return response(snapshot);
 }};
 vm.runInNewContext(source,context);
 return {get,context,timers,release:()=>release(),get calls(){return calls}};
}
(async()=>{
 const host=browser(true);await new Promise(setImmediate);assert.equal(host.get('judge-count').textContent,'0 joined');
 const judge=browser(false,true);judge.get('judge-name').value='Alex';
 const join=judge.get('join-form').listeners.submit({preventDefault(){}});
 await new Promise(setImmediate);judge.release();await join;
 assert.equal(judge.get('judge-count').textContent,'1 joined');assert.equal(judge.get('judge-roster').children.length,1);assert.equal(judge.get('join-form').hidden,true);
 assert(judge.calls>=2,'join must refresh after the stale in-flight poll');
 await host.timers.shift()();
 assert.equal(host.get('judge-count').textContent,'1 joined');assert.equal(host.get('judge-roster').children.length,1);assert.equal(host.get('judge-roster').children[0].children[1].textContent,'Alex');assert.equal(host.get('start-challenge').disabled,false);
 assert.equal(host.get('lobby-daniel').textContent,'88.69');assert.equal(host.get('lobby-joicee').textContent,'89.75');
 console.log('PASS: a joining judge appears in both panels; stale polls cannot hide the join; host poll updates roster, projections, and start control.');
})().catch(error=>{console.error(error);process.exitCode=1});
