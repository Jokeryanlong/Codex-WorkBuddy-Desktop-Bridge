'use strict';
const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const allowed=new Set(['probe','inspect','create','load','configure','dispatch','read','cancel','release']);
if(!allowed.has(input.op))throw Error('Unknown native operation');
async function operation(op,args){
  const sdk=globalThis.wb;
  if(!sdk?.conversations?.prepareInput)throw Error('WorkBuddy native conversation SDK is not ready');
  const contexts=globalThis.__codexWorkBuddyNativeContexts??=(new Map());
  const publicTools=content=>(Array.isArray(content)?content:[]).filter(b=>b?.type==='tool').slice(-128).map(b=>({id:String(b.toolCallId||'').slice(0,180),name:String(b.toolName||b._meta?.['codebuddy.ai/toolName']||b.kind||'tool').slice(0,180),status:String(b.status||'').slice(0,80)}));
  if(op==='probe')return {ready:true,current_session_id:sdk.conversations.currentId||null,version:globalThis.__WORKBUDDY_VERSION__||null};
  if(op==='inspect'){
    const c=await sdk.conversations.get(args.session_id);
    if(!c?.info?.id||c.info.id!==args.session_id)throw Error('Native conversation not found');
    const history=await c.requests();
    const latest=(history.items||[]).slice().sort((a,b)=>(b.timestamp||0)-(a.timestamp||0))[0];
    return {session_id:c.info.id,title:c.info.title||'',state:c.info.state,origin:c.info.origin||'desktop',mode:c.configManager.mode,permission_mode:c.configManager.permissionMode,
      latest_request:latest?{request_id:latest.id,state:latest.state||latest.assistantMessage?.state,tools:publicTools(latest.assistantMessage?.content)}:null};
  }
  let job=contexts.get(args.context);
  if(op==='create'||op==='load'){
    if(job)return {session_id:job.c.info.id,title:job.c.info.title||'',state:job.c.info.state};
    const c=op==='create'?await sdk.conversations.create({conversationId:args.session_id,cwd:args.cwd,transport:'local',isPlayground:true,conversationOrigin:'local',welcomeMode:'working',mode:'ask',model:args.model}):await sdk.conversations.get(args.session_id);
    if(!c?.info?.id)throw Error('WorkBuddy did not return a native conversation');
    if(c.info.id!==args.session_id)throw Error('WorkBuddy returned an unexpected conversation ID');
    if(op==='load'&&['working','planning','pending','waiting_permission','waiting_question'].includes(c.info.state))throw Error('Native conversation is busy; wait for the desktop task or cancel it explicitly before resuming');
    contexts.set(args.context,{c,sent:false,done:false});
    return {session_id:c.info.id,title:c.info.title||'',state:c.info.state,origin:c.info.origin||'desktop'};
  }
  if(op==='release'){
    if(job?.sent&&!job.done)try{await job.c.cancel();}catch{}
    contexts.delete(args.context);return {released:true};
  }
  if(!job)throw Error('Native conversation binding was lost; WorkBuddy may have restarted');
  const ensureAsk=async()=>{
    for(const [setter,value] of [['setMode','ask'],['setPermissionMode','default']]){
      const result=await job.c.configManager[setter](value);
      if(result&&typeof result==='object'&&'errorCode' in result)throw Error(`WorkBuddy ${setter} failed: ${JSON.stringify(result)}`);
    }
    if(job.c.configManager.mode!=='ask'||job.c.configManager.permissionMode!=='default')throw Error('WorkBuddy did not apply Ask mode with default permissions; prompt was not submitted');
    return {mode:job.c.configManager.mode,permission_mode:job.c.configManager.permissionMode};
  };
  if(op==='configure'){
    if(args.model)await job.c.configManager.setModel(args.model);
    if(args.reasoning_effort)await job.c.configManager.setThoughtLevel(args.reasoning_effort);
    return {configured:true,...await ensureAsk()};
  }
  if(op==='cancel'){await job.c.cancel();job.done=true;return {cancelled:true};}
  if(op==='dispatch'){
    if(job.sent)return {submitted:true,session_id:job.c.info.id,deduplicated:true};
    // A user may switch the desktop mode between turns; verify again at submission.
    const config=await ensureAsk();
    const history=await job.c.requests();
    job.before=new Set((history.items||[]).map(r=>r.id));job.prompt=args.prompt;job.startedAt=Date.now();job.sent=true;
    await sdk.conversations.prepareInput({target:{type:'conversation',conversationId:job.c.info.id},contentBlocks:[{type:'text',text:args.prompt}],submitMode:'blocks'});
    return {submitted:true,session_id:job.c.info.id,...config};
  }
  const history=await job.c.requests();
  const textOf=content=>typeof content==='string'?content:(Array.isArray(content)?content:[]).filter(b=>b?.type==='text').map(b=>b.text||'').join('');
  const matches=(history.items||[]).filter(r=>!job.before?.has(r.id)&&textOf(r.userMessage?.content)===job.prompt).sort((a,b)=>(a.timestamp||0)-(b.timestamp||0));
  const request=job.request_id?matches.find(r=>r.id===job.request_id):matches[0];
  if(!request)return {accepted:false,state:job.c.info.state,title:job.c.info.title||'',session_id:job.c.info.id};
  job.request_id=request.id;
  const state=request.state||request.assistantMessage?.state;
  const done=['completed','failed','cancelled','error','aborted'].includes(state);
  // Public tool metadata only; inputs, outputs and reasoning blocks stay out of MCP.
  const tools=publicTools(request.assistantMessage?.content);
  if(done)job.done=true;
  return {accepted:true,done,state,tools,mode:job.c.configManager.mode,permission_mode:job.c.configManager.permissionMode,request_id:request.id,session_id:job.c.info.id,title:job.c.info.title||'',answer:textOf(request.assistantMessage?.content),error:request.error?.message||request.assistantMessage?.error?.message||null,usage:request.usage||null};
}
(async()=>{
 const endpoint=`http://127.0.0.1:${input.port||18794}`;
 const pages=await (await fetch(endpoint+'/json/list',{signal:AbortSignal.timeout(3000)})).json();
 const page=pages.find(p=>p.type==='page'&&/\/WorkBuddy\/resources\/app\.asar\/renderer\/index\.html/i.test(decodeURI(p.url||'')));
 if(!page)throw Error('WorkBuddy native renderer target is unavailable');
 const socket=new WebSocket(page.webSocketDebuggerUrl);
 const timer=setTimeout(()=>{socket.close();console.error('Native WorkBuddy SDK call timed out');process.exitCode=1},40000);
 socket.addEventListener('open',()=>socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression:'('+operation.toString()+')('+JSON.stringify(input.op)+','+JSON.stringify(input.args||{})+')',returnByValue:true,awaitPromise:true}})));
 socket.addEventListener('message',event=>{const r=JSON.parse(event.data);if(r.id!==1)return;clearTimeout(timer);socket.close();if(r.error||r.result?.exceptionDetails){const e=r.error||r.result.exceptionDetails;console.error(e.exception?.description||e.text||e.message||'Native SDK call failed');process.exitCode=1;return}console.log(JSON.stringify(r.result?.result?.value??null));});
 socket.addEventListener('error',()=>{clearTimeout(timer);socket.close();console.error('Native WorkBuddy SDK transport failed');process.exitCode=1});
})().catch(e=>{console.error('Native desktop connection unavailable: '+e.message);process.exitCode=1});
