from __future__ import annotations

import asyncio
import os
from functools import wraps
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from workbuddy_bridge.activity_log import ActivityLogger
from workbuddy_bridge.acp import (
    AcpClient,
    WorkBuddyError,
    task_prompt,
)
from workbuddy_bridge.native import discover_desktop_server, kill_isolated_server, spawn_isolated_server, VERSION, BACKEND, NativeClient
AcpClient = NativeClient
from workbuddy_bridge.history import register_completed_session, wait_for_task_registration
from workbuddy_bridge.identities import compose_identity_prompt, normalize_identity
from workbuddy_bridge.multiplexer import SessionEventChannel
from workbuddy_bridge.scheduler import Scheduler
from workbuddy_bridge.review_sessions import (
    REVIEW_IDENTITIES,
    bind_review_session,
    build_rereview_prompt,
    find_review_session,
    normalize_review_target,
    normalize_session_id,
    prepare_review_resume,
    target_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "work" / "workbuddy-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP(
    "workbuddy_worker",
    instructions="Delegate one bounded analysis step per turn to WorkBuddy's native desktop Ask conversations. Read each answer before sending the next step; progress appears in the WorkBuddy chat window.",
    log_level="WARNING",
)


def bridge_tool():
    """FastMCP invokes synchronous tools on its event loop; offload blocking I/O."""
    def register(fn):
        @wraps(fn)
        async def invoke(*args, **kwargs):
            return await asyncio.to_thread(fn, *args, **kwargs)
        mcp.tool()(invoke)
        return fn
    return register


@dataclass
class TaskState:
    task_id: str
    prompt: str
    cwd: str
    identity: str = ""
    model: str = ""
    reasoning_effort: str = ""
    resume_session_id: str = ""
    review_target: str = ""
    resume_review: bool = False
    resumed: bool = False
    previous_sha256: str | None = None
    current_sha256: str | None = None
    state: str = "queued"
    session_id: str | None = None
    answer: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    warning: str | None = None
    cleanup_error: str | None = None
    last_activity: str = ""
    request_id: str = ""
    request_signature: tuple = ()
    client: AcpClient | None = None
    channel: SessionEventChannel | None = None
    condition: threading.Condition = field(default_factory=threading.Condition)


TASKS: dict[str, TaskState] = {}
TASKS_LOCK = threading.Lock()
TERMINAL_STATES = {"completed", "failed", "cancelled"}
MAX_CONCURRENT_TASKS = int(os.environ.get("WORKBUDDY_MAX_CONCURRENT", "1"))
MAX_QUEUED_TASKS = int(os.environ.get("WORKBUDDY_MAX_QUEUED", "32"))
DEFAULT_MODEL = os.environ.get("WORKBUDDY_DEFAULT_MODEL", "deepseek-v4.1-flash").strip() or "deepseek-v4.1-flash"
TASK_RETENTION = 200
SCHEDULER = Scheduler(MAX_CONCURRENT_TASKS, MAX_QUEUED_TASKS)


class TaskCancelled(WorkBuddyError):
    pass


def _check_cancelled(task: TaskState) -> None:
    if task.cancel_event.is_set():
        raise TaskCancelled("Task cancelled by caller")


def _transition(task: TaskState, state: str) -> None:
    with task.condition:
        _check_cancelled(task)
        task.state = state
        task.updated_at = time.time()
        task.condition.notify_all()

PROMPT_DISPATCH_INTERVAL_SECONDS = 1.0
PROMPT_ACCEPTANCE_TIMEOUT_SECONDS = 45.0
PROMPT_DISPATCH_LOCK = threading.Lock()
LAST_PROMPT_DISPATCH_AT = 0.0
CONTINUATION_SESSION_LOCKS: dict[str, threading.Lock] = {}
CONTINUATION_SESSION_LOCKS_LOCK = threading.Lock()


@dataclass
class PromptTransport:
    done: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    thread: threading.Thread | None = None


