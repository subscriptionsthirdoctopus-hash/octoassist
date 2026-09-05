# OctoAssist - collect the source files needed to write the item 1 / item 6
# patches for TEMA India. (Aug 2026)
#
# READ ONLY. This script copies files and computes hashes. It does not modify
# the application, the database, or the service. Nothing here needs elevation.
#
# Why it exists: TEMA's production build is ahead of every branch in the repo,
# so a patch written blind against the repo will not apply cleanly to their
# files. The tailnet client on this box is offline, so the files cannot be
# fetched over SSH. Run this over RDP and send back the ZIP it produces.

$root = 'C:\Program Files\Third Octopus\OctoAssist Server\app'
$stamp = Get-Date -Format yyyyMMdd-HHmmss
$dest  = "$env:USERPROFILE\Desktop\octoassist-src-$stamp"

if (-not (Test-Path -LiteralPath $root)) {
  Write-Host "NOT FOUND: $root" -ForegroundColor Red
  Write-Host 'Check the install path and edit $root at the top of this script.' -ForegroundColor Yellow
  return
}

# Exactly the files the two patches touch - nothing else is collected.
$wanted = @(
  'web\views_tickets.py',
  'web\views_reports.py',
  'services\reporting.py',
  'services\ticketing.py',
  'templates\ticket_detail.html',
  'templates\reports_home.html',
  'templates\report_sla.html'
)

New-Item -ItemType Directory -Path $dest -Force | Out-Null
$manifest = @()
$missing  = @()

foreach ($rel in $wanted) {
  $src = Join-Path $root $rel
  if (-not (Test-Path -LiteralPath $src)) { $missing += $rel; continue }

  # Flatten into one folder, keeping the original location in the name so the
  # files cannot be confused with each other on the way back.
  $flat = $rel -replace '\\', '__'
  Copy-Item -LiteralPath $src -Destination (Join-Path $dest $flat) -Force

  $item = Get-Item -LiteralPath $src
  $manifest += [pscustomobject]@{
    Path     = $rel
    Bytes    = $item.Length
    Modified = $item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    SHA256   = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
  }
}

$manifest | Format-Table -AutoSize | Out-String -Width 200 |
  Set-Content -LiteralPath (Join-Path $dest 'MANIFEST.txt') -Encoding ASCII

# Which marked patches are already on this box - tells us what has been applied
# by hand previously, so a new patch does not collide with one of them.
$markers = @('OCTO-BRANDSIZE','OCTO-NOSPARK','OCTO-MASTERS','OCTO-ASSETREC','OCTO-SAMURL')
$found = foreach ($m in $markers) {
  $hits = (Select-String -Path "$root\*.py","$root\**\*.py","$root\static\styles.css" `
                         -SimpleMatch $m -ErrorAction SilentlyContinue).Count
  "{0,-16} {1}" -f $m, $hits
}
$found | Add-Content -LiteralPath (Join-Path $dest 'MANIFEST.txt') -Encoding ASCII

$zip = "$dest.zip"
Compress-Archive -Path "$dest\*" -DestinationPath $zip -Force

Write-Host ''
Write-Host 'Collected (read-only, nothing was modified):' -ForegroundColor Green
$manifest | Format-Table -AutoSize
if ($missing.Count) {
  Write-Host 'NOT FOUND on this box (their build may name these differently):' -ForegroundColor Yellow
  $missing | ForEach-Object { Write-Host "  $_" }
}
Write-Host ''
Write-Host "ZIP ready: $zip" -ForegroundColor Cyan
Write-Host 'Send that file back. It contains source and templates only - no'
Write-Host 'database contents, no .env, no credentials.'
