$ErrorActionPreference = "Stop"
$pluginsDir = "C:\Users\Frank\.gemini\config\plugins"
$logPath = Join-Path (Split-Path $PSScriptRoot -Parent) "artifacts\remove-antigravity-telemetry.log"

Start-Transcript -LiteralPath $logPath -Force | Out-Null

try {
    $targets = Get-ChildItem -Path $pluginsDir -Filter "*datacloud_telemetry*" -Directory -ErrorAction SilentlyContinue

    if ($targets) {
        foreach ($t in $targets) {
            Write-Host "Deleting: $($t.FullName)" -ForegroundColor Yellow
            Remove-Item -LiteralPath $t.FullName -Recurse -Force -ErrorAction Stop
        }
        Write-Host "SUCCESS: All datacloud_telemetry folders deleted!" -ForegroundColor Green
    } else {
        Write-Host "No datacloud_telemetry folders found in $pluginsDir." -ForegroundColor Cyan
    }

    # Verify plugins directory contents:
    Get-ChildItem -Path $pluginsDir | Select-Object Name
} finally {
    Stop-Transcript | Out-Null
}
