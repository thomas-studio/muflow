const assert=require('node:assert/strict');
const create=require('../assets/video-preview.js');
class Node{
  constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.checked=true;this.textContent='';this.paused=true;this.currentTime=0;this.loadCalls=0;this.sourceWrites=0;}
  get firstChild(){return this.children[0]||null;}
  get nextSibling(){if(!this.parent)return null;return this.parent.children[this.parent.children.indexOf(this)+1]||null;}
  set src(value){this._src=value;this.sourceWrites++;}get src(){return this._src;}
  append(...nodes){for(const n of nodes)this.insertBefore(n,null);}
  insertBefore(n,ref){if(n.parent){const at=n.parent.children.indexOf(n);n.parent.children.splice(at,1);}const at=ref?this.children.indexOf(ref):-1;this.children.splice(at<0?this.children.length:at,0,n);n.parent=this;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  fire(name){const f=this.listeners[name]||this['on'+name];return f&&f();}
  pause(){this.paused=true;}
  play(){this.paused=false;return Promise.resolve();}
  load(){this.loadCalls++;this.currentTime=0;}
  removeAttribute(name){if(name==='src'){delete this._src;this.sourceWrites++;}}
  remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const ids={};for(const id of ['videos','stamp','update-error','summary','empty','browser-link','refresh','auto','show-pending'])ids[id]=new Node('div');
ids['show-pending'].checked=false;
const doc={getElementById:id=>ids[id],createElement:tag=>new Node(tag),head:new Node('head')};
let timerFn;const win={setInterval:fn=>(timerFn=fn,1),clearInterval:()=>{}};const view=create(doc,win);
const row=(id,version='v1',phase='downloaded')=>({id,episode:1,version,phase,file_available:phase==='downloaded',src:id+'-'+version+'.mp4',href:id+'-'+version+'.mp4',filename:id+'.mp4',bytes:100});
const data=(items,n)=>({items,total:items.length,registered_groups:items.length,downloaded:items.filter(r=>r.phase==='downloaded').length,data_digest:String(n),updated_at:'now'});
view.receive(data([row('G01')],1));const card=ids.videos.children[0],video=card.children[2],actions=card.children[3];
assert.equal(video.hidden,true);assert.equal(video.src,undefined,'No eager media source');assert.equal(video.preload,'none');
actions.children[0].fire('click');assert.equal(video.hidden,false);video.currentTime=12.5;assert.equal(video.paused,false);const loads=video.loadCalls,writes=video.sourceWrites;
view.receive(data([row('G01'),row('G02')],2));assert.strictEqual(ids.videos.children[0],card);assert.strictEqual(card.children[2],video);assert.equal(video.currentTime,12.5);assert.equal(video.paused,false);assert.equal(video.loadCalls,loads);assert.equal(video.sourceWrites,writes);
video.pause();view.receive(data([row('G01'),row('G02'),row('G03','v1','running')],3));assert.equal(video.currentTime,12.5);assert.equal(video.paused,true);
view.receive(data([row('G01','v2'),row('G02')],4));assert.equal(video.src,'G01-v1.mp4');assert.equal(video.currentTime,12.5);assert.equal(actions.children[1].hidden,false);
actions.children[1].fire('click');assert.equal(video.src,'G01-v2.mp4');assert.equal(video.paused,false);
video.error={code:4};video.fire('error');assert.match(card.children[6].textContent,/当前播放器不支持/);
const stableSrc=video.src,stableLoad=video.loadCalls;timerFn();assert.equal(video.src,stableSrc);assert.equal(video.loadCalls,stableLoad);assert.equal(doc.head.children.length,1);doc.head.children[0].fire('error');assert.match(ids['update-error'].textContent,/已加载的视频保持不变/);assert.equal(video.src,stableSrc);
console.log('PASS: on-demand sources; unchanged node/time/pause; explicit version switch; media error and progress failure preserve player');
