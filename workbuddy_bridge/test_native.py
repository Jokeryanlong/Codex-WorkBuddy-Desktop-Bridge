import json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from workbuddy_bridge import native as n
from workbuddy_bridge.acp import WorkBuddyError

class NativeSDKTests(unittest.TestCase):
    def binding(self):return n.Binding(None,'C:\\project','model','owned-context')
    def test_prompt_is_transported_as_json_data(self):
        prompt="引号 ');globalThis.unwanted=true;//\n换行"
        result=Mock(returncode=0,stdout='{"submitted":true}',stderr='')
        with patch.object(n,'node_executable',return_value=Path('node.exe')),patch.object(n.subprocess,'run',return_value=result) as run:
            n.call('dispatch',{'prompt':prompt})
        payload=json.loads(run.call_args.kwargs['input'])
        self.assertEqual(payload['args']['prompt'],prompt)
        self.assertEqual(payload['op'],'dispatch')

    def test_native_transport_failure_is_not_silently_replaced(self):
        result=Mock(returncode=1,stdout='',stderr='Native desktop SDK is unavailable')
        with patch.object(n,'node_executable',return_value=Path('node.exe')),patch.object(n.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(WorkBuddyError,'SDK is unavailable'):n.call('probe')

    def test_public_stream_is_returned_once_with_original_session(self):
        client=n.NativeClient(self.binding(),timeout_seconds=5);client.session_id='native-session'
        client._cancel=Mock();client._cancel.is_set.return_value=False
        updates=iter([{'accepted':False},{'accepted':True,'answer':'分析','state':'streaming'},{'accepted':True,'done':True,'answer':'分析完成','state':'completed','request_id':'native-request','title':'任务'}])
        events=[]
        def call(op,args=None):return next(updates) if op=='read' else {'submitted':True}
        with patch.object(n,'call',side_effect=call):result=client.prompt('任务',event_callback=events.append)
        chunks=[e['params']['update']['content']['text'] for e in events if e['params']['update']['sessionUpdate']=='agent_message_chunk']
        self.assertEqual(chunks,['分析','完成'])
        self.assertEqual(result['answer'],'分析完成')
        self.assertEqual(result['result']['nativeRequestId'],'native-request')
        self.assertTrue(all(e['params']['sessionId']=='native-session' for e in events))

    def test_failed_native_request_keeps_error_and_terminal_state(self):
        client=n.NativeClient(self.binding());client.session_id='sid'
        with patch.object(n,'call',side_effect=[{'submitted':True},{'accepted':True,'done':True,'state':'failed','error':'Model refused account','answer':''}]):
            result=client.prompt('test')
        self.assertEqual(result['result']['stopReason'],'refusal')
        self.assertEqual(result['result']['error'],'Model refused account')

    def test_timeout_cancels_owned_native_conversation(self):
        client=n.NativeClient(self.binding(),timeout_seconds=1);client.session_id='sid'
        with patch.object(n,'call') as call,patch.object(n.time,'monotonic',side_effect=[0,2]):
            with self.assertRaisesRegex(WorkBuddyError,'timed out'):client.prompt('test')
        self.assertEqual(call.call_args.args[0],'cancel')
        self.assertEqual(call.call_args.args[1]['context'],'owned-context')

    def test_cleanup_releases_binding_without_killing_desktop(self):
        binding=self.binding()
        with patch.object(n,'call',return_value={'released':True}) as call,patch.object(n.subprocess,'run') as process:
            n.kill_isolated_server(binding);n.kill_isolated_server(binding)
        call.assert_called_once_with('release',{'context':'owned-context'})
        process.assert_not_called()

    def test_restart_lost_binding_is_reported(self):
        client=n.NativeClient(self.binding());client.session_id='sid'
        with patch.object(n,'call',side_effect=WorkBuddyError('Native conversation binding was lost')):
            with self.assertRaisesRegex(WorkBuddyError,'binding was lost'):client.prompt('test')

    def test_legacy_full_access_argument_is_not_forwarded(self):
        client=n.NativeClient(self.binding())
        with patch.object(n,'call',return_value={'mode':'ask','permission_mode':'default'}) as call:
            result=client.configure_session(permission_mode='fullAccess')
        self.assertEqual(call.call_args.args[1]['permission_mode'],'default')
        self.assertEqual(result['mode'],'ask')

    def test_mode_evidence_is_in_completed_result(self):
        client=n.NativeClient(self.binding());client.session_id='sid'
        with patch.object(n,'call',side_effect=[{'submitted':True},{'accepted':True,'done':True,'state':'completed','answer':'one step','mode':'ask','permission_mode':'default'}]):
            result=client.prompt('test')
        self.assertEqual(result['result']['nativeMode'],'ask')
        self.assertEqual(result['result']['nativePermissionMode'],'default')

if __name__=='__main__':unittest.main()