def _dispatch_prompt(
    task: TaskState,
    client: AcpClient,
    event_callback: Any,
    prompt: str,
) -> PromptTransport:
    """Serialize prompt acceptance while keeping each runtime independent."""
    global LAST_PROMPT_DISPATCH_AT
    transport = PromptTransport()

    def send(channel: SessionEventChannel) -> None:
        try:
            _check_cancelled(task)
            response = client.prompt(
                prompt,
                session_id=task.session_id,
                event_callback=channel.feed,
            )
            transport.response = response
            transport.result = response["result"]
        except Exception as exc:
            transport.error = str(exc)
            channel.fail(transport.error)
        finally:
            transport.done.set()

    with PROMPT_DISPATCH_LOCK:
        _check_cancelled(task)
        if task.resume_session_id:
            task.session_id = client.load_session(
                task.resume_session_id,
                task.cwd,
            )
        else:
            task.session_id = client.new_session(task.cwd)
        task.channel = SessionEventChannel(
            task.session_id,
            event_callback=event_callback,
        )
        client.configure_session(
            model=task.model,
            reasoning_effort=task.reasoning_effort,
            permission_mode="default",
            session_id=task.session_id,
        )
        now = time.monotonic()
        delay = PROMPT_DISPATCH_INTERVAL_SECONDS - (now - LAST_PROMPT_DISPATCH_AT)
        if delay > 0:
            task.cancel_event.wait(delay)
        _check_cancelled(task)
        LAST_PROMPT_DISPATCH_AT = time.monotonic()
        _transition(task, "running")
        task.started_at = time.time()
        transport.thread = threading.Thread(
            target=send,
            args=(task.channel,),
            name=f"workbuddy-prompt-{task.task_id}",
            daemon=True,
        )
        transport.thread.start()
        prompt_deadline = time.monotonic() + PROMPT_ACCEPTANCE_TIMEOUT_SECONDS
        while not task.channel.wait_for_prompt_start(
            min(0.1, max(0.0, prompt_deadline - time.monotonic())),
            completed_event=transport.done,
        ):
            _check_cancelled(task)
            if transport.done.is_set():
                if transport.error:
                    raise WorkBuddyError(transport.error)
                result = transport.result or {}
                stop_reason = str(result.get("stopReason") or "")
                if stop_reason:
                    break
                raise WorkBuddyError(
                    f"WorkBuddy prompt request finished without session activity: {task.session_id}"
                )
            if time.monotonic() >= prompt_deadline:
                raise WorkBuddyError(
                    f"WorkBuddy did not accept prompt within {PROMPT_ACCEPTANCE_TIMEOUT_SECONDS:.0f}s: {task.session_id}"
                )
    return transport


def _public(task: TaskState, include_answer: bool = True) -> dict[str, Any]:
    with task.condition:
        return _snapshot(task, include_answer)


