param([string]$WorkBuddyPath = '', [string]$PythonCommand = 'python', [int]$Port = 18794)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $WorkBuddyPath) {
        $WorkBuddyPath = Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and (Test-Path -LiteralPath $_.Path) } |
            Select-Object -First 1 -ExpandProperty Path
    }
    if (-not $WorkBuddyPath -or -not (Test-Path -LiteralPath $WorkBuddyPath -PathType Leaf)) {
        throw 'Open WorkBuddy desktop first, or specify -WorkBuddyPath "D:\Apps\WorkBuddy\WorkBuddy.exe".'
    }
    if ([IO.Path]::GetFileName($WorkBuddyPath) -ine 'WorkBuddy.exe') { throw 'Select WorkBuddy.exe, not CodeBuddy CLI.' }
    if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid port.' }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $PythonCommand -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.10 or newer.' }
        & $PythonCommand -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Install Python 3.10 or newer.' }
    }
    & '.\.venv\Scripts\python.exe' -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & '.\.venv\Scripts\python.exe' '.\install_codex.py' --workbuddy $WorkBuddyPath --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Codex registration failed.' }
    Write-Host 'Setup complete. Use the generated desktop shortcut and restart Codex. Run doctor.cmd to diagnose.'
} finally {
    Pop-Location
}
