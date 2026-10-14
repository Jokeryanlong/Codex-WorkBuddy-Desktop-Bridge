"""Call WorkBuddy's own conversation SDK; the desktop owns execution and streaming."""
from __future__ import annotations
from dataclasses import dataclass
import json,os,subprocess,threading,time,uuid
from pathlib import Path
from workbuddy_bridge.acp import WorkBuddyError
from workbuddy_bridge.desktop import discover_desktop_server as discover_window, profile_dir

VERSION='0.4.1'
BACKEND='workbuddy-desktop-native-conversation'
PORT=int(os.environ.get('WORKBUDDY_NATIVE_PORT','18794'))

def node_executable() -> Path:
    root=profile_dir()/'binaries/node/versions'
    candidates=sorted(root.glob('*/node.exe'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not candidates:raise WorkBuddyError('WorkBuddy managed Node.js runtime was not found')
    return candidates[0]

def call(op: str, args: dict|None=None) -> dict:
    try:
        result=subprocess.run([str(node_executable()),str(Path(__file__).with_name('native_driver.cjs'))],
                              input=json.dumps({'port':PORT,'op':op,'args':args or {}},ensure_ascii=False),
                              capture_output=True,text=True,encoding='utf8',errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW,timeout=45)
    except subprocess.TimeoutExpired as exc:raise WorkBuddyError('Native WorkBuddy SDK request timed out') from exc
    if result.returncode:
        raise WorkBuddyError(result.stderr.strip()[:1500] or 'Native desktop SDK request failed')
    try:return json.loads(result.stdout)
    except ValueError as exc:raise WorkBuddyError('Native SDK returned an invalid response') from exc

def discover_desktop_server():
    desktop=discover_window()
    call('probe')
    return desktop

@dataclass
class Binding:
    desktop: object
    cwd: str
    model: str
    context: str
    released: bool=False

def spawn_isolated_server(desktop,cwd,*,model='deepseek-v4.1-flash',session_id='',**unused):
    # Bind to a native conversation; no separate agent process is spawned.
    return Binding(desktop,cwd,model,uuid.uuid4().hex)

def kill_isolated_server(binding: Binding):
    if binding.released:return
    try:call('release',{'context':binding.context})
    finally:binding.released=True

class NativeClient:
    def __init__(self,server: Binding,*,timeout_seconds=600,event_callback=None):
        self.server=server;self.timeout_seconds=timeout_seconds;self.event_callback=event_callback
        self.session_id=None;self.is_playground=True;self._cancel=threading.Event()

    def connect(self):call('probe')
    def new_session(self,cwd,*,as_task=True):
        sid=str(uuid.uuid4())
        result=call('create',{'context':self.server.context,'session_id':sid,'cwd':cwd,'model':self.server.model})
        self.session_id=result['session_id'];return self.session_id
    def load_session(self,session_id,cwd,*,as_task=True):
        result=call('load',{'context':self.server.context,'session_id':session_id})
        self.session_id=result['session_id'];return self.session_id
    def configure_session(self,*,model='',reasoning_effort='',permission_mode='default',session_id=None):
        return call('configure',{'context':self.server.context,'model':model,'reasoning_effort':reasoning_effort,'permission_mode':'default'})
    def notify(self,method,params):
        if method=='session/cancel':
            self._cancel.set();call('cancel',{'context':self.server.context})
    def close(self):kill_isolated_server(self.server)
    def prompt(self,text,*,session_id=None,event_callback=None):
        sid=session_id or self.session_id
        events=[]
        def feed(update):
            event={'method':'session/update','params':{'sessionId':sid,'update':update}}
            events.append(event)
            if self.event_callback:self.event_callback(event)
            if event_callback and event_callback is not self.event_callback:event_callback(event)
        call('dispatch',{'context':self.server.context,'prompt':text})
        deadline=time.monotonic()+self.timeout_seconds
        previous='';accepted=False
        while time.monotonic()<deadline:
            if self._cancel.is_set():raise WorkBuddyError('Task cancelled by caller')
            result=call('read',{'context':self.server.context})
            if result.get('accepted') and not accepted:
                accepted=True;feed({'sessionUpdate':'user_message_chunk','content':{'type':'text','text':text}})
            answer=result.get('answer') or ''
            if answer.startswith(previous) and len(answer)>len(previous):
                feed({'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':answer[len(previous):]}})
            previous=answer
            if result.get('done'):
                reason='end_turn' if result.get('state')=='completed' else 'cancelled' if result.get('state') in {'cancelled','aborted'} else 'refusal'
                feed({'sessionUpdate':'session_info_update','title':result.get('title') or ''})
                feed({'sessionUpdate':'session_end','stopReason':reason})
                return {'session_id':sid,'answer':answer,'title':result.get('title') or '',
                        'result':{'stopReason':reason,'error':result.get('error'),'nativeRequestId':result.get('request_id'),'nativeState':result.get('state'),'nativeMode':result.get('mode'),'nativePermissionMode':result.get('permission_mode'),'usage':result.get('usage')},'events':events}
            self._cancel.wait(.7)
        call('cancel',{'context':self.server.context})
        raise WorkBuddyError('Native WorkBuddy conversation timed out; its desktop session remains visible')