def _snapshot(task: TaskState, include_answer: bool) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "state": task.state,
        "session_id": task.session_id,
        "cwd": task.cwd,
        "identity": task.identity or None,
        "model": task.model or None,
        "reasoning_effort": task.reasoning_effort or None,
        "resumed": task.resumed,
        "review_target": task.review_target or None,
        "resume_review": task.resume_review,
        "previous_sha256": task.previous_sha256,
        "current_sha256": task.current_sha256,
        "answer": task.answer if include_answer and task.state == "completed" else "",
        "answer_chars": len(task.answer),
        "last_activity": task.last_activity or None,
        "cancel_requested": task.cancel_requested,
        "warning": task.warning,
        "cleanup_error": task.cleanup_error,
        "elapsed_seconds": round((task.finished_at or time.time()) - task.created_at, 2),
        "result": task.result if include_answer and task.state == "completed" else None,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _run(task: TaskState, timeout_seconds: float) -> None:
    log_path = LOG_DIR / f"{task.task_id}.jsonl"
    activity_logger = None

    def log_event(event: dict[str, Any]) -> None:
        activity_logger.feed(event)
        params = event.get("params") or {}
        update = params.get("update") or {} if isinstance(params, dict) else {}
        with task.condition:
            if isinstance(update, dict):
                task.last_activity = str(update.get("sessionUpdate") or task.last_activity)
            task.updated_at = time.time()

    client: AcpClient | None = None
    runtime = None
    continuation_session_lock: threading.Lock | None = None
    continuation_lock_acquired = False
    try:
        _transition(task, "connecting")
        activity_logger = ActivityLogger(log_path, task.cwd, task_id=task.task_id)
        activity_logger.record(
            {
                "activity": "任务已开始",
                "status": "connecting",
            }
        )
        prompt_body = compose_identity_prompt(task.identity, task.prompt)
        if task.resume_session_id:
            with CONTINUATION_SESSION_LOCKS_LOCK:
                continuation_session_lock = CONTINUATION_SESSION_LOCKS.setdefault(
                    task.resume_session_id,
                    threading.Lock(),
                )
            if not continuation_session_lock.acquire(blocking=False):
                raise WorkBuddyError(
                    f"旧会话 {task.resume_session_id} 正在执行另一轮续接任务"
                )
            continuation_lock_acquired = True
            task.resumed = True
            if task.identity in REVIEW_IDENTITIES:
                review_resume = prepare_review_resume(
                    session_id=task.resume_session_id,
                    identity=task.identity,
                    cwd=task.cwd,
                    target=task.review_target,
                )
                task.previous_sha256 = review_resume.previous_sha256
                task.current_sha256 = review_resume.current_sha256
                prompt_body = build_rereview_prompt(review_resume, task.prompt)
        elif task.review_target:
            task.current_sha256 = target_sha256(task.review_target)

        _check_cancelled(task)
        desktop = discover_desktop_server()
        _check_cancelled(task)
        runtime = spawn_isolated_server(
            desktop,
            task.cwd,
            model=task.model or DEFAULT_MODEL,
            session_id=task.resume_session_id,
        )
        _check_cancelled(task)
        client = AcpClient(runtime, timeout_seconds=timeout_seconds)
        client.connect()
        task.client = client
        transport = _dispatch_prompt(
            task,
            client,
            log_event,
            task_prompt(prompt_body, task.cwd),
        )
        deadline = time.monotonic() + timeout_seconds

        _transition(task, "observing")
        stop_reason = ""
        while time.monotonic() < deadline:
            _check_cancelled(task)
            stop_reason = task.channel.wait_for_end(
                min(0.25, max(0.0, deadline - time.monotonic()))
            )
            if stop_reason:
                break
            if transport.done.is_set():
                if transport.error:
                    raise WorkBuddyError(transport.error)
                transport_stop_reason = str(
                    (transport.result or {}).get("stopReason") or ""
                )
                if transport_stop_reason:
                    stop_reason = transport_stop_reason
                    break
        if not stop_reason:
            raise WorkBuddyError(
                f"Timed out waiting for session_end: {task.session_id}"
            )
        if stop_reason == "cancelled":
            task.state = "cancelled"
            task.error = "WorkBuddy cancelled the prompt"
            return
        if stop_reason != "end_turn":
            meta = (transport.result or {}).get('_meta') or {}
            refusal = meta.get('codebuddy.ai/errorMessage') or ''
            if isinstance(refusal, str):
                try:
                    import json
                    parsed = json.loads(refusal)
                    refusal = (parsed.get('data') or {}).get('details') or parsed.get('message') or refusal
                except (ValueError, AttributeError):
                    pass
            reason = str(
                refusal
                or
                (transport.result or {}).get("error")
                or (transport.result or {}).get("message")
                or ""
            ).strip()[:500]
            raise WorkBuddyError(
                f"WorkBuddy session ended with stopReason={stop_reason}"
                + (f": {reason}" if reason else "")
            )

        transport.done.wait(5.0)
        task.result = transport.result or {"transportError": transport.error}
        transport_stop_reason = str((task.result or {}).get("stopReason") or "")
        task.result = {
            **(task.result or {}),
            "stopReason": stop_reason,
            "transportStopReason": transport_stop_reason or None,
            "transportError": transport.error or None,
        }

        title = task.channel.wait_for_title() or str(
            (transport.response or {}).get("title") or ""
        )
        _check_cancelled(task)
        try:
            if not wait_for_task_registration(task.session_id):
                register_completed_session(
                    task.session_id, task.cwd, generated_title=title,
                )
        except Exception as exc:
            # History is ancillary; an already received answer must remain available.
            task.warning = f"Answer received, but history registration failed: {exc}"
        task.answer = str(
            (transport.response or {}).get("answer") or ""
        ) or task.channel.answer()
        if task.review_target and task.identity in REVIEW_IDENTITIES:
            bind_review_session(
                session_id=task.session_id,
                identity=task.identity,
                cwd=task.cwd,
                target=task.review_target,
                baseline_sha256=task.current_sha256
                or target_sha256(task.review_target),
            )
        _transition(task, "completed")
    except Exception as exc:
        with task.condition:
            task.state = "cancelled" if task.cancel_event.is_set() else "failed"
            task.error = "Task cancelled by caller" if task.cancel_event.is_set() else str(exc)
        if activity_logger:
            activity_logger.terminal(
                "任务已取消" if task.cancel_event.is_set() else "任务执行失败",
                status=type(exc).__name__, session_id=task.session_id,
            )
    finally:
        # Terminate the exact runtime before closing a possibly blocked SSE reader.
        cleanup_errors = []
        for action in (
            (lambda: kill_isolated_server(runtime)) if runtime else None,
            client.close if client else None,
            activity_logger.close if activity_logger else None,
        ):
            if action:
                try:
                    action()
                except Exception as exc:
                    cleanup_errors.append(str(exc))
        if continuation_session_lock and continuation_lock_acquired:
            continuation_session_lock.release()
        with task.condition:
            task.cleanup_error = "; ".join(cleanup_errors) or None
            task.client = None
            task.channel = None
            task.updated_at = time.time()
            task.finished_at = task.updated_at
            task.condition.notify_all()



