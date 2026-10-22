/* Local video dashboard. Progress updates never replace an existing player. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory;
  else root.MuFlowVideoPreview=factory(root.document,root);
})(typeof window==='undefined'?this:window,function(doc,win){
  'use strict';
  const cards=new Map(), container=doc.getElementById('videos');
  const labels={not_started:'未开始',ready:'待提交',submitting:'提交中',running:'生成中',poll_result:'核对原任务',download:'生成成功，待下载',downloaded:'文件就绪 · 待用户审片',retry:'待重试',uncertain:'结果不明，待核对原任务',blocked:'阻塞，查看本机任务记录',file_unavailable:'文件待恢复'};
  const text=(tag,value,cls)=>{const n=doc.createElement(tag);n.textContent=value||'';if(cls)n.className=cls;return n;};
  const clock=doc.getElementById('stamp'), problem=doc.getElementById('update-error');
  let active=null,lastDigest=null,timer=null,loading=false;
  function applySource(card,row){
    card.row=row;card.pending=null;card.notice.textContent='';card.switcher.hidden=true;
    card.open.href=row.href;card.open.hidden=!row.file_available;
    card.filename.textContent=row.filename||'';card.play.disabled=!row.file_available;
    card.play.hidden=!row.file_available;
    // Assign src only after the user presses play. No metadata storm on page load.
  }
  function play(card){
    if(!card.row.file_available)return;
    if(active&&active!==card)active.video.pause();active=card;
    if(card.loadedVersion!==card.row.version){
      card.video.src=card.row.src;card.loadedVersion=card.row.version;card.video.load();
    }
    card.video.hidden=false;card.error.textContent='';
    const promise=card.video.play();
    if(promise&&promise.catch)promise.catch(()=>{card.error.textContent='当前预览器未能播放。请点“打开视频文件”或上方“本地浏览器播放”；无需重新生成。';});
  }
  function makeCard(row){
    const node=text('section','','card');node.dataset.groupId=row.id;
    const heading=text('h2',(row.episode==null?'集数待核':'第'+row.episode+'集')+' · '+row.id);
    const state=text('p','','muted'),video=doc.createElement('video');
    video.controls=true;video.preload='none';video.playsInline=true;video.hidden=true;
    const actions=text('div','','actions'),button=text('button','播放 / 继续');button.type='button';
    const switcher=text('button','切换已就绪的新版本');switcher.type='button';switcher.hidden=true;
    const open=text('a','打开视频文件');open.target='_blank';open.rel='noopener';
    const filename=text('p','','muted'),notice=text('p','','notice'),error=text('p','','error');
    actions.append(button,switcher,open);node.append(heading,state,video,actions,filename,notice,error);
    const card={node,heading,state,video,play:button,switcher,open,filename,notice,error,row,loadedVersion:null,pending:null};
    button.addEventListener('click',()=>play(card));
    switcher.addEventListener('click',()=>{const next=card.pending;if(!next)return;video.pause();video.removeAttribute('src');video.load();card.loadedVersion=null;applySource(card,next);play(card);});
    video.addEventListener('error',()=>{const codes={1:'播放被中止',2:'媒体读取失败',3:'当前播放器解码失败',4:'当前播放器不支持该媒体来源或编码'};card.error.textContent=(codes[video.error&&video.error.code]||'未能加载视频')+'。尝试打开视频文件或使用本地浏览器入口；保持原任务，不自动重生成。';});
    applySource(card,row);return card;
  }
  function receive(data){
    if(!data||!Array.isArray(data.items))throw Error('Invalid progress snapshot');
    problem.textContent='';
    if(lastDigest===data.data_digest&&data.data_digest)return;
    lastDigest=data.data_digest;
    doc.getElementById('summary').textContent='已出成片 '+data.downloaded+' / '+data.total+' 组 · 当前项目已登记 '+data.registered_groups+' 组';
    clock.textContent='实际记录更新时间：'+(data.updated_at||'待登记')+' · 进度更新不重新加载视频';
    doc.getElementById('empty').hidden=data.items.length>0;
    const link=doc.getElementById('browser-link');
    if(data.service_url){link.href=data.service_url;link.hidden=false;}
    let previous=null;
    for(const row of data.items){
      let card=cards.get(row.id);
      if(!card){card=makeCard(row);cards.set(row.id,card);}
      else if(card.row.version!==row.version&&card.loadedVersion){
        // Even a paused video keeps its timeline. The viewer chooses when to switch.
        card.pending=row;card.notice.textContent='此组有更新。当前播放器保持原版本，点击切换才加载新版本。';card.switcher.hidden=false;
      }else if(card.row.version!==row.version){applySource(card,row);}
      else{card.row=row;}
      card.node.hidden=!row.file_available&&!doc.getElementById('show-pending').checked;
      card.state.textContent=(labels[row.phase]||'状态待核')+(row.bytes?' · '+(row.bytes/1048576).toFixed(1)+' MB':'');
      if(row.warning)card.notice.textContent=row.warning;
      const desired=previous?previous.nextSibling:container.firstChild;
      if(desired!==card.node)container.insertBefore(card.node,desired);
      previous=card.node;
    }
    for(const [id,card]of cards){if(!data.items.some(r=>r.id===id))card.notice.textContent='本次清单暂未包含此组；已打开的播放器保留，需核对记录。';}
  }
  function update(){
    if(loading)return;loading=true;
    const script=doc.createElement('script');
    script.src='成片进度.js?progress='+Date.now();script.async=true;
    const done=()=>{loading=false;script.remove();};
    script.onload=done;script.onerror=()=>{problem.textContent='未能读取最新进度，已加载的视频保持不变。当前内置预览器若限制本地文件，请使用本地浏览器入口。';done();};
    doc.head.append(script);
  }
  function schedule(){
    if(timer)win.clearInterval(timer);timer=null;
    if(doc.getElementById('auto').checked)timer=win.setInterval(update,5000);
  }
  doc.getElementById('show-pending').addEventListener('change',()=>{for(const card of cards.values())card.node.hidden=!card.row.file_available&&!doc.getElementById('show-pending').checked;});
  doc.getElementById('refresh').addEventListener('click',update);
  doc.getElementById('auto').addEventListener('change',schedule);schedule();
  return {receive,update};
});
