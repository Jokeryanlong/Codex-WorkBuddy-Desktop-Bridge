from pathlib import Path
import os
import unittest
from unittest.mock import Mock, patch
from workbuddy_bridge import desktop as d
from workbuddy_bridge.acp import DesktopServer, WorkBuddyError

class DesktopRuntimeTests(unittest.TestCase):
    def test_worker_does_not_inherit_unrelated_auth_or_endpoint(self):
        with patch.dict(os.environ, {'CODEBUDDY_AUTH_TOKEN':'foreign-token','CODEBUDDY_API_KEY':'foreign-key','CODEBUDDY_BASE_URL':'https://other.invalid','CODEBUDDY_CUSTOM_HEADERS':'private'}):
            env=d._runtime_env('gateway-password',1234)
        for key in ('CODEBUDDY_AUTH_TOKEN','CODEBUDDY_API_KEY','CODEBUDDY_BASE_URL','CODEBUDDY_CUSTOM_HEADERS'):
            self.assertNotIn(key,env)
        self.assertEqual(env['SERVER__HOST'],'127.0.0.1')
        self.assertEqual(env['CODEBUDDY_GATEWAY_AUTH'],'password')

    def test_native_account_check_ignores_worker_logs(self):
        proc=Mock(stdout='other log\nWB_AUTH_CHECK:{"account_authenticated":true}\n',stderr='',returncode=0)
        with patch.object(d,'_process_executable',return_value=Path('WorkBuddy.exe')),patch.object(d.subprocess,'run',return_value=proc):
            result=d.account_status(DesktopServer('','','desktop','',0,123))
        self.assertTrue(result['account_authenticated'])

    def test_auth_failure_does_not_return_arbitrary_stderr(self):
        proc=Mock(stdout='',stderr='arbitrary sensitive log\nWORKBUDDY_DESKTOP_AUTH_ERROR: login expired',returncode=1)
        with patch.object(d,'_process_executable',return_value=Path('WorkBuddy.exe')),patch.object(d.subprocess,'run',return_value=proc):
            with self.assertRaisesRegex(WorkBuddyError,'^login expired$'):
                d.account_status(DesktopServer('','','desktop','',0,123))

    def test_cleanup_never_kills_an_unowned_process(self):
        with patch.object(d.subprocess,'run') as kill:
            d.kill_isolated_server(DesktopServer('','','unowned','',0,123))
        kill.assert_not_called()

    def test_owned_worker_cleanup_is_idempotent(self):
        proc=Mock(pid=9876)
        proc.poll.return_value=None
        with patch.dict(d._workers,{'owned':proc},clear=True),patch.object(d.subprocess,'run') as kill:
            runtime=DesktopServer('','','owned','',0,9876)
            d.kill_isolated_server(runtime)
            d.kill_isolated_server(runtime)
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0],['taskkill','/PID','9876','/T','/F'])

if __name__=='__main__':unittest.main()