@bridge_tool()
def workbuddy_doctor() -> dict[str, Any]:
    """Check WorkBuddy Desktop's native conversation SDK; a real prompt verifies the account."""
    from workbuddy_bridge.doctor import diagnose
    return diagnose()


@bridge_tool()
def workbuddy_status(task_id: str = "") -> dict[str, Any]:
    """Check WorkBuddy desktop connectivity, or inspect one dispatched task."""
    if task_id:
        with TASKS_LOCK:
            task = TASKS.get(task_id)
        if not task:
            return {"ok": False, "error": f"Unknown task_id: {task_id}"}
        return {"ok": True, **_public(task)}
    try:
        server = discover_desktop_server()
        return {
            "ok": True,
            "connected": True,
            "server_version": VERSION,
            "backend": BACKEND,
            "source_path": str(ROOT),
            "desktop_pid": server.runtime_pid,
            "endpoint": server.acp_endpoint or None,
            "worker_startable": True,
            "account_authentication": "not_checked",
            "sidecar_pid": server.sidecar_pid,
            "host_session_id": server.session_host_id,
            "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
            "queued_tasks": SCHEDULER.queued,
            "max_queued_tasks": MAX_QUEUED_TASKS,
            "event_routing": "native_desktop_conversation",
            "desktop_realtime": True,
            "conversation_mode": "ask",
            "permission_mode": "default",
        }
    except Exception as exc:
        return {"ok": False, "connected": False, "error": str(exc)}


@bridge_tool()
def workbuddy_start(
    prompt: str,
    cwd: str = "",
    timeout_seconds: int = 300,
    model: str = "",
    reasoning_effort: str = "",
    identity: str = "",
    resume_session_id: str = "",
    review_target: str = "",
    resume_review: bool = False,
    request_id: str = "",
) -> dict[str, Any]:
    """Queue one bounded analysis step in WorkBuddy desktop Ask mode; read its answer before the next step.
    Use request_id for safe retries; poll workbuddy_wait. New and resumed conversations use Ask/default permissions.

    identity: online-search, S1 (basic review), S2 (dependencies), S3 (maintainability).
    timeout_seconds: 1..3600 for prompt execution, excluding queue/setup/cleanup.
    Free chat supports resume_session_id. Review resume additionally requires a bound review_target.
    """
    if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 3600:
        return {"ok": False, "code": "INVALID_ARGUMENT", "error": "timeout_seconds must be between 1 and 3600"}
    if len(request_id) > 128:
        return {"ok": False, "code": "INVALID_ARGUMENT", "error": "request_id must be at most 128 characters"}
    working_dir = Path(cwd or Path.cwd()).expanduser().resolve()
    if not working_dir.is_dir():
        return {"ok": False, "error": f"cwd is not a directory: {working_dir}"}
    if not prompt.strip():
        return {"ok": False, "error": "prompt must not be empty"}
    try:
        canonical_identity = normalize_identity(identity)
        canonical_resume_session_id = (
            normalize_session_id(resume_session_id)
            if resume_session_id.strip()
            else ""
        )
        canonical_review_target = (
            normalize_review_target(review_target, str(working_dir))
            if review_target.strip()
            else ""
        )
        if resume_review and canonical_identity not in REVIEW_IDENTITIES:
            raise ValueError("resume_review 只能与 S1、S2、S3 一起使用")
        if resume_review and not canonical_resume_session_id:
            if not canonical_review_target:
                raise ValueError("自动复审时必须提供 review_target")
            canonical_resume_session_id = find_review_session(
                canonical_identity,
                str(working_dir),
                canonical_review_target,
            )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if canonical_resume_session_id:
        if canonical_identity and canonical_identity not in REVIEW_IDENTITIES:
            return {
                "ok": False,
                "error": "只有 S1、S2、S3 支持复用旧审查会话",
            }
        if canonical_identity in REVIEW_IDENTITIES and not canonical_review_target:
            return {
                "ok": False,
                "error": "复用旧审查会话时必须提供 review_target",
            }
    if canonical_review_target and canonical_identity not in REVIEW_IDENTITIES:
        return {
            "ok": False,
            "error": "review_target 只能与 S1、S2、S3 审查身份一起使用",
        }
    task_id = f"wb-{uuid.uuid4().hex[:12]}"
    task = TaskState(
        task_id=task_id,
        prompt=prompt,
        cwd=str(working_dir),
        identity=canonical_identity,
        model=model.strip() or DEFAULT_MODEL,
        reasoning_effort=reasoning_effort.strip(),
        resume_session_id=canonical_resume_session_id,
        review_target=canonical_review_target,
        resume_review=bool(
            canonical_resume_session_id
            and canonical_identity in REVIEW_IDENTITIES
        ),
    )
    task.request_id = request_id
    task.request_signature = (
        prompt, str(working_dir), timeout_seconds, task.model, task.reasoning_effort,
        canonical_identity, resume_session_id, canonical_review_target, resume_review,
    )
    with TASKS_LOCK:
        if request_id:
            previous = next((item for item in TASKS.values() if item.request_id == request_id), None)
            if previous:
                if previous.request_signature != task.request_signature:
                    return {"ok": False, "code": "REQUEST_CONFLICT", "error": "request_id already used with different arguments"}
                return {"ok": True, "deduplicated": True, **_public(previous, False)}
        TASKS[task_id] = task
        if not SCHEDULER.submit(lambda: _run(task, float(timeout_seconds))):
            TASKS.pop(task_id)
            return {"ok": False, "code": "QUEUE_FULL", "error": "WorkBuddy queue is full; wait for an active task before retrying", "retryable": True}
        finished = sorted((item for item in TASKS.values() if item.finished_at is not None), key=lambda item: item.finished_at)
        for old in finished[:-TASK_RETENTION]:
            TASKS.pop(old.task_id, None)
    return {
        "ok": True,
        "task_id": task_id,
        "state": task.state,
        "cwd": task.cwd,
        "resume_session_id": task.resume_session_id or None,
        "review_target": task.review_target or None,
        "resume_review": task.resume_review,
        "model": task.model or None,
        "reasoning_effort": task.reasoning_effort or None,
    }


