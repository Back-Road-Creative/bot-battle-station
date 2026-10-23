<#
.SYNOPSIS
  The session board on a wall screen. Starts bin/session-board.py in WSL if it is not answering, then
  opens it as a full-screen app window on the spare monitor, with sound allowed so the siren needs no click.
.EXAMPLE
  Once, from WSL in the repo (copies itself to %LOCALAPPDATA%\SessionBoard and runs at every Windows login):
    powershell.exe -ExecutionPolicy RemoteSigned -File "$(wslpath -w bin/session-board-kiosk.ps1)" -Install -Repo "$PWD"
#>
param(
  [switch]$Install,                       # also run at every Windows login
  [switch]$Uninstall,                     # remove the startup shortcut and installed copy
  [int]$Port = 8765,
  [ValidatePattern('^[A-Za-z0-9_.-]*$')]
  [string]$Distro = "",                   # WSL distro; empty means the default one
  [ValidatePattern('^[A-Za-z0-9_.-]*$')]
  [string]$User = "",                     # the WSL user whose sessions the board shows; empty means the distro's default
  [ValidatePattern('^[A-Za-z0-9_./~+-]+$')]
  [string]$Repo = "~",                    # the WSL folder the command runs in: the checkout holding bin/session-board.py
  [ValidatePattern('^[A-Za-z0-9._/=-]+(?: +[A-Za-z0-9._/=-]+)*$')]
  [string]$Command = "python3 bin/session-board.py",  # how to start the board there (--port is added)
  [int]$Monitor = -1                      # screen index; -1 means the first non-primary screen, else the primary
)
$ErrorActionPreference = "Stop"
$state = Join-Path $env:LOCALAPPDATA "SessionBoard"
$url = "http://localhost:$Port/"
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "Session Board.lnk"
if ($Uninstall) {
  Remove-Item -LiteralPath $lnk -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path $state "session-board-kiosk.ps1") -Force -ErrorAction SilentlyContinue
  Write-Output "removed startup shortcut and installed script"
  return
}
New-Item -ItemType Directory -Force -Path $state | Out-Null

if ($Install) {
  $copy = Join-Path $state "session-board-kiosk.ps1"   # Windows cannot start a login task from a WSL path
  Copy-Item -Force $PSCommandPath $copy
  $flags = "-NoProfile -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File `"$copy`" -Port $Port -Repo `"$Repo`" -Command `"$Command`" -Monitor $Monitor"
  if ($Distro) { $flags += " -Distro $Distro" }
  if ($User) { $flags += " -User $User" }
  $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $sc.TargetPath = "powershell.exe"; $sc.Arguments = $flags; $sc.WindowStyle = 7; $sc.Save()
  Write-Output "runs at login: $lnk"
}

function Test-Board {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "${url}api"
    $payload = $response.Content | ConvertFrom-Json
    return ($payload.api -eq 1 -and $null -ne $payload.sessions)
  } catch { return $false }
}

if (-not (Test-Board)) {
  $wsl = @()
  if ($Distro) { $wsl += @("-d", $Distro) }
  if ($User) { $wsl += @("-u", $User) }
  [string[]]$boardArgs = @([regex]::Matches($Command, '[A-Za-z0-9._/=-]+') | ForEach-Object { $_.Value })
  $wsl += @("--cd", $Repo, "--") + $boardArgs + @("--port", "$Port")
  Start-Process -WindowStyle Hidden wsl.exe -ArgumentList $wsl   # stays running, which keeps WSL up
  for ($i = 0; $i -lt 30 -and -not (Test-Board); $i++) { Start-Sleep -Seconds 1 }
  if (-not (Test-Board)) { Write-Warning "the board did not answer on $url; does '$Command' start it in $Repo?" }
}

Add-Type -AssemblyName System.Windows.Forms
$screens = [System.Windows.Forms.Screen]::AllScreens
$screen = if ($Monitor -ge 0 -and $Monitor -lt $screens.Count) { $screens[$Monitor] }
          else { $screens | Where-Object { -not $_.Primary } | Select-Object -First 1 }
if (-not $screen) { $screen = [System.Windows.Forms.Screen]::PrimaryScreen }
$b = $screen.Bounds
$browser = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
             "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
             "$env:ProgramFiles\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "no Edge or Chrome found" }
# its own profile: remembers ARM SIREN and the notification permission, and opens a new window where asked
Start-Process $browser -ArgumentList @("--app=$url", "--user-data-dir=`"$state\browser`"", "--no-first-run",
  "--window-position=$($b.X),$($b.Y)", "--window-size=$($b.Width),$($b.Height)", "--start-fullscreen",
  "--autoplay-policy=no-user-gesture-required")
