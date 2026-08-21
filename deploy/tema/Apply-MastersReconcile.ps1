<#
    OctoAssist - Asset Management (Masters) reconciliation, TEMA India server.

    This is the patch for the screen Dipesh Panchal actually reported:
    Settings -> Asset Management -> Assets. It is a different module from the
    Asset Register (/assets) and a different table (am_assets / ManagedAsset),
    which is why Apply-AssetReconcile.ps1 does not address his ticket.

    Confirmed against the production database on 21 Aug 2026:

      * Achhad holds exactly 110 rows here - 106 in_use, 3 stock, 1 retired.
        His 110 is not wrong, it is just not reconcilable against a sheet.

      * The list was ordered by ManagedAsset.id DESC - the order rows were
        entered. That is the "numeric order" he asked for.

      * 13 rows have location_id NULL. They count toward the total but match
        no location filter, and the Location dropdown is built only from
        assets that HAVE a location, so they could not be selected at all.
        Exactly one resolves to Achhad (serial PG047VPT, AU-TIPL-LAP-027).

    Four one-line edits plus an appended block in views_asset_mgmt.py, and
    three inserts in asset_list.html. No function is rewritten.

        .\Apply-MastersReconcile.ps1 -Capture    # copy current files out
        .\Apply-MastersReconcile.ps1 -Check      # report only, change nothing
        .\Apply-MastersReconcile.ps1             # apply
        .\Apply-MastersReconcile.ps1 -Rollback   # restore newest backups

    Python change - restart the service afterwards. Safe to re-run: the
    OCTO-MASTERS marker makes a second run a no-op.
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Program Files\Third Octopus\OctoAssist Server',
    [switch]$Capture,
    [switch]$Check,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$Marker = 'OCTO-MASTERS'
$Stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'

