"""Read-only diagnostics; never starts a Worker or sends a model prompt."""
from __future__ import annotations

import json
import platform
import sys
from importlib.metadata import version


def diagnose(timeout_seconds: float = 15) -> dict:
    info = {"python": platform.python_version(), "platform": platform.system()}
    for package in ("mcp", "httpx"):
        try:
            info[package] = version(package)
        except Exception:
            info[package] = "not installed"
    if sys.platform != "win32":
        return {"ok": False, "code": "UNSUPPORTED_PLATFORM", **info,
                "next_action": "Run this desktop bridge on Windows with WorkBuddy open."}
    # Probe in-process so the MCP server and its child do not race sidecar
    # ownership or inherit a different temporary-directory configuration.
    return {**info, **probe()}


def probe() -> dict:
    from workbuddy_bridge.native import discover_desktop_server, call, VERSION, BACKEND
    try:
        server = discover_desktop_server()
        native = call('probe')
        return {"ok": True, "connected": True, "server_version":VERSION, "backend":BACKEND,
                "desktop_pid": server.runtime_pid,
                "worker_startable": True,
                "native_sdk_ready": native.get('ready',False),
                "desktop_realtime": True,
                "account_authentication": "not_checked",
                "next_action": "Native desktop conversation SDK is ready. Submit a task to verify its reply in WorkBuddy."}
    except Exception as exc:
        return {"ok": False, "connected": False, "code": "DESKTOP_UNAVAILABLE",
                "error": str(exc), "next_action": "Start WorkBuddy with its native automation launcher, sign in, and keep the desktop open."}


if __name__ == "__main__":
    result = probe() if "--probe" in sys.argv else diagnose()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
