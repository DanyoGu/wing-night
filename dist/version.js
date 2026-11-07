(()=>{
 const label=document.getElementById('build-version');
 const loadedRevision=label.dataset.revision;
 label.textContent=label.dataset.version;
 async function refresh(){
  try{
   const response=await fetch('/api/version',{cache:'no-store',signal:AbortSignal.timeout(8000)});
   if(!response.ok)throw Error('Build metadata unavailable');
   const release=await response.json();
   if(!/^v0\.\d+$/.test(release.version)||!Number.isInteger(release.commit_count)||release.version!=='v0.'+release.commit_count||!(release.revision==='development'||/^[0-9a-f]{40}$/.test(release.revision)))throw Error('Invalid build metadata');
   if(loadedRevision&&loadedRevision!==release.revision){
    label.textContent='Update available: '+release.version+' · Reload to use this build';
    label.title='Current server revision: '+release.revision;
    return;
   }
   label.textContent=release.version;
   label.title='Deployed revision: '+release.revision;
  }catch(error){label.textContent='Build version unavailable';label.title=error.message;}
 }
 refresh();
 window.addEventListener('focus',refresh);
})();
