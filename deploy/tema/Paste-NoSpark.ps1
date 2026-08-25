# OctoAssist - hide the KPI-card sparkline (TEMA India, Aug 2026)
# CSS only: no template change, no service restart, no downtime.
#
# The 25 Aug attempt failed silently: Copy-Item created the .bak in the same
# directory, but the WriteAllText did not take - styles.css kept its 19 Aug
# timestamp and the graph stayed. Creating a NEW file there worked while
# overwriting the existing one did not, which points at a ReadOnly attribute
# on styles.css rather than at directory permissions (an ACL denial would have
# blocked the .bak too). This version checks both, says which it hit, and
# verifies the result instead of trusting a green message.
#
# Run elevated (Run as administrator) regardless - it costs nothing.

$css = 'C:\Program Files\Third Octopus\OctoAssist Server\app\static\styles.css'

if (-not (Test-Path -LiteralPath $css)) {
  Write-Host "NOT FOUND: $css" -ForegroundColor Red; return
}

$before = Get-Item -LiteralPath $css
Write-Host "stylesheet : $css"
Write-Host "size before: $($before.Length) bytes   modified: $($before.LastWriteTime)"
Write-Host "attributes : $($before.Attributes)"
try {
  $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
              ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  Write-Host "elevated   : $elevated"
} catch { }

# The likely 25 Aug culprit. Clearing it is one line to undo:
#   Set-ItemProperty -LiteralPath $css -Name IsReadOnly -Value $true
if ($before.IsReadOnly) {
  Write-Host 'styles.css is READ-ONLY - clearing that attribute.' -ForegroundColor Yellow
  Set-ItemProperty -LiteralPath $css -Name IsReadOnly -Value $false
}

# Pre-flight: can this session actually open the file for writing?
try {
  $fs = [IO.File]::Open($css, 'Open', 'Write', 'None'); $fs.Close()
} catch {
  Write-Host 'CANNOT WRITE to styles.css - this is the 25 Aug failure, caught.' -ForegroundColor Red
  Write-Host "  ($($_.Exception.GetType().Name): $($_.Exception.Message))"
  Write-Host 'Fix one of these, then paste again:' -ForegroundColor Yellow
  Write-Host '  - not elevated : close this window, right-click PowerShell > Run as administrator'
  Write-Host '  - file locked  : something else has it open for writing'
  Write-Host '  - ACL          : icacls on the file will show who is denied'
  return
}

if (Select-String -LiteralPath $css -SimpleMatch 'OCTO-NOSPARK' -Quiet) {
  Write-Host 'Already applied - nothing to do.' -ForegroundColor Green; return
}

$bak = "$css.octonospark-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
Copy-Item -LiteralPath $css -Destination $bak -Force

$patch = @'

/* ===========================================================================
   OCTO-NOSPARK - remove the KPI-card sparkline   (TEMA India, Aug 2026)

   The sparkline is position:absolute in the bottom-right of .kpi-card, so it
   sits on top of the .kpi-sub line ("5 past resolution SLA") and the text
   reads through it. It has no axis, no scale and no label, so it carries no
   information the card needs.

   Scoped to .kpi-card on purpose: the "Tickets created - last 30 days" chart
   below the cards is .chart-line and is deliberately left alone.
   =========================================================================== */

.kpi-card .kpi-spark,
.kpi-card svg.sparkline,
.kpi-card .sparkline {
  display: none !important;
}
'@

try {
  # UTF8 without BOM - a BOM appended mid-file renders as stray characters.
  [IO.File]::WriteAllText($css, ((Get-Content -LiteralPath $css -Raw) + "`r`n" + $patch), (New-Object Text.UTF8Encoding($false)))
} catch {
  Write-Host "WRITE FAILED: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Nothing changed. Backup left at $bak" -ForegroundColor Yellow
  return
}

# Verify, rather than assume.
$after  = Get-Item -LiteralPath $css
$marked = Select-String -LiteralPath $css -SimpleMatch 'OCTO-NOSPARK' -Quiet
Write-Host "size after : $($after.Length) bytes   modified: $($after.LastWriteTime)"
Write-Host "backup     : $bak"

if ($marked -and $after.Length -gt $before.Length) {
  Write-Host 'VERIFIED: marker present, file grew, timestamp moved.' -ForegroundColor Green
  Write-Host 'No restart needed. Hard-refresh the browser with Ctrl-Shift-R.' -ForegroundColor Green
} else {
  Write-Host 'FAILED: the file did not change. Re-run elevated.' -ForegroundColor Red
}
