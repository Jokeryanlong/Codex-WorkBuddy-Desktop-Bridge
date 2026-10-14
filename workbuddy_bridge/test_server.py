from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from workbuddy_bridge import server as s
from workbuddy_bridge.acp import WorkBuddyError, _iter_sse_json
from workbuddy_bridge.scheduler import Scheduler


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log_patch = patch.object(s, "LOG_DIR", Path(self.temp.name))
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.tasks_patch = patch.object(s, "TASKS", {})
        self.tasks_patch.start()
        self.addCleanup(self.tasks_patch.stop)
        self.scheduler = Mock()
        self.scheduler.submit.return_value = True
        self.scheduler_patch = patch.object(s, "SCHEDULER", self.scheduler)
        self.scheduler_patch.start()
        self.addCleanup(self.scheduler_patch.stop)

    def start(self, **kwargs):
        return s.workbuddy_start("test", cwd=self.temp.name, **kwargs)

    def test_prompt_refusal_is_returned_without_waiting_for_activity_timeout(self):
        task = s.TaskState("refusal", "test", self.temp.name)
        client = Mock()
        client.new_session.return_value = "session-refusal"
        client.prompt.return_value = {
            "result": {"stopReason": "refusal", "error": "Authentication required"}
        }
        start = time.monotonic()
        transport = s._dispatch_prompt(task, client, None, "test prompt")
        self.assertTrue(transport.done.wait(0.2))
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(transport.result["stopReason"], "refusal")

    def test_invalid_timeout_has_no_side_effect(self):
        for timeout in (0, -10, 3601, True):
            self.assertFalse(self.start(timeout_seconds=timeout)["ok"])
        self.scheduler.submit.assert_not_called()

    def test_free_dialogue_can_resume_without_review_target(self):
        result=self.start(resume_session_id='01a13537-c09d-74af-be16-55e68835aa75')
        self.assertTrue(result['ok'])
        self.assertFalse(result['resume_review'])

    def test_native_auth_refusal_metadata_is_reported(self):
        task=s.TaskState('refusal-meta','test',self.temp.name)
        channel=Mock()
        channel.wait_for_end.return_value='refusal'
        def dispatch(*args):
            task.channel=channel
            transport=s.PromptTransport(result={'stopReason':'refusal','_meta':{'codebuddy.ai/errorMessage':'{"message":"Authentication required","data":{"details":"Desktop account expired"}}'}})
            transport.done.set()
            return transport
        with patch.object(s,'discover_desktop_server'),patch.object(s,'spawn_isolated_server'),patch.object(s,'AcpClient'),patch.object(s,'kill_isolated_server'),patch.object(s,'_dispatch_prompt',side_effect=dispatch):
            s._run(task,1)
        self.assertEqual(task.state,'failed')
        self.assertIn('Desktop account expired',task.error)

    def test_retry_dedup_and_conflict(self):
        first = self.start(request_id="one")
        second = self.start(request_id="one")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(self.start(request_id="one", model="different")["code"], "REQUEST_CONFLICT")
        self.assertEqual(self.scheduler.submit.call_count, 1)

    def test_queue_full_does_not_leave_phantom_task(self):
        self.scheduler.submit.return_value = False
        self.assertEqual(self.start()["code"], "QUEUE_FULL")
        self.assertEqual(s.TASKS, {})

    def test_queued_cancel_never_contacts_desktop(self):
        result = self.start()
        task = s.TASKS[result["task_id"]]
        self.assertEqual(s.workbuddy_cancel(task.task_id)["state"], "cancelled")
        with patch.object(s, "discover_desktop_server") as discover:
            s._run(task, 1)
        discover.assert_not_called()
        self.assertEqual(s.workbuddy_wait(task.task_id, 0)["state"], "cancelled")

    def test_connecting_cancel_is_accepted(self):
        task = s.TaskState("one", "test", self.temp.name, state="connecting")
        s.TASKS[task.task_id] = task
        self.assertTrue(s.workbuddy_cancel(task.task_id)["ok"])
        self.assertTrue(task.cancel_event.is_set())

    def test_list_pagination_omits_answers_and_filters(self):
        for index in range(3):
            task = s.TaskState(str(index), "test", self.temp.name, state="completed", answer="很长的结果", created_at=index)
            s.TASKS[task.task_id] = task
        result = s.workbuddy_list(limit=2)
        self.assertEqual([row["task_id"] for row in result["tasks"]], ["2", "1"])
        self.assertEqual(result["next_offset"], 2)
        self.assertEqual(result["tasks"][0]["answer"], "")
        self.assertEqual(s.workbuddy_list(state="failed")["total"], 0)
        page = s.workbuddy_result("2", limit=2)
        self.assertEqual(page["answer"], "很长")
        self.assertEqual(s.workbuddy_result("2", offset=page["next_offset"])["answer"], "的结果")

    def test_cancel_completed_is_idempotent(self):
        task = s.TaskState("one", "test", self.temp.name, state="completed", answer="done", finished_at=time.time())
        s.TASKS[task.task_id] = task
        self.assertEqual(s.workbuddy_cancel(task.task_id)["state"], "completed")
        self.assertFalse(task.cancel_event.is_set())

    def test_competing_review_does_not_release_owners_lock(self):
        owner = threading.Lock()
        owner.acquire()
        task = s.TaskState("one", "test", self.temp.name, identity="S1", resume_session_id="occupied")
        with patch.dict(s.CONTINUATION_SESSION_LOCKS, {"occupied": owner}, clear=True):
            s._run(task, 1)
        self.assertEqual(task.state, "failed")
        self.assertTrue(owner.locked())
        owner.release()

    def test_failed_connect_still_kills_runtime_and_finishes(self):
        task = s.TaskState("one", "test", self.temp.name)
        client = Mock()
        client.connect.side_effect = WorkBuddyError("connect failed")
        with (patch.object(s, "discover_desktop_server"),
              patch.object(s, "spawn_isolated_server", return_value="runtime"),
              patch.object(s, "AcpClient", return_value=client),
              patch.object(s, "kill_isolated_server") as kill):
            s._run(task, 1)
        kill.assert_called_once_with("runtime")
        client.close.assert_called_once()
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.state, "failed")

    def test_cleanup_failure_does_not_hide_primary_error(self):
        task = s.TaskState("one", "test", self.temp.name)
        client = Mock()
        client.connect.side_effect = WorkBuddyError("connect failed")
        client.close.side_effect = RuntimeError("close failed")
        with (patch.object(s, "discover_desktop_server"),
              patch.object(s, "spawn_isolated_server", return_value="runtime"),
              patch.object(s, "AcpClient", return_value=client),
              patch.object(s, "kill_isolated_server", side_effect=RuntimeError("kill failed"))):
            s._run(task, 1)
        self.assertEqual(task.error, "connect failed")
        self.assertIn("kill failed", task.cleanup_error)
        self.assertIn("close failed", task.cleanup_error)
        self.assertIsNotNone(task.finished_at)

    def test_history_failure_keeps_completed_answer(self):
        task = s.TaskState("one", "test", self.temp.name)
        client = Mock()
        transport = s.PromptTransport(result={"stopReason": "end_turn"})
        transport.done.set()

        def dispatch(*args):
            task.session_id = "session"
            task.channel = Mock()
            task.channel.wait_for_end.return_value = "end_turn"
            task.channel.wait_for_title.return_value = "title"
            task.channel.answer.return_value = "verified answer"
            return transport

        with (patch.object(s, "discover_desktop_server"),
              patch.object(s, "spawn_isolated_server", return_value="runtime"),
              patch.object(s, "AcpClient", return_value=client),
              patch.object(s, "kill_isolated_server"),
              patch.object(s, "_dispatch_prompt", side_effect=dispatch),
              patch.object(s, "wait_for_task_registration", return_value=False),
              patch.object(s, "register_completed_session", side_effect=OSError("database locked"))):
            s._run(task, 1)
        self.assertEqual(task.state, "completed")
        self.assertEqual(task.answer, "verified answer")
        self.assertIn("database locked", task.warning)

    def test_running_cancel_cleans_up_runtime(self):
        task = s.TaskState("one", "test", self.temp.name)
        s.TASKS[task.task_id] = task
        ready = threading.Event()
        client = Mock()

        def dispatch(*args):
            task.session_id = "session"
            task.channel = s.SessionEventChannel("session")
            ready.set()
            return s.PromptTransport()

        with (patch.object(s, "discover_desktop_server"),
              patch.object(s, "spawn_isolated_server", return_value="runtime"),
              patch.object(s, "AcpClient", return_value=client),
              patch.object(s, "kill_isolated_server") as kill,
              patch.object(s, "_dispatch_prompt", side_effect=dispatch)):
            worker = threading.Thread(target=s._run, args=(task, 10), daemon=True)
            worker.start()
            self.assertTrue(ready.wait(2))
            self.assertTrue(s.workbuddy_cancel(task.task_id)["ok"])
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(task.state, "cancelled")
        kill.assert_called_once_with("runtime")

    def test_mcp_wait_does_not_block_cancel(self):
        task = s.TaskState("one", "test", self.temp.name)
        s.TASKS[task.task_id] = task

        async def scenario():
            waiting = asyncio.create_task(s.mcp.call_tool("workbuddy_wait", {"task_id": "one", "timeout_seconds": 3}))
            await asyncio.sleep(0.05)
            await s.mcp.call_tool("workbuddy_cancel", {"task_id": "one"})
            await waiting

        start = time.monotonic()
        asyncio.run(scenario())
        self.assertLess(time.monotonic() - start, 1.5)


class SchedulerTests(unittest.TestCase):
    def test_concurrency_and_queue_capacity_are_enforced(self):
        scheduler = Scheduler(workers=2, capacity=2)
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        try:
            for event in entered:
                self.assertTrue(scheduler.submit(lambda event=event: (event.set(), release.wait(3))))
            self.assertTrue(all(event.wait(1) for event in entered))
            self.assertTrue(scheduler.submit(lambda: None))
            self.assertTrue(scheduler.submit(lambda: None))
            self.assertFalse(scheduler.submit(lambda: None))
        finally:
            release.set()
            scheduler._queue.join()


class SseTests(unittest.TestCase):
    def test_standard_data_fields_and_multiline(self):
        wire = ': heartbeat\r\nevent: update\r\ndata:{"id":1,\r\ndata: "result":{}}\r\n\r\ndata: {"id":2,"result":true}'
        response = httpx.Response(200, text=wire)
        self.assertEqual(list(_iter_sse_json(response)), [{"id": 1, "result": {}}, {"id": 2, "result": True}])


if __name__ == "__main__":
    unittest.main()
