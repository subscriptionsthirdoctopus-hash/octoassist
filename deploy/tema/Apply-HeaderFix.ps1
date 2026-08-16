<#
    OctoAssist — table header / row overlap fix, for the TEMA India server.

    Static-asset change only: appends a CSS block to styles.css. No database
    change, no code change, no service restart, no downtime.

    Safe to re-run: a marker check makes a second run a no-op. Writes a
    timestamped backup and can restore it.

        .\Apply-HeaderFix.ps1              # apply
        .\Apply-HeaderFix.ps1 -Check       # report only, change nothing
        .\Apply-HeaderFix.ps1 -Rollback    # restore the newest backup
        .\Apply-HeaderFix.ps1 -CssPath 'C:\path\to\styles.css'
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Rollback,
    [string]$CssPath
)

$ErrorActionPreference = 'Stop'
$Marker = 'OctoAssist - table header / row overlap fix'

# --- the patch ---------------------------------------------------------------
# Single-quoted here-string: nothing in here is interpolated by PowerShell.
$Patch = @'

/* ===========================================================================
   OctoAssist - table header / row overlap fix   (TEMA India, Aug 2026)

   Three independent faults put the header labels and the row values in the
   same strip. Fixing only one leaves the artefact visible.
   =========================================================================== */

@keyframes octoTableFadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}

table.data {
  /* FAULT 1 - the table is permanently transformed. table.data is in the
     slideUp entrance animation, which animates `transform`, with fill-mode
     `both` - so the final keyframe stays applied for the life of the page, and
     an identity translateY(0) is still a transform. A transformed element
     becomes the containing block for its descendants, which displaces the
     sticky header: measured on this build as matrix(1, 0, 0, 1, 0, 16), i.e.
     16px down, into the first row's band. Tables now fade without moving. */
  animation: octoTableFadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) both !important;
  transform: none !important;

  /* FAULT 2 - the table is its own scroll container. `overflow: hidden` (for
     the rounded corners) makes the table a scrollport, which confines the
     sticky header to the table's own box: it stops pinning and scrolls away.
     `clip` clips identically without creating a scrollport. Declared as a pair
     so a browser without `clip` keeps `hidden`. */
  overflow: hidden !important;
  overflow: clip !important;
}

/* FAULT 3 - the header is translucent. This build paints it with --surface-2,
   rgba(240, 244, 249, 0.90), so rows read straight through it. Same colours,
   full opacity. (--thead-solid does not exist in this build, so the values are
   written out rather than referenced.) */
table.data thead th,
table.data thead tr,
table.data thead {
  background: #eef2f8 !important;
}
html[data-theme="dark"] table.data thead th,
html[data-theme="dark"] table.data thead tr,
html[data-theme="dark"] table.data thead {
  background: #161e38 !important;
}

/* Keep the header above the rows passing under it. This build already sets
   z-index 5 and top: var(--topbar-h); restated so a later rule cannot undo it. */
table.data thead th {
  position: sticky !important;
  top: var(--topbar-h, 75px) !important;
  z-index: 5 !important;
}
'@

# --- locate styles.css -------------------------------------------------------
function Find-Css {
    if ($CssPath) { return $CssPath }

    $candidates = @(
        'C:\octoassist\server\app\static\styles.css'
        'C:\OctoAssist\server\app\static\styles.css'
        'C:\inetpub\wwwroot\octoassist\server\app\static\styles.css'
        'C:\Program Files\OctoAssist\server\app\static\styles.css'
        'D:\octoassist\server\app\static\styles.css'
    )
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }

    # Fall back to a bounded search of the fixed drives.
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem).Root) {
        $hit = Get-ChildItem -Path $drive -Filter 'styles.css' -Recurse -Depth 7 `
                   -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -match '[\\/]static[\\/]styles\.css$' } |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$css = Find-Css
if (-not $css -or -not (Test-Path -LiteralPath $css)) {
    Write-Host 'ERROR: could not locate styles.css.' -ForegroundColor Red
    Write-Host "       Re-run with the path, e.g.  .\Apply-HeaderFix.ps1 -CssPath 'C:\...\static\styles.css'"
    exit 1
}

$applied = Select-String -LiteralPath $css -SimpleMatch -Pattern $Marker -Quiet
Write-Host "stylesheet  : $css"
Write-Host "size        : $((Get-Item -LiteralPath $css).Length) bytes"
Write-Host "fix applied : $(if ($applied) { 'yes' } else { 'no' })"

if ($Check) { exit 0 }

if ($Rollback) {
    $backup = Get-ChildItem -LiteralPath (Split-Path $css) -Filter 'styles.css.bak.*' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $backup) { Write-Host 'ERROR: no backup found next to styles.css' -ForegroundColor Red; exit 1 }
    Copy-Item -LiteralPath $backup.FullName -Destination $css -Force
    Write-Host "rolled back from $($backup.FullName)" -ForegroundColor Yellow
    exit 0
}

if ($applied) {
    Write-Host 'Nothing to do - the fix is already present.' -ForegroundColor Green
    exit 0
}

$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$css.bak.$stamp"
Copy-Item -LiteralPath $css -Destination $backup -Force
Write-Host "backup      : $backup"

# UTF8 without BOM: a BOM appended mid-file would render as stray characters.
$existing = Get-Content -LiteralPath $css -Raw
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($css, $existing + "`r`n`r`n" + $Patch, $utf8NoBom)

Write-Host "new size    : $((Get-Item -LiteralPath $css).Length) bytes"
Write-Host ''
Write-Host 'Done. No restart needed - styles.css is served from disk.' -ForegroundColor Green
Write-Host 'In the browser press Ctrl-Shift-R (hard refresh) to get past the cache.'
Write-Host 'To undo:  .\Apply-HeaderFix.ps1 -Rollback'
