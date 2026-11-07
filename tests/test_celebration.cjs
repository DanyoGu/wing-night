const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(reduced=false){
 const elements=new Map(),events=new Map(),stored=new Map();let shown=0,frames=0,cancelled=0,painted=0;
 function element(){const classes=new Set();return {getContext(){return {createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(image){painted++;assert(image.data.some(value=>value>0));}}},textContent:'',hidden:false,open:false,children:[],style:{setProperty(){}},classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),toggle(k,on){on?classes.add(k):classes.delete(k)}},events:{},append(child){this.children.push(child)},replaceChildren(){this.children=[]},addEventListener(name,callback){this.events[name]=callback},showModal(){this.open=true;shown++},close(){this.open=false;this.events.close?.()}}}
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
 const context={document:{getElementById:get,createElement:element},window:{requestAnimationFrame(){return ++frames},cancelAnimationFrame(){cancelled++},matchMedia:()=>({matches:reduced}),addEventListener:(e,f)=>events.set(e,f)},sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../dist/celebration.js'),'utf8'),context);
 return {get,context,events,get shown(){return shown},get frames(){return frames},get cancelled(){return cancelled},get painted(){return painted}};
}
const app=setup(),api=app.context.window.WingCelebration;
assert.equal(api.outcome({totals:[5,6]}).name,'Joicee');assert.equal(api.outcome({totals:[7,6]}).name,'Daniel');assert(api.outcome({totals:[6,6]}).tie);assert.equal(api.outcome({totals:[NaN,4]}),null);
app.context.window.sharedState={room:'ROOM',generation:0,phase:'started',totals:[90,80]};app.events.get('wing-shared-state')();assert.equal(app.shown,0);
app.context.window.sharedState.phase='finished';app.events.get('wing-shared-state')();assert.equal(app.shown,1);assert.equal(app.get('fire-title').textContent,'Daniel');assert.equal(app.get('fire-daniel-score').textContent,'90.00');assert.equal(app.get('fire-embers').children.length,44);assert.equal(app.frames,1);assert.equal(app.painted,1);
app.get('fire-return').events.click();assert(!app.get('victory-dialog').open);assert.equal(app.get('fire-embers').children.length,0);assert.equal(app.cancelled,1);
app.events.get('wing-shared-state')();assert.equal(app.shown,1);
app.get('replay-celebration').events.click();assert.equal(app.shown,2);
app.events.get('wing-event-reset')();assert(!app.get('victory-dialog').open);
app.context.window.sharedState.generation=1;app.context.window.sharedState.totals=[80,80];app.events.get('wing-shared-state')();assert.equal(app.shown,3);assert.equal(app.get('fire-title').textContent,'It’s a tie');assert.equal(app.get('fire-portraits').children.length,2);
const quiet=setup(true);quiet.context.window.WingCelebration.show({totals:[4,5]});assert.equal(quiet.get('fire-embers').children.length,0);assert.equal(quiet.frames,0);assert.equal(quiet.painted,1);
console.log('PASS: winner/tie output, final-only trigger, one-time playback, replay, reset, scores, and reduced motion.');
