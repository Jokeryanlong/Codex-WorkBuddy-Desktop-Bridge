"""Register the extracted package while preserving unrelated Codex settings."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomlkit

def replace_server(text, block):
    document = tomlkit.parse(text)
    if 'mcp_servers' not in document:
        document['mcp_servers'] = tomlkit.table()
    document['mcp_servers']['workbuddy_worker'] = tomlkit.parse(block)['mcp_servers']['workbuddy_worker']
    result = tomlkit.dumps(document)
    tomlkit.parse(result)
    return result

def make_block(root, python, workbuddy, port):
    q = lambda x: json.dumps(str(x), ensure_ascii=False)
    return (f'[mcp_servers.workbuddy_worker]\ncommand = {q(python)}\n'
        f'args = ["-m", "workbuddy_bridge", "serve"]\ncwd = {q(root)}\n'
        'enabled = true\nstartup_timeout_sec = 30\n[mcp_servers.workbuddy_worker.env]\n'
        f'PYTHONUTF8 = "1"\nWORKBUDDY_NATIVE_PORT = "{port}"\nWORKBUDDY_DESKTOP_EXE = {q(workbuddy)}\n')

def install(config, root, python, workbuddy, port):
    existed = config.exists()
    before = config.read_bytes() if existed else b''
    original = before.decode('utf-8-sig')
    after = replace_server(original, make_block(root, python, workbuddy, port)).encode('utf-8')
    backup = root / '.install-backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns()))
    backup.mkdir(parents=True)
    (backup / 'BASELINE.toml').write_bytes(before)
    (backup / 'MODIFIED.toml').write_bytes(after)
    (backup / 'DIFF.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),
        after.decode().splitlines(True), fromfile='BASELINE.toml', tofile='MODIFIED.toml')), encoding='utf-8')
    rollback = ('import hashlib\nfrom pathlib import Path\n' + f'p=Path({str(config)!r})\n'
        + 'b=Path(__file__).with_name("BASELINE.toml")\n'
        + f'expected={hashlib.sha256(after).hexdigest()!r}\n'
        + 'if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:\n'
        + '    raise SystemExit("Config changed after install. Restore only the WorkBuddy stanza manually.")\n')
    rollback += 'p.write_bytes(b.read_bytes())\n' if existed else 'p.unlink()\n'
    (backup / 'rollback.py').write_text(rollback, encoding='utf-8')
    (backup / 'ROLLBACK.ps1').write_text("& '" + str(python).replace("'", "''")
        + "' (Join-Path $PSScriptRoot 'rollback.py')\nexit $LASTEXITCODE\n", encoding='utf-8-sig')
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_name(config.name + '.workbuddy-install.tmp')
    with temporary.open('xb') as stream:
        stream.write(after)
    os.replace(temporary, config)
    if config.read_bytes() != after:
        raise RuntimeError('Configuration readback failed')
    (backup / 'VERIFICATION.txt').write_text(f'config={config}\nsource={root}\n'
        f'baseline_sha256={hashlib.sha256(before).hexdigest()}\n'
        f'modified_sha256={hashlib.sha256(after).hexdigest()}\nreadback=PASS\n', encoding='utf-8')
    return backup

def shortcut(root, pythonw, workbuddy, port, destination=None):
    payload = json.dumps({'pythonw': str(pythonw), 'root': str(root), 'workbuddy': str(workbuddy),
        'destination': str(destination) if destination else '',
        'arguments': subprocess.list2cmdline([str(root/'workbuddy_bridge'/'launch_workbuddy.py'),
        '--exe', str(workbuddy), '--port', str(port)])})
    script = '''$ErrorActionPreference='Stop'
$p=[Console]::In.ReadToEnd() | ConvertFrom-Json
$desktop=[Environment]::GetFolderPath('Desktop')
if ($p.destination) { $desktop=$p.destination }
$shell=New-Object -ComObject WScript.Shell
$link=$shell.CreateShortcut((Join-Path $desktop 'WorkBuddy - Codex Native Chat.lnk'))
$link.TargetPath=$p.pythonw
$link.Arguments=$p.arguments
$link.WorkingDirectory=$p.root
$link.IconLocation=$p.workbuddy
$link.Save()
'''
    result = subprocess.run(['powershell','-NoProfile','-Command',script], input=payload,
        text=True, capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError('Shortcut creation failed: ' + result.stderr)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbuddy', required=True)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'config.toml')
    parser.add_argument('--port', type=int, default=18794)
    parser.add_argument('--no-shortcut', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    workbuddy = Path(args.workbuddy).resolve()
    if not workbuddy.is_file() or workbuddy.name.lower() != 'workbuddy.exe':
        parser.error('--workbuddy must be the installed WorkBuddy.exe')
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    python = Path(sys.executable).resolve()
    pythonw = python.with_name('pythonw.exe')
    if not args.no_shortcut:
        if not pythonw.is_file():
            parser.error('pythonw.exe missing; use Windows Python')
        shortcut(root, pythonw, workbuddy, args.port)
    backup = install(args.config.resolve(), root, python, workbuddy, args.port)
    print('Codex MCP registered: ' + str(args.config))
    print('Backup and rollback: ' + str(backup))
    print('Fully quit WorkBuddy, use WorkBuddy - Codex Native Chat, then restart Codex.')

if __name__ == '__main__':
    main()
