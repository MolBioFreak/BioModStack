const endpoint='http://127.0.0.1:19093';
(async()=>{
 const version=await (await fetch(endpoint+'/json/version')).json(), ws=new WebSocket(version.webSocketDebuggerUrl);
 await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let id=0, pending=new Map(), events=[], sessions=[];
 function send(method,params={},sessionId){return new Promise((resolve,reject)=>{const i=++id;pending.set(i,{resolve,reject});ws.send(JSON.stringify({id:i,method,params,...sessionId?{sessionId}:{}}))})}
 ws.onmessage=async e=>{let m=JSON.parse(e.data); if(m.id){let p=pending.get(m.id); if(p){pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)};return}
 if(m.method==='Network.webSocketCreated') events.push({session:m.sessionId,url:m.params.url,type:'WebSocket'});
 if(m.method==='Network.requestWillBeSent') events.push({session:m.sessionId,url:m.params.request.url,type:m.params.type});
 if(m.method==='Network.loadingFailed') events.push({session:m.sessionId,failed:m.params.errorText,blocked:m.params.blockedReason});
 if(m.method==='Target.attachedToTarget'){let s=m.params.sessionId;sessions.push({id:s,type:m.params.targetInfo.type,url:m.params.targetInfo.url});try{await send('Network.enable',{},s);await send('Runtime.runIfWaitingForDebugger',{},s)}catch(e){console.error(e)}}
 };
 const {targetId}=await send('Target.createTarget',{url:'about:blank'}); const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true}); await send('Network.enable',{},sessionId); await send('Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:true,flatten:true},sessionId);
 await send('Page.navigate',{url:'http://127.0.0.1:4178/tests/browser/av2-test.html'},sessionId); await new Promise(r=>setTimeout(r,6000));
 const child=sessions.find(x=>x.type==='iframe'); let info=child?await send('Runtime.evaluate',{expression:`JSON.stringify({text:document.body.innerText,canvas:document.querySelectorAll('canvas').length, rows:document.querySelectorAll('.av2-wheel-scroller').length})`,returnByValue:true},child.id):null;
 if (!child || !info?.result?.value?.includes('row_2')) throw Error('AV2 did not render synthetic rows');
 const frameRect = await send('Runtime.evaluate',{expression:`JSON.stringify(document.querySelector('iframe').getBoundingClientRect().toJSON())`,returnByValue:true},sessionId);
 const matrix = await send('Runtime.evaluate',{expression:`JSON.stringify([...document.querySelectorAll('.av2-wheel-scroller')].at(-1).getBoundingClientRect().toJSON())`,returnByValue:true},child.id);
 const f=JSON.parse(frameRect.result.value), r=JSON.parse(matrix.result.value);
 const x=f.x+r.x+14, y=f.y+r.y+8;
 await send('Input.dispatchMouseEvent',{type:'mouseMoved',x,y},sessionId); await send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1},sessionId); await send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1},sessionId);
 await new Promise(r=>setTimeout(r,500));
 const result=await send('Runtime.evaluate',{expression:`document.querySelector('#result').textContent`,returnByValue:true},sessionId);
 const anchor=JSON.parse(result.result.value); if(anchor.rowId!=='r1'||anchor.column!==2||anchor.residue!==null) throw Error('Wrong clicked gap: '+JSON.stringify(anchor));
 const remote=events.filter(e=>e.url && !e.url.startsWith('http://127.0.0.1:4178/') && !e.url.startsWith('ws://127.0.0.1:4178/'));
 console.log(JSON.stringify({sessions,events,anchor,remote},null,2)); if(remote.length) throw Error('Unexpected outbound requests'); await send('Target.closeTarget',{targetId});ws.close();
})().catch(e=>{console.error(e);process.exit(1)})