# OctoAssist - undo the KPI-card sparkline hide (TEMA India, Aug 2026)
# Restores the newest octonospark backup of styles.css. No restart needed.

$roots = @(
  'C:\Program Files\Third Octopus\OctoAssist Server'
  'C:\Program Files\OctoAssist\server'
  'C:\octoassist\server'
)
$css = $null
foreach ($r in $roots) {
  $p = Join-Path $r 'app\static\styles.css'
  if (Test-Path -LiteralPath $p) { $css = $p; break }
}
if (-not $css) { Write-Host 'NOT FOUND: styles.css - set $css by hand' -ForegroundColor Red; return }

$bak = Get-ChildItem -LiteralPath (Split-Path $css) -Filter 'styles.css.octonospark-*.bak' -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $bak) { Write-Host 'No octonospark backup found next to styles.css.' -ForegroundColor Red; return }

Copy-Item -LiteralPath $bak.FullName -Destination $css -Force
Write-Host "Rolled back from $($bak.Name)" -ForegroundColor Yellow
Write-Host "size now : $((Get-Item -LiteralPath $css).Length) bytes"
Write-Host 'Hard-refresh the browser with Ctrl-Shift-R.'
