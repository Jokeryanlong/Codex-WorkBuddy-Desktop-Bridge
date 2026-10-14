"""Exercise the actual JavaScript SDK operation, including rejected mode changes."""
import subprocess
import unittest
from pathlib import Path
from workbuddy_bridge.native import node_executable
from workbuddy_bridge.acp import WorkBuddyError

class AskModeContractTests(unittest.TestCase):
    def test_creation_resume_dispatch_and_rejected_configuration(self):
        try:node=node_executable()
        except WorkBuddyError:self.skipTest('WorkBuddy Node runtime not installed')
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(source.indexOf('async function operation('),source.indexOf('\n(async()=>'));
let c,created,sent=0,reject=false;
const manager={mode:'craft',permissionMode:'fullAccess',
  async setMode(mode){if(reject)return {errorCode:'MODE_FAILED'};this.mode=mode},
  async setPermissionMode(mode){this.permissionMode=mode}};
c={info:{id:'sid',state:'idle'},configManager:manager,async requests(){return {items:[]}}};
const sdk={conversations:{async create(args){created=args;manager.mode=args.mode;return c},
  async get(){return c},async prepareInput(){sent++}}};
const context={wb:sdk,Map,Set,Date};
vm.runInNewContext(body+';globalThis.run=operation',context);
(async()=>{
  await context.run('create',{context:'new',session_id:'sid',cwd:'test'});
  assert.equal(created.mode,'ask');
  c.info.state='working';
  await assert.rejects(context.run('load',{context:'busy',session_id:'sid'}),/busy/);
  c.info.state='idle';
  await context.run('load',{context:'resume',session_id:'sid'});
  let config=await context.run('configure',{context:'resume',permission_mode:'fullAccess'});
  assert.equal(config.mode,'ask');assert.equal(config.permission_mode,'default');
  manager.mode='craft';manager.permissionMode='fullAccess';
  await context.run('dispatch',{context:'resume',prompt:'one step'});
  assert.equal(manager.mode,'ask');assert.equal(manager.permissionMode,'default');assert.equal(sent,1);
  await context.run('dispatch',{context:'resume',prompt:'one step'});assert.equal(sent,1);
  c.requests=async()=>({items:[{id:'request',timestamp:1,state:'completed',userMessage:{content:'one step'},assistantMessage:{content:[{type:'reasoning',text:'PRIVATE'},{type:'text',text:'public'},{type:'tool',toolCallId:'t',toolName:'Read',status:'completed',rawInput:{secret:'PRIVATE'},rawOutput:{text:'PRIVATE'}}]}}]});
  const progress=await context.run('read',{context:'resume'});
  assert.equal(progress.answer,'public');assert.equal(progress.tools[0].name,'Read');
  assert.ok(!JSON.stringify(progress).includes('PRIVATE'));
  const inspected=await context.run('inspect',{session_id:'sid'});
  assert.ok(!JSON.stringify(inspected).includes('PRIVATE'));assert.equal(sent,1);
  reject=true;
  await assert.rejects(context.run('dispatch',{context:'new',prompt:'must not submit'}),/MODE_FAILED/);
  assert.equal(sent,1);
  console.log('ASK_CONTRACT_PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run([str(node),'-e',script,str(Path(__file__).with_name('native_driver.cjs'))],
            capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('ASK_CONTRACT_PASS',result.stdout)
