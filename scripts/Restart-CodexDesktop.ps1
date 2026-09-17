[CmdletBinding()]
param(
    [int]$ShutdownTimeoutSeconds = 20
)

$ErrorActionPreference = 'Stop'

# This is the currently installed Codex desktop executable. If Codex is later
# updated, the script also falls back to the running process path.
$knownExecutable = 'C:\Program Files\WindowsApps\OpenAI.Codex_26.818.3698.0_x64__2p2nqsd0c76g0\app\resources\codex.exe'
$runningCodex = @(Get-Process -Name 'Codex' -ErrorAction SilentlyContinue)
$runningPath = $runningCodex |
    Where-Object { $_.Path -and (Test-Path -LiteralPath $_.Path) } |
    Select-Object -First 1 -ExpandProperty Path

$codexExecutable = if ($runningPath) { $runningPath } else { $knownExecutable }
if (-not (Test-Path -LiteralPath $codexExecutable)) {
    throw "Codex executable was not found: $codexExecutable"
}

foreach ($process in $runningCodex) {
    Stop-Process -Id $process.Id -Force -ErrorAction Stop
}

$deadline = (Get-Date).AddSeconds([Math]::Max(1, $ShutdownTimeoutSeconds))
while ((Get-Process -Name 'Codex' -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
}

if (Get-Process -Name 'Codex' -ErrorAction SilentlyContinue) {
    throw "Codex did not exit within $ShutdownTimeoutSeconds seconds."
}

Start-Process -FilePath $codexExecutable
Write-Output 'Codex desktop was restarted.'
