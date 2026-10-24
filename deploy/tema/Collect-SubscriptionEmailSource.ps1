# OctoAssist - collect the source behind TEMA's "[OctoAssist] Software
# subscription" reminder email so the professional-email rewrite (repo main,
# PR #7) can be ported to their build. (Oct 2026)
#
# READ ONLY. Copies files and computes hashes. Does not modify the
# application, the database, or the service. Nothing here needs elevation.
#
# Why it exists: the reminder TEMA received on 22 Oct ("S.No / Software /
# Expiry date / Status / Time frame", "Review and renew in OctoAssist ->
# Settings -> Software Subscriptions") is NOT produced by any code in this
# repository. Their build is a different lineage, so the patch has to be
# written against their files, not ours. Run this over RDP in PowerShell and
# send back the ZIP it produces.

$root  = 'C:\Program Files\Third Octopus\OctoAssist Server\app'
$stamp = Get-Date -Format yyyyMMdd-HHmmss
$dest  = "$env:USERPROFILE\Desktop\octoassist-subemail-src-$stamp"

if (-not (Test-Path -LiteralPath $root)) {
  Write-Host "NOT FOUND: $root" -ForegroundColor Red
  Write-Host 'Check the install path and edit $root at the top of this script.' -ForegroundColor Yellow
  return
}

New-Item -ItemType Directory -Path $dest -Force | Out-Null

# 1. Find the file(s) that actually build the email, by the wording in it.
#    Their function may not be called send_software_expiry_digest at all.
$needles = @('Time frame', 'Review and renew', 'need attention', 'Expiring soon',
             'Software subscription', 'expiry_digest', 'expiring_soon')
$hits = Get-ChildItem -LiteralPath $root -Recurse -Include *.py,*.html,*.txt,*.j2 -File -EA SilentlyContinue |
        Select-String -SimpleMatch -Pattern $needles -EA SilentlyContinue
$hits | ForEach-Object { "{0}:{1}: {2}" -f $_.Path.Substring($root.Length + 1), $_.LineNumber, $_.Line.Trim() } |
  Set-Content -LiteralPath (Join-Path $dest 'WHERE-THE-EMAIL-IS-BUILT.txt') -Encoding UTF8

# 2. Collect those files plus the ones the rewrite leans on.
$wanted = @(
  'services\notifications.py',
  'services\subscriptions.py',
  'services\scheduler.py',
  'services\email.py',
  'models.py',
  'config.py'
) + ($hits | ForEach-Object { $_.Path.Substring($root.Length + 1) } | Sort-Object -Unique)
$wanted = $wanted | Sort-Object -Unique

$manifest = @()
$missing  = @()
foreach ($rel in $wanted) {
  $src = Join-Path $root $rel
  if (-not (Test-Path -LiteralPath $src)) { $missing += $rel; continue }
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

# 3. Which hand patches are already on this box, so the new one does not collide.
$markers = @('OCTO-BRANDSIZE','OCTO-NOSPARK','OCTO-MASTERS','OCTO-ASSETREC','OCTO-SAMURL','OCTO-ITEM6','OCTO-SUBEMAIL')
$found = foreach ($m in $markers) {
  $c = (Get-ChildItem -LiteralPath $root -Recurse -Include *.py,*.css -File -EA SilentlyContinue |
        Select-String -SimpleMatch $m -EA SilentlyContinue).Count
  "{0,-16} {1}" -f $m, $c
}
$found | Add-Content -LiteralPath (Join-Path $dest 'MANIFEST.txt') -Encoding ASCII

$zip = "$dest.zip"
Compress-Archive -Path "$dest\*" -DestinationPath $zip -Force

Write-Host ''
Write-Host 'Collected (read-only, nothing was modified):' -ForegroundColor Green
$manifest | Format-Table -AutoSize
Write-Host "Email wording found in $(($hits | Select-Object -ExpandProperty Path -Unique).Count) file(s) - see WHERE-THE-EMAIL-IS-BUILT.txt"
if ($missing.Count) {
  Write-Host 'NOT FOUND on this box (their build may name these differently):' -ForegroundColor Yellow
  $missing | ForEach-Object { Write-Host "  $_" }
}
Write-Host ''
Write-Host "ZIP ready: $zip" -ForegroundColor Cyan
Write-Host 'Send that file back. Source only - no database contents, no .env, no credentials.'