@bridge_tool()
def workbuddy_wait(task_id: str, timeout_seconds: int = 55, include_answer: bool = True) -> dict[str, Any]:
    """Wait briefly for a WorkBuddy task; returns current state on timeout."""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    with task.condition:
        task.condition.wait_for(
            lambda: task.finished_at is not None,
            timeout=max(0, min(timeout_seconds, 55)),
        )
        return {"ok": True, **_public(task, include_answer)}


@bridge_tool()
def workbuddy_cancel(task_id: str) -> dict[str, Any]:
    """Request cancellation of a running WorkBuddy task."""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    with task.condition:
        if task.state in TERMINAL_STATES:
            return {"ok": True, **_public(task)}
        task.cancel_requested = True
        task.cancel_event.set()
        # The worker owns transport teardown. Cancellation must never wait on I/O.
        if task.state == "queued":
            task.state = "cancelled"
            task.finished_at = time.time()
        else:
            task.state = "cancelling"
            if task.channel:
                task.channel.fail("Task cancelled by caller")
        task.updated_at = time.time()
        task.condition.notify_all()
        return {"ok": True, **_public(task)}


@bridge_tool()
def workbuddy_list(state: str = "", limit: int = 20, offset: int = 0, include_answer: bool = False) -> dict[str, Any]:
    """List newest tasks first, with pagination. Answers are omitted by default."""
    if not 1 <= limit <= 100 or offset < 0:
        return {"ok": False, "code": "INVALID_ARGUMENT", "error": "limit must be 1..100 and offset must be nonnegative"}
    with TASKS_LOCK:
        tasks = sorted(TASKS.values(), key=lambda task: task.created_at, reverse=True)
        rows = [_public(task, include_answer) for task in tasks]
    if state:
        rows = [row for row in rows if row["state"] == state]
    end = offset + limit
    return {"ok": True, "tasks": rows[offset:end], "total": len(rows), "next_offset": end if end < len(rows) else None}


@bridge_tool()
def workbuddy_result(task_id: str, offset: int = 0, limit: int = 12000) -> dict[str, Any]:
    """Read a completed answer in character pages without repeatedly returning it all."""
    if offset < 0 or not 1 <= limit <= 50000:
        return {"ok": False, "code": "INVALID_ARGUMENT", "error": "offset must be nonnegative and limit must be 1..50000"}
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return {"ok": False, "error": f"Unknown task_id: {task_id}"}
    with task.condition:
        text = task.answer if task.state == "completed" else ""
        end = offset + limit
        return {"ok": True, "task_id": task_id, "state": task.state, "answer": text[offset:end], "total_chars": len(text), "next_offset": end if end < len(text) else None, "error": task.error}


if __name__ == "__main__":
    mcp.run(transport="stdio")
