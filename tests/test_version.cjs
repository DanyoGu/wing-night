const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dist/version.js'),'utf8');
let release={version:'v0.152',revision:'a'.repeat(40),commit_count:152},failure=false;
const label={dataset:{revision:'a'.repeat(40),version:'v0.152'}},events=new Map();
vm.runInNewContext(source,{
 document:{getElementById:()=>label},AbortSignal,
 window:{addEventListener:(name,fn)=>events.set(name,fn)},
 fetch:async(url,options)=>{assert.equal(url,'/api/version');assert.equal(options.cache,'no-store');if(failure)throw Error('offline');return {ok:true,json:async()=>release};}
});
(async()=>{
 await new Promise(setImmediate);assert.equal(label.textContent,'v0.152');assert(label.title.includes('a'.repeat(40)));
 release={version:'v0.153',revision:'b'.repeat(40),commit_count:153};await events.get('focus')();
 assert(label.textContent.includes('Reload'),'an old page must not claim to be the newly deployed UI');
 failure=true;await events.get('focus')();assert.equal(label.textContent,'Build version unavailable');
 console.log('PASS: actual build version, no-store metadata, stale-page reload notice and visible metadata failure.');
})().catch(error=>{console.error(error);process.exitCode=1});
