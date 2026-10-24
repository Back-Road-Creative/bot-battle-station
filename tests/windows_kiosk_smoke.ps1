# Exercise the real Windows install and uninstall paths without requiring WSL or
# reading a developer's sessions. The local API stub makes the launcher skip WSL.
$ErrorActionPreference = 'Stop'
$port = 18765
$launcher = Join-Path $PSScriptRoot '..\bin\session-board-kiosk.ps1'
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Session Board.lnk'
$installed = Join-Path (Join-Path $env:LOCALAPPDATA 'SessionBoard') 'session-board-kiosk.ps1'
$stub = Start-Process python -ArgumentList @('-u', (Join-Path $PSScriptRoot 'kiosk_api_stub.py'), "$port") -PassThru -WindowStyle Hidden

try {
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/api"
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready) { throw 'local API stub did not start' }
    $board = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/api").Content | ConvertFrom-Json
    if (-not ($board.api -eq 1 -and $null -ne $board.sessions)) {
        throw 'local API stub does not satisfy the launcher board check'
    }
    $probe = @'
$url = 'http://127.0.0.1:18765/'
try {
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "${url}api"
    $payload = $response.Content | ConvertFrom-Json
    if (-not ($payload.api -eq 1 -and $null -ne $payload.sessions)) { exit 5 }
} catch {
    Write-Output "child board probe failed: $($_.Exception.Message)"
    exit 6
}
'@
    $probeOutput = & powershell.exe -NoProfile -Command $probe 2>&1
    if ($LASTEXITCODE -ne 0) { throw "child board probe exited $LASTEXITCODE`: $probeOutput" }

    $output = & powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File $launcher -Install -Port $port -Repo '~' 2>&1
    if ($LASTEXITCODE -ne 0) { throw "kiosk install exited $LASTEXITCODE" }
    if ($output -match 'board did not answer') { throw 'launcher did not accept the local board API' }
    if (-not (Test-Path -LiteralPath $startup)) { throw 'startup shortcut was not created' }
    if (-not (Test-Path -LiteralPath $installed)) { throw 'installed script was not copied' }
    if ((Get-FileHash $launcher).Hash -ne (Get-FileHash $installed).Hash) {
        throw 'installed script differs from the source'
    }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($startup)
    if ($shortcut.TargetPath -notlike '*powershell.exe' -or $shortcut.Arguments -notmatch '-Port 18765') {
        throw 'startup shortcut does not launch this board'
    }
} finally {
    & powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File $launcher -Uninstall
    if ($LASTEXITCODE -ne 0) { throw "kiosk uninstall exited $LASTEXITCODE" }
    Stop-Process -Id $stub.Id -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $startup) { throw 'startup shortcut remained after uninstall' }
if (Test-Path -LiteralPath $installed) { throw 'installed script remained after uninstall' }
