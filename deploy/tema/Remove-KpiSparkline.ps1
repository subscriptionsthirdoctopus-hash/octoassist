<#
    OctoAssist - remove the sparkline from the "Open tickets" KPI card,
    for the TEMA India server.

    The tiny line chart is absolutely positioned in the bottom-right of the
    card and overlaps the "N past resolution SLA" sub-line, so the text reads
    through the graph. It carries no label, axis or scale, so nothing is lost
    by removing it - the same trend is on the "Tickets created" chart further
    down the same page, which this patch does NOT touch.

    Two steps, either of which is sufficient on its own:

      1. CSS   - hides .kpi-spark inside KPI cards. No restart, no downtime.
      2. HTML  - drops the <div class="kpi-spark"> from reports_home.html so
                 the markup is not emitted at all. Needs a service restart.

    Run -Check first: this build is a different lineage from the repo, and the
    script refuses to guess if the markup does not look like it expects.

        .\Remove-KpiSparkline.ps1 -Check      # report only, change nothing
        .\Remove-KpiSparkline.ps1 -Capture    # copy the two files out for review
        .\Remove-KpiSparkline.ps1             # CSS + HTML, then asks to restart
        .\Remove-KpiSparkline.ps1 -CssOnly    # CSS only - no restart, no downtime
        .\Remove-KpiSparkline.ps1 -Restart    # apply and restart without asking
        .\Remove-KpiSparkline.ps1 -Rollback   # restore the newest backups
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Capture,
    [switch]$CssOnly,
    [switch]$Restart,
    [switch]$Rollback,
    [string]$AppRoot,
    [string]$ServiceName = 'OctoAssistServer'
)

$ErrorActionPreference = 'Stop'
$Marker = 'OCTO-NOSPARK'

function Say($msg, $colour) {
    if ($colour) { Write-Host $msg -ForegroundColor $colour } else { Write-Host $msg }
}

# --- the CSS patch -----------------------------------------------------------
# Single-quoted here-string: nothing in here is interpolated by PowerShell.
$CssPatch = @'

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

# --- locate the install ------------------------------------------------------
function Find-AppRoot {
    if ($AppRoot) { return $AppRoot }

    $candidates = @(
        'C:\Program Files\Third Octopus\OctoAssist Server'
        'C:\Program Files\OctoAssist\server'
        'C:\octoassist\server'
        'C:\OctoAssist\server'
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $c 'app\static\styles.css')) { return $c }
    }
    return $null
}

$root = Find-AppRoot
if (-not $root) {
    Say 'ERROR: could not locate the OctoAssist install.' Red
    Say "       Re-run with the path, e.g.  .\Remove-KpiSparkline.ps1 -AppRoot 'C:\Program Files\Third Octopus\OctoAssist Server'"
    exit 1
}

$css  = Join-Path $root 'app\static\styles.css'
$tpl  = Join-Path $root 'app\templates\reports_home.html'

if (-not (Test-Path -LiteralPath $css)) { Say "ERROR: no styles.css at $css" Red; exit 1 }
$tplFound = Test-Path -LiteralPath $tpl

# --- report ------------------------------------------------------------------
$cssApplied = Select-String -LiteralPath $css -SimpleMatch -Pattern $Marker -Quiet
$tplApplied = $false
$sparkHits  = 0
if ($tplFound) {
    $tplApplied = Select-String -LiteralPath $tpl -SimpleMatch -Pattern $Marker -Quiet
    $sparkHits  = ([regex]::Matches((Get-Content -LiteralPath $tpl -Raw), 'kpi-spark')).Count
}

Say "install root : $root"
Say "stylesheet   : $css  ($((Get-Item -LiteralPath $css).Length) bytes)"
if ($tplFound) {
    Say "template     : $tpl  ($((Get-Item -LiteralPath $tpl).Length) bytes)"
    Say "kpi-spark in template : $sparkHits occurrence(s)"
} else {
    Say "template     : NOT FOUND at $tpl - CSS-only is the option here" Yellow
}
Say "css patch applied      : $(if ($cssApplied) { 'yes' } else { 'no' })"
Say "template patch applied : $(if ($tplApplied) { 'yes' } else { 'no' })"

if ($Check) { exit 0 }

if ($Capture) {
    $out = Join-Path ([Environment]::GetFolderPath('Desktop')) ("octo-nospark-capture-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -LiteralPath $css -Destination $out -Force
    if ($tplFound) { Copy-Item -LiteralPath $tpl -Destination $out -Force }
    Say "Captured to: $out" Green
    exit 0
}

# --- rollback ----------------------------------------------------------------
function Restore-Newest($file, $pattern) {
    $backup = Get-ChildItem -LiteralPath (Split-Path $file) -Filter $pattern -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $backup) { Say "  no backup matching $pattern" Yellow; return $false }
    Copy-Item -LiteralPath $backup.FullName -Destination $file -Force
    Say "  restored $file from $($backup.Name)" Yellow
    return $true
}

