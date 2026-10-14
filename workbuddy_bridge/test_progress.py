import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from workbuddy_bridge.progress import ProgressJournal
from workbuddy_bridge import server as s
from workbuddy_bridge import native as n

class ProgressTests(unittest.TestCase):
    def test_direct_file_read_rejects_escape_and_routes_sdk_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'sample.txt').write_text('page',encoding='utf-8')
            with patch.object(s,'native_call',return_value={'text':'pa','next_offset':2}) as call:
                out=s.workbuddy_read_file('sample.txt',str(root),0,2)
                self.assertTrue(out['ok']);self.assertEqual(out['text'],'pa')
                self.assertEqual(call.call_args.args[0],'read_file')
                self.assertEqual(call.call_args.args[1]['limit'],2)
            with patch.object(s,'native_call') as call:
                self.assertFalse(s.workbuddy_read_file('../outside.txt',str(root))['ok']);call.assert_not_called()
    def test_cursor_pages_do_not_repeat_events(self):
        p=ProgressJournal()
        for text in ('one','two'):
            p.feed({'sessionUpdate':'agent_message_chunk','content':{'text':text}})
        first=p.read(0,1);second=p.read(first['next_cursor'])
        self.assertEqual([e['text'] for e in first['events']],['one'])
        self.assertEqual([e['text'] for e in second['events']],['two'])
        self.assertEqual(p.read(second['next_cursor'])['events'],[])

    def test_private_thoughts_prompts_and_tool_payloads_are_excluded(self):
        p=ProgressJournal()
        for kind in ('agent_thought_chunk','user_message_chunk'):
            p.feed({'sessionUpdate':kind,'content':{'text':'private'}})
        p.feed({'sessionUpdate':'tool_call','toolCallId':'t','title':'Read','status':'completed','rawInput':{'secret':'private'},'rawOutput':'private'})
        result=p.read();self.assertEqual(len(result['events']),1)
        self.assertNotIn('private',str(result))

    def test_retention_gap_and_output_budget_are_explicit(self):
        p=ProgressJournal(capacity=4)
        p.feed({'sessionUpdate':'agent_message_chunk','content':{'text':'x'*30000}})
        result=p.read();self.assertTrue(result['history_gap'])
        self.assertLessEqual(sum(len(e.get('text','')) for e in result['events']),12000)
        self.assertTrue(result['has_more'])

    def test_invalid_cursor_is_not_treated_as_completion(self):
        p=ProgressJournal()
        with self.assertRaises(ValueError):p.read(1)
        with self.assertRaises(ValueError):p.read(0,101)

    def test_empty_replacement_clears_previous_answer(self):
        p=ProgressJournal()
        p.feed({'sessionUpdate':'agent_message_replace','content':{'text':''}})
        self.assertEqual(p.read()['events'][0]['type'],'answer_replace')
        self.assertEqual(p.read()['events'][0]['text'],'')

    def test_direct_session_uses_only_inspect_and_validates_uuid(self):
        sid='01234567-89ab-4cde-8fab-0123456789ab'
        with patch.object(s,'native_call',return_value={'session_id':sid,'mode':'ask'}) as call:
            result=s.workbuddy_session(sid)
        call.assert_called_once_with('inspect',{'session_id':sid})
        self.assertEqual(result['mode'],'ask')
        with patch.object(s,'native_call') as call:
            self.assertFalse(s.workbuddy_session('bad')['ok']);call.assert_not_called()

    def test_waiting_state_and_terminal_state_are_distinct(self):
        task=s.TaskState('owned','prompt','cwd',state='observing',native_state='waiting_question')
        with patch.dict(s.TASKS,{'owned':task}):
            result=s.workbuddy_progress('owned')
            self.assertTrue(result['awaiting_user']);self.assertFalse(result['finished'])
            task.state='failed';task.native_state='error';task.error='model service failed'
            result=s.workbuddy_progress('owned')
            self.assertTrue(result['finished']);self.assertEqual(result['error'],'model service failed')

    def test_native_stream_emits_tool_changes_and_answer_replacement(self):
        client=n.NativeClient(n.Binding(None,'cwd','model','context'),timeout_seconds=5)
        client.session_id='sid';client._cancel=unittest.mock.Mock();client._cancel.is_set.return_value=False
        tool={'id':'t','name':'Read','status':'running'}
        rows=iter([
            {'accepted':True,'state':'streaming','answer':'draft','tools':[tool]},
            {'accepted':True,'state':'streaming','answer':'draft','tools':[tool]},
            {'accepted':True,'done':True,'state':'completed','answer':'corrected','tools':[dict(tool,status='completed')]},
        ])
        def call(op,args):return next(rows) if op=='read' else {}
        events=[]
        with patch.object(n,'call',side_effect=call):result=client.prompt('step',event_callback=events.append)
        updates=[e['params']['update'] for e in events]
        self.assertEqual(len([u for u in updates if u['sessionUpdate']=='tool_call']),1)
        self.assertEqual(len([u for u in updates if u['sessionUpdate']=='tool_call_update']),1)
        self.assertEqual(result['answer'],'corrected')
        self.assertTrue(any(u['sessionUpdate']=='agent_message_replace' for u in updates))
