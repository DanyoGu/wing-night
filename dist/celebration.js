(()=>{
const $=id=>document.getElementById(id),seen=new Set();let emberTimer,fireFrame=0;
function outcome(state){
 if(!state||!Array.isArray(state.totals)||state.totals.length!==2||!state.totals.every(Number.isFinite))return null;
 const [daniel,joicee]=state.totals,tie=daniel===joicee;
 return {tie,name:tie?'It’s a tie':daniel>joicee?'Daniel':'Joicee',winner:tie?null:daniel>joicee?0:1,margin:Math.abs(daniel-joicee),totals:[daniel,joicee]};
}
// A turbulent temperature field: layered noise advects upward, with hotter
// yellow cores and translucent red edges. Small render size keeps mobile cost low.
function startFire(still=false){
 const canvas=$('fire-canvas'),ctx=canvas.getContext?.('2d',{alpha:true});if(!ctx)return;
 const w=180,h=110;canvas.width=w;canvas.height=h;
 const frame=ctx.createImageData(w,h),noise=new Float32Array(4096);
 for(let i=0;i<noise.length;i++)noise[i]=Math.random();
 function sample(x,y){const ix=Math.floor(x),iy=Math.floor(y);let fx=x-ix,fy=y-iy;fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy);const a=noise[((iy&63)*64)+(ix&63)],b=noise[((iy&63)*64)+((ix+1)&63)],c=noise[(((iy+1)&63)*64)+(ix&63)],d=noise[(((iy+1)&63)*64)+((ix+1)&63)];return (a+(b-a)*fx)*(1-fy)+(c+(d-c)*fx)*fy;}
 function turbulence(x,y){return sample(x,y)*.57+sample(x*2.04+11,y*2.04+7)*.29+sample(x*4.1+23,y*4.1+13)*.14;}
 let previous=-Infinity,elapsed=0;
 function draw(now){
  if(!still&&!$('victory-dialog').open)return;
  if(now-previous>=40){
   if(!document.hidden){elapsed+=previous===-Infinity?0:Math.min(now-previous,80)/1000;
    const t=elapsed;
    for(let y=0;y<h;y++){
     const rise=1-y/(h-1),flow=rise*3.4-t*1.8;
     for(let x=0;x<w;x++){
      const nx=x/(w-1),sway=sample(nx*3+2,flow*.6)*.8;
      const n=turbulence(nx*6+sway,flow);
      const fuel=.08*Math.sin(nx*19+t*.8)+.06*Math.sin(nx*37-t*.5);
      const heat=Math.max(0,Math.min(1,1-rise*1.55+(n-.5)*1.45+fuel));
      const edge=Math.max(0,Math.min(1,(heat-.08)*6)),i=(y*w+x)*4;
      frame.data[i]=Math.min(255,heat*650);
      frame.data[i+1]=Math.max(0,Math.min(255,Math.pow(heat,1.8)*330-15));
      frame.data[i+2]=Math.max(0,Math.min(210,Math.pow(heat,4)*190-12));
      frame.data[i+3]=Math.round(edge*245);
     }
    }
    ctx.putImageData(frame,0,0);
   }
   previous=now;
  }
  if(!still)fireFrame=window.requestAnimationFrame(draw);
 }
 draw(0);
}
function stop(){if(fireFrame)window.cancelAnimationFrame?.(fireFrame);fireFrame=0;clearTimeout(emberTimer);$('fire-embers').replaceChildren();$('fire-card').classList.remove('playing');}
function show(state,preview=false){
 const result=outcome(state);if(!result)return;
 const dialog=$('victory-dialog'),reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 stop();
 $('fire-title').textContent=result.name;$('fire-subtitle').textContent=result.tie?'SHARED GLORY':'WING NIGHT CHAMPION';
 $('fire-kicker').textContent=preview?'A TASTE OF THE FINAL REVEAL':'TEN WINGS. THE HEAT HAS SPOKEN.';
 $('fire-margin').textContent=result.tie?'Same heat. Same score. Bragging rights shared.':result.name+' takes the crown by '+result.margin.toFixed(2)+' points.';
 const portraits=$('fire-portraits');portraits.replaceChildren();portraits.classList.toggle('tie',result.tie);
 for(const name of result.tie?['Daniel','Joicee']:[result.name]){const img=document.createElement('img');img.src='assets/'+name.toLowerCase()+'.png';img.alt=name;portraits.append(img);}
 ['daniel','joicee'].forEach((name,p)=>{$('fire-'+name+'-score').textContent=result.totals[p].toFixed(2);$('fire-'+name+'-result').classList.toggle('fire-leader',result.tie||result.winner===p);});
 $('fire-preview-note').hidden=!preview;
 const voting=document.getElementById('details');if(voting.open)voting.close();
 if(!dialog.open)dialog.showModal();
 startFire(reduced);
 if(!reduced){void $('fire-card').offsetWidth;$('fire-card').classList.add('playing');for(let i=0;i<44;i++){const ember=document.createElement('i');ember.className='fire-ember';for(const [key,value] of Object.entries({'--x':Math.random()*100+'%','--size':2+Math.random()*4+'px','--duration':2.5+Math.random()*2+'s','--delay':Math.random()*1.5+'s','--drift':Math.random()*120-60+'px'}))ember.style.setProperty(key,value);$('fire-embers').append(ember);}emberTimer=setTimeout(()=>{$('fire-embers').replaceChildren();},6500);}
}
function key(state){return 'wing-finale:'+state.room+':'+(state.generation||0);}
function sync(){
 const state=window.sharedState,finished=state?.phase==='finished';
 $('replay-celebration').hidden=!finished;
 $('preview-celebration').hidden=!window.wingTestMode;
 if(!finished)return;
 const id=key(state);let recorded=false;try{recorded=sessionStorage.getItem(id)==='seen';}catch{}
 if(seen.has(id)||recorded)return;
 seen.add(id);try{sessionStorage.setItem(id,'seen');}catch{}
 show(state);
}
$('fire-close').addEventListener('click',()=>$('victory-dialog').close());
$('fire-return').addEventListener('click',()=>$('victory-dialog').close());
$('victory-dialog').addEventListener('close',stop);
$('replay-celebration').addEventListener('click',()=>show(window.sharedState));
$('preview-celebration').addEventListener('click',()=>show({totals:[88.69,89.75]},true));
window.addEventListener('wing-shared-state',sync);
window.addEventListener('wing-event-reset',()=>{if($('victory-dialog').open)$('victory-dialog').close();stop();$('replay-celebration').hidden=true;});
window.WingCelebration={outcome,show,sync};sync();
})();
