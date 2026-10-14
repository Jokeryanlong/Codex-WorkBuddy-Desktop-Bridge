"""Bounded, cursor-addressed public progress; excludes prompts and reasoning."""
from collections import deque
import threading
import time

class ProgressJournal:
    def __init__(self, capacity=256):
        self.entries=deque(maxlen=capacity)
        self.sequence=0
        self.lock=threading.Lock()

    def feed(self, update):
        kind=update.get('sessionUpdate')
        items=[]
        if kind in {'agent_message_chunk','agent_message_replace'}:
            content=update.get('content') or {}
            text=content.get('text','') if isinstance(content,dict) else ''
            if not isinstance(text,str):return
            if kind=='agent_message_replace' and not text:
                items.append({'type':'answer_replace','text':''})
            for offset in range(0,len(text),4096):
                items.append({'type':'answer_replace' if kind=='agent_message_replace' and offset==0 else 'answer_delta', 'text':text[offset:offset+4096]})
        elif kind=='native_progress':
            items.append({'type':'status','native_state':str(update.get('state') or ''),
                'mode':str(update.get('mode') or ''),'permission_mode':str(update.get('permission_mode') or '')})
        elif kind in {'tool_call','tool_call_update'}:
            items.append({'type':'tool','tool_call_id':str(update.get('toolCallId') or '')[:180],
                'tool_name':str(update.get('title') or '')[:180],'status':str(update.get('status') or '')[:80]})
        elif kind=='session_end':
            items.append({'type':'end','stop_reason':str(update.get('stopReason') or '')[:80]})
        with self.lock:
            for item in items:
                self.sequence+=1
                self.entries.append({'cursor':self.sequence,'timestamp':time.time(),**item})

    def read(self, cursor=0, limit=50):
        if cursor<0 or not 1<=limit<=100:raise ValueError('cursor must be nonnegative; limit must be 1..100')
        with self.lock:
            if cursor>self.sequence:raise ValueError('cursor is ahead of this task')
            earliest=self.entries[0]['cursor'] if self.entries else self.sequence+1
            selected=[dict(e) for e in self.entries if e['cursor']>cursor]
            page=[];chars=0
            for e in selected[:limit]:
                size=len(e.get('text',''))+300
                if page and chars+size>12000:break
                page.append(e);chars+=size
            next_cursor=page[-1]['cursor'] if page else max(cursor,earliest-1)
            return {'events':page,'next_cursor':next_cursor,'latest_cursor':self.sequence,
                'has_more':next_cursor<self.sequence,'history_gap':cursor<earliest-1,
                'earliest_cursor':earliest,'reasoning_included':False}