if ($Rollback) {
    $restored = $false
    if (Restore-Newest $css 'styles.css.octonospark-*.bak') { $restored = $true }
    if ($tplFound -and (Restore-Newest $tpl 'reports_home.html.octonospark-*.bak')) { $restored = $true }
    if (-not $restored) { Say 'ERROR: nothing to roll back.' Red; exit 1 }
    Say ''
    Say "Rolled back. If the template was restored, restart the service:" Green
    Say "  Restart-Service $ServiceName"
    exit 0
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)   # a BOM mid-file renders as stray characters
$templateChanged = $false

# --- step 1: CSS -------------------------------------------------------------
if ($cssApplied) {
    Say 'CSS  : already patched, skipping.' Green
} else {
    $backup = "$css.octonospark-$stamp.bak"
    Copy-Item -LiteralPath $css -Destination $backup -Force
    $existing = Get-Content -LiteralPath $css -Raw
    [System.IO.File]::WriteAllText($css, $existing + "`r`n`r`n" + $CssPatch, $utf8NoBom)
    Say "CSS  : patched (backup $([System.IO.Path]::GetFileName($backup)))" Green
}

# --- step 2: template --------------------------------------------------------
if ($CssOnly) {
    Say 'HTML : skipped (-CssOnly). The div is still emitted, just hidden.' Yellow
} elseif (-not $tplFound) {
    Say 'HTML : skipped - reports_home.html not found.' Yellow
} elseif ($tplApplied) {
    Say 'HTML : already patched, skipping.' Green
} elseif ($sparkHits -eq 0) {
    Say 'HTML : no kpi-spark in the template - nothing to remove. The CSS rule' Yellow
    Say '       still covers it if this build names the element differently.' Yellow
} else {
    $html = Get-Content -LiteralPath $tpl -Raw

    # Only a self-contained, single-line <div ...kpi-spark...>...</div> is removed.
    # Anything else and we stop rather than guess at this build's markup.
    $rx = [regex]'(?m)^[ \t]*<div[^>]*class\s*=\s*"[^"]*kpi-spark[^"]*"[^>]*>[^\r\n]*?</div>[ \t]*\r?\n'
    $hits = $rx.Matches($html)

    if ($hits.Count -ne $sparkHits) {
        Say "HTML : NOT patched. Found $sparkHits 'kpi-spark' mention(s) but only" Red
        Say "       $($hits.Count) matched the expected single-line <div> shape." Red
        Say '       Run -Capture and review the markup before forcing this.' Red
        Say '       The CSS rule above already hides the graph, so the visible' Yellow
        Say '       fix is in place either way.' Yellow
    } else {
        $backup = "$tpl.octonospark-$stamp.bak"
        Copy-Item -LiteralPath $tpl -Destination $backup -Force
        $new = $rx.Replace($html, '')
        if ($new -match 'kpi-spark') {
            Copy-Item -LiteralPath $backup -Destination $tpl -Force
            Say 'HTML : NOT patched - kpi-spark survived the removal, reverted.' Red
        } else {
            $new = $new.TrimEnd() + "`r`n{# $Marker - KPI-card sparkline removed, it overlapped .kpi-sub #}`r`n"
            [System.IO.File]::WriteAllText($tpl, $new, $utf8NoBom)
            $templateChanged = $true
            Say "HTML : removed $($hits.Count) sparkline div(s) (backup $([System.IO.Path]::GetFileName($backup)))" Green
        }
    }
}

# --- restart -----------------------------------------------------------------
Say ''
if (-not $templateChanged) {
    Say 'Done. CSS is served from disk - no restart needed.' Green
    Say 'In the browser press Ctrl-Shift-R to get past the cache.'
} else {
    $doIt = $Restart
    if (-not $doIt) {
        $ans = Read-Host "Template changed - restart $ServiceName now? [y/N]"
        $doIt = ($ans -eq 'y' -or $ans -eq 'Y')
    }
    if ($doIt) {
        Restart-Service -Name $ServiceName -Force
        Start-Sleep -Seconds 4
        $svc = Get-Service -Name $ServiceName
        Say "$ServiceName : $($svc.Status)" $(if ($svc.Status -eq 'Running') { 'Green' } else { 'Red' })
    } else {
        Say "Not restarted. The CSS rule already hides the graph; the markup" Yellow
        Say "stops being emitted after:  Restart-Service $ServiceName" Yellow
    }
}
Say ''
Say 'To undo:  .\Remove-KpiSparkline.ps1 -Rollback'
