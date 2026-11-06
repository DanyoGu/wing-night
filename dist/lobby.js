(()=>{
// The shared lobby is the default. Use ?test=1 for the isolated UI preview.
if(new URLSearchParams(location.search).get('test')==='1'){
 const forecasts=[[8.42,8.74],[8.86,9.12],[9.03,8.65],[8.78,9.36],[9.14,8.92],[8.67,9.08],[9.21,8.46],[8.54,8.83],[8.96,9.17],[9.08,9.42]];
 const state={room:'TEST',phase:'started',activeRound:0,votingOpen:true,judgeCount:1,maxPoints:100,judges:[{id:'test-judge',name:'Test judge'}],me:{id:'test-judge',name:'Test judge',role:'judge'},ownVotes:{},rounds:[],totals:[0,0],projections:[88.69,89.75],chances:[47,53]};
 async function refresh(){
  state.rounds=forecasts.map((r,i)=>{const grades=state.ownVotes[i],points=grades?[grades[0]+grades[1],grades[2]+grades[3]]:[0,0];return {points,submitted:grades?1:0,submittedIds:grades?['test-judge']:[],projection:grades?points:[...r]}});
  state.totals=[0,1].map(p=>state.rounds.reduce((sum,r)=>sum+r.points[p],0));
  state.projections=[0,1].map(p=>state.rounds.reduce((sum,r)=>sum+Math.round(r.projection[p]*100),0)/100);
  const remaining=state.rounds.filter(r=>!r.submitted).length;
  const chance=remaining?Math.max(1,Math.min(99,Math.round(100/(1+Math.exp(-(state.projections[0]-state.projections[1])/(2.4*Math.sqrt(remaining))))))):state.totals[0]===state.totals[1]?50:state.totals[0]>state.totals[1]?100:0;
  state.chances=[chance,100-chance];window.dispatchEvent(new Event('wing-shared-state'));
  document.getElementById('judge-round-status').textContent='Testing mode · Tap any wing to grade it. Votes stay in this preview only.';
 }
 window.wingTestMode=true;window.sharedState=state;window.wingConnected=true;
 window.WingAPI={get state(){return state},online:true,refresh,async request(action,data){if(action!=='vote')throw Error('Unavailable in testing mode.');if(!Array.isArray(data.grades)||data.grades.length!==4||data.grades.some(v=>!Number.isInteger(v)||v<1||v>5))throw Error('Select all four grades.');state.ownVotes[data.round]=[...data.grades];return {ok:true}}};
 document.getElementById('lobby-view').hidden=true;document.getElementById('matchup-view').hidden=false;document.getElementById('back-to-lobby').hidden=true;
 const notice=document.getElementById('connection-status');notice.hidden=false;notice.textContent='TESTING MODE · Lobby skipped · Preview votes only';notice.style.background='#173b38';
 refresh();return;
}

const $=id=>document.getElementById(id);let state=null,online=false,refreshPromise=null;
let room=new URLSearchParams(location.search).get('room')||'';
window.wingLobby={get judgeCount(){return state?.judgeCount||0},get phase(){return state?.phase||'waiting'}};
async function request(action,data){const response=await fetch('/api/'+action,{method:'POST',signal:AbortSignal.timeout(10000),headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Start python3 server.py instead of the static preview server.');const result=await response.json();if(!response.ok)throw Error(result.error||'Request failed.');return result;}
async function refresh(){
 // A join/start must fetch again after any older poll has finished.
 if(refreshPromise){await refreshPromise;return refresh();}
 refreshPromise=refreshState().finally(()=>{refreshPromise=null;});
 return refreshPromise;
}
async function refreshState(){
 try{const response=await fetch('/api/state'+(room?'?room='+encodeURIComponent(room):''),{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Start python3 server.py instead of the static preview server.');const result=await response.json();if(!response.ok){if(response.status===404&&!room){state=null;online=true;render();return}throw Error(result.error||'Connection interrupted')}
 if(state&&state.room===result.room&&state.generation!==result.generation)window.dispatchEvent(new Event('wing-event-reset'));
 state=result;room=result.room;online=true;window.sharedState=state;render();window.dispatchEvent(new Event('wing-shared-state'));
 }catch(error){online=false;$('lobby-feedback').textContent=error.message.includes('JSON')?'Start the shared server to connect the lobby.':error.message;$('connection-status').textContent='Disconnected · retrying';$('connection-status').hidden=false;}finally{window.wingConnected=online;window.dispatchEvent(new Event('wing-connection'));}
}
function render(){
 const me=state?.me,n=state?.judgeCount||0,started=state&&state.phase!=='waiting';
 $('connection-status').hidden=online;
 $('event-reset-control').hidden=me?.role!=='host';$('reset-event').disabled=!online;
 if(!started||!me){$('lobby-view').hidden=false;$('matchup-view').hidden=true;}
 $('judge-count').textContent=n+' joined';$('lobby-daniel').textContent=(state?.projections?.[0]??88.69*n).toFixed(2);$('lobby-joicee').textContent=(state?.projections?.[1]??89.75*n).toFixed(2);$('lobby-max').textContent=(100*n).toFixed(2)+' points possible per player';
 $('room-code').textContent=room||'------';$('judge-roster').replaceChildren();
 if(state?.me)renderJudgeRoster($('judge-roster'),state);
 $('judge-empty').hidden=!!state?.judges?.length;$('judge-empty').textContent=me?'No judges yet. The first seat is open.':n?'Join to see the judging panel.':'No judges yet. The first seat is open.';
 $('join-form').hidden=!!me;$('create-room').hidden=!!room||!!me;
 $('start-challenge').hidden=me?.role!=='host'||!!started;$('start-challenge').disabled=!n||!online;
 $('start-challenge').textContent=n?'Start challenge · '+n+' judge'+(n===1?'':'s'):'Waiting for judges';
 $('lobby-status').textContent=started?'ROSTER LOCKED':'LOBBY OPEN';$('lobby-heading').textContent=started?'The heat is on.':'Before the first bite.';
 $('lobby-description').textContent=started?'The judging roster is locked. Your original browser keeps your place.':'Gather the judges. Settle in. The wings can wait.';
 $('roster-note').textContent=started?'New arrivals can watch. Withdrawing a judge removes all their grades.':'Everyone who joins is a judge, except the competitors. Remove duplicates before starting.';
 $('continue-match').hidden=!started||!me;
 $('join-label').textContent=started?'Join to watch · judging roster locked':'Join the judging panel';$('join-button').textContent=started?'Watch event':'Join lobby';
 $('room-entry').hidden=!!room;$('copy-invite').disabled=!room;
 if(!document.activeElement?.closest('#join-form'))$('lobby-feedback').textContent=me?me.role==='host'?'You’re hosting as Daniel. Share the invite to welcome judges.':me.role==='judge'?'You’re registered as '+me.name+'. Your place and saved votes are kept in this browser.':me.role==='withdrawn'?'You’ve been withdrawn. All your grades were removed; you can still watch.':me.role==='removed'?'You’re no longer on the judging roster. You can still watch.':'You’re watching as '+me.name+'. Spectators cannot vote.':started?'Judging has started. You can join as a spectator.':'';
 if(started&&me&&!window.wingViewingLobby)showMatch();
}
function renderJudgeRoster(container,current){
 container.replaceChildren();
 for(const judge of current.judges||[]){
  const li=document.createElement('li'),icon=document.createElement('span'),name=document.createElement('strong'),status=document.createElement('small');
  icon.className='judge-icon';icon.textContent=judge.name.slice(0,1).toUpperCase();name.textContent=judge.name;status.textContent=judge.id===current.me?.id?'You · Judge':'Judge';li.append(icon,name,status);
  if(current.me?.role==='host'&&current.phase!=='finished'){
   const button=document.createElement('button');button.type='button';button.className='roster-remove';const before=current.phase==='waiting';button.textContent=before?'Remove':'Withdraw';button.setAttribute('aria-label',(before?'Remove ':'Withdraw ')+judge.name);button.disabled=!online||(!before&&current.judgeCount<=1);
   if(!before&&current.judgeCount<=1)button.title='At least one judge must remain.';
   button.addEventListener('click',async()=>{
    const question=before?'Remove '+judge.name+' from the judging roster? They will keep spectator access.':'Withdraw '+judge.name+'? ALL of their grades from EVERY round will be removed. Both players’ totals, projections, and maximum points will change. They can continue watching but cannot vote again.';
    if(!window.confirm(question))return;
    button.disabled=true;
    try{await request(before?'remove-judge':'withdraw-judge',{room:current.room,member:judge.id,confirmed:true});await refresh();}
    catch(error){$('lobby-feedback').textContent=error.message;document.getElementById('host-feedback').textContent=error.message;button.disabled=false;}
   });li.append(button);
  }
  container.append(li);
 }
 const changes=current.rosterChanges||[];
 for(const id of ['lobby-roster-history','match-roster-history']){const history=document.getElementById(id);if(history)history.textContent=changes.map(change=>change.name+(change.action==='withdraw-judge'?' withdrawn · all grades removed':' removed before start')).join(' • ');}
}
window.renderJudgeRoster=renderJudgeRoster;
function showMatch(){window.wingViewingLobby=false;$('lobby-view').hidden=true;$('matchup-view').hidden=false;}
window.WingAPI={request,refresh,get state(){return state},get online(){return online}};
$('reset-event').addEventListener('click',async()=>{
 if(state?.me?.role!=='host'||!online)return;
 const generation=state.generation;
 if(window.prompt('Reset this event? This permanently clears ALL judges, votes, results, and roster changes. You keep your host access and room code. Everyone else must rejoin. Type RESET to confirm.')!=='RESET')return;
 $('reset-event').disabled=true;
 try{await request('reset',{room,generation,confirmed:true});window.wingViewingLobby=true;await refresh();$('reset-feedback').textContent='Event reset. Invite your judges to join again.';}
 catch(error){$('reset-feedback').textContent=error.message;}
 finally{$('reset-event').disabled=!online;}
});
$('create-room').addEventListener('click',async()=>{const button=$('create-room');button.disabled=true;try{const result=await request('create',{});room=result.room;history.replaceState(null,'','?lobby=1&room='+room);await refresh()}catch(e){$('lobby-feedback').textContent=e.message}finally{button.disabled=false}});
$('join-form').addEventListener('submit',async e=>{e.preventDefault();const button=$('join-button');button.disabled=true;try{const code=room||$('room-input').value.trim().toUpperCase();await request('join',{room:code,name:$('judge-name').value});room=code;history.replaceState(null,'','?lobby=1&room='+room);await refresh()}catch(e){$('lobby-feedback').textContent=e.message}finally{button.disabled=false}});
$('start-challenge').addEventListener('click',async()=>{const button=$('start-challenge');button.disabled=true;try{await request('start',{room});await refresh()}catch(e){$('lobby-feedback').textContent=e.message;button.disabled=false}});
$('continue-match').addEventListener('click',()=>{showMatch();window.scrollTo(0,0)});
$('back-to-lobby').addEventListener('click',()=>{window.wingViewingLobby=true;$('matchup-view').hidden=true;$('lobby-view').hidden=false;render();window.scrollTo(0,0)});
$('copy-invite').addEventListener('click',async()=>{const url=new URL(location.href);url.search='?lobby=1&room='+room;url.hash='';try{await navigator.clipboard.writeText(url.href);$('lobby-feedback').textContent='Invite link copied.'}catch{$('invite-fallback').hidden=false;$('invite-fallback').value=url.href;$('invite-fallback').select();$('lobby-feedback').textContent='Copy the invite link below.'}});
async function poll(){try{await refresh();}finally{setTimeout(poll,2000)}}
window.addEventListener('focus',()=>{refresh()});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
render();poll();
})();
