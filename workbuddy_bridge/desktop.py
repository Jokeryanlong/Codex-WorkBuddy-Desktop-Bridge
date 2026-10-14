"""Authenticated WorkBuddy Desktop runtimes, independent of sidecar lifecycle."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time

import httpx
from workbuddy_bridge.acp import DesktopServer, WorkBuddyError, REQUEST_HEADER, _process_executable, _free_local_port

VERSION = '0.3.0'
BACKEND = 'workbuddy-desktop-authenticated-runtime'
_workers: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def profile_dir() -> Path:
    return Path(os.environ.get('WORKBUDDY_CONFIG_DIR') or os.environ.get('CODEBUDDY_CONFIG_DIR') or Path.home()/'.workbuddy').expanduser().resolve()


def discover_desktop_server() -> DesktopServer:
    if os.name != 'nt':
        raise WorkBuddyError('WorkBuddy Desktop bridge requires Windows')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    pids: set[int] = set()

    @callback_type
    def collect(hwnd, unused):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(collect, 0)
    for pid in sorted(pids):
        try:
            exe = _process_executable(pid)
        except WorkBuddyError:
            continue
        if exe.name.lower() != 'workbuddy.exe':
            continue
        if not (exe.parent/'resources/app.asar.unpacked/cli/bin/codebuddy').is_file():
            continue
        if not (profile_dir()/'cache/acc-product-config-v3.json').is_file():
            raise WorkBuddyError('WorkBuddy desktop product configuration is missing; wait for the desktop to finish loading')
        return DesktopServer('', '', 'desktop', '', 0, pid)
    raise WorkBuddyError('WorkBuddy Desktop has no open window; open the desktop app and sign in')


def _runtime_env(password: str = '', port: int = 0) -> dict[str, str]:
    env = dict(os.environ)
    # Do not inherit a different CLI account or custom endpoint from Codex.
    for key in ('CODEBUDDY_AUTH_TOKEN', 'CODEBUDDY_API_KEY', 'CODEBUDDY_CUSTOM_HEADERS', 'CODEBUDDY_BASE_URL'):
        env.pop(key, None)
    env.update(ELECTRON_RUN_AS_NODE='1', CODEBUDDY_DISABLE_IDE='1',
               WORKBUDDY_CONFIG_DIR=str(profile_dir()), CODEBUDDY_CONFIG_DIR=str(profile_dir()),
               CODEBUDDY_CODE_DISABLE_TERMINAL_TITLE='1', SERVER__HOST='127.0.0.1', SERVER__PORT=str(port),
               CODEBUDDY_GATEWAY_AUTH='password', CODEBUDDY_GATEWAY_PASSWORD=password)
    return env


def account_status(desktop: DesktopServer, timeout_seconds: float = 15) -> dict:
    exe = _process_executable(desktop.runtime_pid)
    try:
        result = subprocess.run([str(exe), str(Path(__file__).with_name('desktop_runtime.cjs')), '--bridge-auth-check'],
                                env=_runtime_env(), capture_output=True, text=True, encoding='utf8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise WorkBuddyError('Desktop authentication check timed out') from exc
    for line in result.stdout.splitlines():
        if line.startswith('WB_AUTH_CHECK:'):
            return json.loads(line[len('WB_AUTH_CHECK:'):])
    safe_error = next((line.partition('WORKBUDDY_DESKTOP_AUTH_ERROR:')[2].strip() for line in result.stderr.splitlines()
                       if 'WORKBUDDY_DESKTOP_AUTH_ERROR:' in line), '')
    raise WorkBuddyError(safe_error or f'Desktop authentication adapter failed (exit {result.returncode})')


def spawn_isolated_server(desktop: DesktopServer, cwd: str, *, model: str = 'deepseek-v4.1-flash',
                          startup_timeout_seconds: float = 30, session_id: str = '') -> DesktopServer:
    exe = _process_executable(desktop.runtime_pid)
    password = secrets.token_urlsafe(32)
    port = _free_local_port()
    runtime_id = 'codex-worker-' + secrets.token_hex(6)
    args = [str(exe), str(Path(__file__).with_name('desktop_runtime.cjs')), '--serve', '--setting-sources', 'user',
            '--permission-mode', 'fullAccess', '--permission-mode-before-plan', 'bypassPermissions', '--model', model]
    if session_id:
        args.extend(['--session-id', session_id])
    proc = subprocess.Popen(args, cwd=cwd, env=_runtime_env(password, port), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW)
    with _lock:
        _workers[runtime_id] = proc
    errors: list[str] = []

    def drain():
        try:
            for line in proc.stdout:
                if 'WORKBUDDY_DESKTOP_AUTH_ERROR:' in line:
                    errors.append(line.partition('WORKBUDDY_DESKTOP_AUTH_ERROR:')[2].strip()[:500])
        finally:
            proc.stdout.close()

    threading.Thread(target=drain, daemon=True, name=runtime_id+'-output').start()
    runtime = DesktopServer(f'http://127.0.0.1:{port}/api/v1/acp', password, runtime_id, '', 0, proc.pid)
    deadline = time.monotonic() + startup_timeout_seconds
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise WorkBuddyError(errors[0] if errors else f'WorkBuddy runtime exited during startup ({proc.returncode})')
                try:
                    r = client.get(runtime.base_url+'/api/v1/auth/status', headers={**REQUEST_HEADER, 'Authorization':'Bearer '+password})
                    if r.is_success and r.json().get('authenticated'):
                        return runtime
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(.1)
        raise WorkBuddyError('Authenticated WorkBuddy runtime did not become ready within the startup deadline')
    except Exception:
        kill_isolated_server(runtime)
        raise


def kill_isolated_server(runtime: DesktopServer) -> None:
    with _lock:
        proc = _workers.pop(runtime.session_host_id, None)
    if proc is None:
        return
    if proc.poll() is None:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    proc.wait(timeout=5)