# PowerShell 5.1's Get-Content -Raw decodes with the system ANSI code page,
# which double-encodes every non-ASCII character in the file. Read and write
# UTF-8 explicitly, without a BOM. See Repair-Encoding.ps1 for the incident.
$UTF8 = New-Object System.Text.UTF8Encoding($false)
function Read-Utf8  ([string]$p) { [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) }
function Write-Utf8 ([string]$p, [string]$s) { [System.IO.File]::WriteAllText($p, $s, $UTF8) }
function Say ([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

$ViewsPath = Join-Path $Root 'app\web\views_asset_mgmt.py'
$TmplPath  = Join-Path $Root 'app\templates\asset_list.html'
$Payload   = Join-Path $PSScriptRoot 'masters-reconcile-append.py'

Say ""
Say "OctoAssist - Asset Management (Masters) reconciliation patch" Cyan
Say "root : $Root"
Say ""

foreach ($p in @($ViewsPath, $TmplPath)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Say "NOT FOUND: $p" Red
        Say "Pass -Root pointing at the OctoAssist Server folder." Yellow
        exit 1
    }
}

if ($Capture) {
    $out = Join-Path $PSScriptRoot "captured-masters-$Stamp"
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -LiteralPath $ViewsPath -Destination $out
    Copy-Item -LiteralPath $TmplPath  -Destination $out
    Get-ChildItem -Path $out -File | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Write-Host
    Say "Captured to: $out" Green
    exit 0
}

if ($Rollback) {
    $restored = 0
    foreach ($p in @($ViewsPath, $TmplPath)) {
        $bak = Get-ChildItem -Path (Split-Path $p) -Filter ((Split-Path $p -Leaf) + '.octomasters-*.bak') -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($bak) {
            Copy-Item -LiteralPath $bak.FullName -Destination $p -Force
            Say "restored $(Split-Path $p -Leaf) from $($bak.Name)" Green
            $restored++
        } else {
            Say "no backup found for $(Split-Path $p -Leaf)" Yellow
        }
    }
    if ($restored) { Say ""; Say "Restart the OctoAssist service to load the restored code." Yellow }
    exit 0
}

$views = Read-Utf8 $ViewsPath
$tmpl  = Read-Utf8 $TmplPath

Say ("views_asset_mgmt.py : {0,8:N0} bytes" -f $views.Length)
Say ("asset_list.html     : {0,8:N0} bytes" -f $tmpl.Length)

if ($views -match $Marker) {
    Say ""
    Say "Already applied - views_asset_mgmt.py carries the $Marker marker. Nothing to do." Green
    exit 0
}

if (-not (Test-Path -LiteralPath $Payload)) {
    Say "NOT FOUND: $Payload" Red
    Say "masters-reconcile-append.py must sit next to this script." Yellow
    exit 1
}

# Names the appended block leans on. Without them the append cannot work and
# nothing should be touched.
$required = @('router', 'require_admin', 'get_db', 'ManagedAsset', 'AssetStatus',
              'AGE_BRACKETS', '_age_bracket', '_hostnames_for')
$missing  = $required | Where-Object { $views -notmatch [regex]::Escape($_) }
if ($missing) {
    Say ""
    Say "views_asset_mgmt.py does not reference: $($missing -join ', ')" Red
    Say "This build differs too much to patch blind. Run -Capture and review." Yellow
    exit 1
}

$edits = @(
    # The list was ordered by insertion. Sort after every filter has run, on
    # the same value the first column displays, and reuse the hosts map the
    # context already needed so this costs no extra query.
    @{ Name = 'sort/order the list by hostname, not by insertion'
       Rx   = [regex]'([ \t]*)# Option lists come from the whole tenant so the admin can always pivot\.'
       New  = '$1# OCTO-MASTERS: order the way a person reads it - hostname then serial,' + "`r`n" +
              '$1# natural-numeric so TIPL-LAP-2 precedes TIPL-LAP-10. Sorted here, after' + "`r`n" +
              '$1# every filter above has run.' + "`r`n" +
              '$1_octo_hosts = _hostnames_for(db, assets)' + "`r`n" +
              '$1_octo_masters_sort(assets, _octo_hosts)' + "`r`n" +
              "`r`n" +
              '$1# Option lists come from the whole tenant so the admin can always pivot.'
       Min  = 1 }

    # Reuse the map built for the sort rather than querying a second time.
    @{ Name = 'perf/reuse the hostname map instead of rebuilding it'
       Rx   = [regex]'"hosts":\s*_hostnames_for\(db,\s*assets\),'
       New  = '"hosts": _octo_hosts,'
       Min  = 1 }

    # A row with location_id NULL failed every location pick while still
    # counting in the total.
    @{ Name = 'filter/location can select the rows that have none'
       Rx   = [regex]'assets = \[a for a in assets if a\.location and a\.location\.name\.lower\(\) == location\.lower\(\)\]'
       New  = 'assets = [a for a in assets if _octo_loc_match(a, location)]'
       Min  = 1 }

    # Surface how many rows carry no location, so the gap between a site count
    # and a spreadsheet has a visible cause instead of being found by
    # subtraction.
    @{ Name = 'context/report how many assets have no location'
       Rx   = [regex]'"f_vendor": vendor, "f_age": age,(\s*)"S": AssetStatus, "flash": flash\},'
       New  = '"f_vendor": vendor, "f_age": age,$1"am_unlocated": sum(1 for a in every if not a.location),$1"S": AssetStatus, "flash": flash},'
       Min  = 1 }
)

$applied = @(); $skipped = @()
$work = $views
foreach ($e in $edits) {
    $n = $e.Rx.Matches($work).Count
    if ($n -ge $e.Min) {
        $work = $e.Rx.Replace($work, $e.New)
        $applied += "$($e.Name)  [$n]"
    } else {
        $skipped += "$($e.Name)  [anchor not found]"
    }
}

$append = Read-Utf8 $Payload
$work = $work.TrimEnd() + "`r`n`r`n`r`n" + $append.TrimEnd() + "`r`n"
$applied += 'append/helpers and the Masters CSV export  [1]'

# ------------------------------------------------------------- the template --
$tmplWork = $tmpl
$tApplied = @(); $tSkipped = @()

# "No location set" entry. The generic filter loop renders four dropdowns, so
# the extra option is emitted only for the location one. The sentinel is
# written literally, so this needs no new template context.
$optAnchor = [regex]'(\{%\s*for o in opts\s*%\}<option value="\{\{ o \}\}"[^\n]*?\{%\s*endfor\s*%\})'
if ($optAnchor.IsMatch($tmplWork) -and $tmplWork -notmatch 'No location set') {
    $tmplWork = $optAnchor.Replace($tmplWork,
        '$1' + "`r`n" +
        '      {% if name == ''location'' %}<option value="__none__" {% if val == ''__none__'' %}selected{% endif %}>-- No location set --</option>{% endif %}', 1)
    $tApplied += 'template/"No location set" filter entry'
} else {
    $tSkipped += 'template/"No location set" filter entry  [anchor not found or already present]'
}

# Export button beside Apply. request.url.query carries the live filters, so
# this needs no new context either.
$applyAnchor = [regex]'(<button type="submit" class="button primary" style="padding:6px 14px;">Apply</button>)'
if ($applyAnchor.IsMatch($tmplWork) -and $tmplWork -notmatch 'export\.csv') {
    $tmplWork = $applyAnchor.Replace($tmplWork,
        '$1' + "`r`n" +
        '  <a class="button" style="padding:6px 14px; text-decoration:none;" title="Downloads exactly the rows listed below, with the current filters applied" href="/settings/asset-management/assets/export.csv{% if request.url.query %}?{{ request.url.query }}{% endif %}">Export CSV</a>', 1)
    $tApplied += 'template/export button'
} else {
    $tSkipped += 'template/export button  [anchor not found or already present]'
}

# A line under the count naming the assets that no location filter can reach.
# Anchored with a non-greedy wildcard rather than the literal text, which
# contains a middle dot: a non-ASCII byte in this script would be mis-decoded
# by PowerShell 5.1 and the match would silently fail.
$countAnchor = [regex]'(\{\{ assets\|length \}\} asset.*?\{% endif %\})'
if ($countAnchor.IsMatch($tmplWork) -and $tmplWork -notmatch 'am_unlocated') {
    $tmplWork = $countAnchor.Replace($tmplWork,
        '$1' + "`r`n" +
        '        {% if am_unlocated and f_location != ''__none__'' %}<br><a href="/settings/asset-management/assets?location=__none__">{{ am_unlocated }} asset{{ '''' if am_unlocated == 1 else ''s'' }} with no location</a> - counted above, but under no location filter.{% endif %}', 1)
    $tApplied += 'template/unlocated count under the header'
} else {
    $tSkipped += 'template/unlocated count under the header  [anchor not found or already present]'
}

# ------------------------------------------------------------------ report ---
Say ""
Say "would apply:" Cyan
foreach ($a in $applied)  { Say "  OK    $a" Green }
foreach ($a in $tApplied) { Say "  OK    $a" Green }
foreach ($s in $skipped)  { Say "  SKIP  $s" Yellow }
foreach ($s in $tSkipped) { Say "  SKIP  $s" Yellow }

if ($skipped.Count -or $tSkipped.Count) {
    Say ""
    Say "Some anchors did not match. The skipped items simply do not get made -" Yellow
    Say "nothing is forced. Run -Capture and review before applying." Yellow
}

if ($Check) {
    Say ""
    Say "-Check: nothing was written." Cyan
    exit 0
}

Copy-Item -LiteralPath $ViewsPath -Destination "$ViewsPath.octomasters-$Stamp.bak" -Force
Copy-Item -LiteralPath $TmplPath  -Destination "$TmplPath.octomasters-$Stamp.bak"  -Force
Say ""
Say "backed up  : *.octomasters-$Stamp.bak" Gray

Write-Utf8 $ViewsPath $work
Write-Utf8 $TmplPath  $tmplWork
Say "written    : views_asset_mgmt.py, asset_list.html" Green

# A syntax error here stops the service starting, so catch it while the backup
# is one command away.
$pyExe = $null
$bundled = Get-ChildItem -Path $Root -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bundled) {
    $pyExe = $bundled.FullName
} else {
    foreach ($cand in @('python.exe', 'python3', 'python')) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c) { $pyExe = $c.Source; break }
    }
}
if ($pyExe) {
    Say "compiling with: $pyExe" Gray
    & $pyExe -m py_compile $ViewsPath 2>&1 | Out-String | Write-Host
    if ($LASTEXITCODE -eq 0) {
        Say "views_asset_mgmt.py compiles" Green
    } else {
        Say "DOES NOT COMPILE - rolling back" Red
        Copy-Item -LiteralPath "$ViewsPath.octomasters-$Stamp.bak" -Destination $ViewsPath -Force
        Copy-Item -LiteralPath "$TmplPath.octomasters-$Stamp.bak"  -Destination $TmplPath  -Force
        Say "restored from backup. Nothing changed." Yellow
        exit 1
    }
} else {
    Say "no python interpreter found - skipping the compile check" Yellow
    Say "Verify the service starts cleanly after the restart; -Rollback undoes this." Yellow
}

Say ""
Say "Restart the OctoAssist service to load the change:" Yellow
try {
    $svc = Get-Service -Name '*octo*' -ErrorAction SilentlyContinue
    if ($svc) { $svc | ForEach-Object { Say "    Restart-Service '$($_.Name)'" White } }
    else { Say "    (no service matching *octo* found - restart it by its own name)" White }
} catch {
    Say "    (could not enumerate services - restart the OctoAssist service manually)" White
}
Say ""
Say "Then check:  Settings -> Asset Management -> Assets" Cyan
Say "  - the list is ordered by hostname, numerically" Cyan
Say "  - Location filter offers '-- No location set --'" Cyan
Say "  - 'Export CSV' sits beside Apply" Cyan
Say ""
Say "Undo at any time:  .\Apply-MastersReconcile.ps1 -Rollback" Gray
