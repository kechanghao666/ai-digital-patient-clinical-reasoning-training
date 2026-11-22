# Restart the local account preview (API 8006 + UI 8508) so code and case changes take effect.
# Keeps the existing preview data directory (accounts and trainings are not touched).
# Usage, from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\restart_account_preview.ps1
param(
    [string]$DataDir = (Join-Path $PSScriptRoot '..\local-only\account-preview-20261113\data'),
    [int]$ApiPort = 8006,
    [int]$UiPort = 8508
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "未找到 $Python，请先按 README 创建 .venv" }
$DataDir = (Resolve-Path $DataDir).Path
$LogDir = Split-Path $DataDir -Parent

foreach ($port in @($ApiPort, $UiPort)) {
    $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $owners) {
        Write-Host "停止占用端口 $port 的进程 $procId"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 1

$env:APP_ENV = 'development'
$env:APP_AUTH_MODE = 'local'
$env:NEXENT_MODE = 'simulator'
$env:APP_DATA_DIR = $DataDir
$env:FRONTEND_API_URL = "http://127.0.0.1:$ApiPort"

$api = Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', "$ApiPort") `
    -RedirectStandardOutput (Join-Path $LogDir 'api.stdout.log') -RedirectStandardError (Join-Path $LogDir 'api.stderr.log')
$ui = Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -ArgumentList @('-m', 'streamlit', 'run', 'frontend/app.py', '--server.address', '127.0.0.1', '--server.port', "$UiPort", '--server.headless', 'true') `
    -RedirectStandardOutput (Join-Path $LogDir 'ui.stdout.log') -RedirectStandardError (Join-Path $LogDir 'ui.stderr.log')

$healthy = $false
for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
    Start-Sleep -Seconds 1
    try { $healthy = (Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2).status -eq 'ok' } catch { }
}
if (-not $healthy) { throw "API 未在 30 秒内就绪，请查看 $LogDir\api.stderr.log" }
Write-Host "API 进程 $($api.Id) 已就绪：http://127.0.0.1:$ApiPort/health"
Write-Host "新版界面：请在浏览器访问 http://127.0.0.1:$ApiPort/ （由 API 直接托管）"
Write-Host "旧版 Streamlit 备用界面进程 $($ui.Id)：http://127.0.0.1:$UiPort"
