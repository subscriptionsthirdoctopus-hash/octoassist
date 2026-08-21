<#
    OctoAssist - Asset Register reconciliation fixes, for the TEMA India server.

    Raised by Dipesh Panchal, 21 Aug 2026 ("Asset Count Discrepancy in Assets
    Management Report"): his site shows 110 assets in OctoAssist against 113 in
    his spreadsheet, and the report is not ordered so he cannot tell which
    three differ.

    Three causes, all of them silent on screen:

      1. Hostnames sorted as bytes, so TEMA-PC-10 lands between -1 and -2.
         His sheet is in human order, so every comparison is done by eye.
      2. An asset with no location on the device AND none on its assigned user
         matches no location filter, while still counting in the total. Those
         rows are reachable only by not filtering - which a per-site count
         never does.
      3. The filters compare untrimmed, so "Achhad" and "Achhad " behave as two
         places, splitting one site's count across two dropdown entries.

    Also adds GET /assets/export.csv, which downloads whatever the filters
    currently select, so a count can be diffed by hostname instead of guessed.

    HOW THIS PATCH IS BUILT. TEMA's build is ahead of every branch in the repo,
    so this does NOT replace any function. It makes four one-line edits, then
    appends a self-contained block of new helpers. Python resolves module
    globals when a function runs, not when the file is parsed, so helpers added
    at the end of the file are visible to code above them. Each edit is guarded
    and reported separately: an edit whose anchor is not found is SKIPPED and
    named, never forced, and the file is written only if every edit that was
    attempted succeeded.

    Run -Capture FIRST and send the output back before applying, so the edits
    can be checked against the build they will actually land on.

        .\Apply-AssetReconcile.ps1 -Capture    # copy current files out, change nothing
        .\Apply-AssetReconcile.ps1 -Check      # report what would happen
        .\Apply-AssetReconcile.ps1             # apply
        .\Apply-AssetReconcile.ps1 -Rollback   # restore the newest backups

    Python changes need the OctoAssist service restarted - see the end of the
    output. Safe to re-run: the OCTO-ASSETREC marker makes a second run a no-op.
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Program Files\Third Octopus\OctoAssist Server',
    [switch]$Capture,
    [switch]$Check,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$Marker = 'OCTO-ASSETREC'
$Stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'

# PowerShell 5.1's Get-Content -Raw decodes using the system ANSI code page,
# not UTF-8, which double-encodes every em dash and emoji in the file. Read and
# write UTF-8 explicitly, and write without a BOM. See Repair-Encoding.ps1 for
# the incident this prevents.
$UTF8 = New-Object System.Text.UTF8Encoding($false)
function Read-Utf8  ([string]$p) { [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) }
function Write-Utf8 ([string]$p, [string]$s) { [System.IO.File]::WriteAllText($p, $s, $UTF8) }

function Say ([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

$ViewsPath = Join-Path $Root 'app\web\views.py'
$TmplPath  = Join-Path $Root 'app\templates\assets_list.html'
$Payload   = Join-Path $PSScriptRoot 'asset-reconcile-append.py'

Say ""
Say "OctoAssist - Asset Register reconciliation patch" Cyan
Say "root : $Root"
Say ""

foreach ($p in @($ViewsPath, $TmplPath)) {
    if (-not (Test-Path -LiteralPath $p)) {
        Say "NOT FOUND: $p" Red
        Say "Pass -Root pointing at the OctoAssist Server folder." Yellow
        exit 1
    }
}

# ---------------------------------------------------------------- capture ----
if ($Capture) {
    $out = Join-Path $PSScriptRoot "captured-$Stamp"
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -LiteralPath $ViewsPath -Destination $out
    Copy-Item -LiteralPath $TmplPath  -Destination $out

    # The screen Dipesh photographed is the Masters module, which does not
    # exist in the repo. Take its sources too so the same three fixes can be
    # ported to it precisely rather than guessed at.
    foreach ($extra in @('app\web\views_settings.py')) {
        $ep = Join-Path $Root $extra
        if (Test-Path -LiteralPath $ep) { Copy-Item -LiteralPath $ep -Destination $out }
    }
    Get-ChildItem -Path (Join-Path $Root 'app\templates') -Filter '*asset*' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike '*.bak' } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $out }

    Get-ChildItem -Path $out -File | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Write-Host
    Say "Captured to: $out" Green
    Say "Zip that folder and send it back before applying." Yellow
    exit 0
}

# --------------------------------------------------------------- rollback ----
if ($Rollback) {
    $restored = 0
    foreach ($p in @($ViewsPath, $TmplPath)) {
        $bak = Get-ChildItem -Path (Split-Path $p) -Filter ((Split-Path $p -Leaf) + '.octoassetrec-*.bak') -ErrorAction SilentlyContinue |
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

# --------------------------------------------------------- read + analyse ----
$views = Read-Utf8 $ViewsPath
$tmpl  = Read-Utf8 $TmplPath

Say ("views.py     : {0,8:N0} bytes" -f $views.Length)
Say ("assets_list  : {0,8:N0} bytes" -f $tmpl.Length)

if ($views -match $Marker) {
    Say ""
    Say "Already applied - views.py carries the $Marker marker. Nothing to do." Green
    exit 0
}

if (-not (Test-Path -LiteralPath $Payload)) {
    Say "NOT FOUND: $Payload" Red
    Say "asset-reconcile-append.py must sit next to this script." Yellow
    exit 1
}

# Names the appended block leans on. If the build does not have them under
# these names, the append cannot work and nothing should be touched.
$required = @('router', 'require_staff', 'get_db', 'Agent', 'EntraDevice')
$missing  = $required | Where-Object { $views -notmatch [regex]::Escape($_) }
if ($missing) {
    Say ""
    Say "views.py does not reference: $($missing -join ', ')" Red
    Say "This build differs too much to patch blind. Run -Capture and send the files." Yellow
    exit 1
}

# --------------------------------------------------------------- the edits ---
# Each entry: name, regex, replacement, how many matches are expected.
$edits = @(
    @{ Name = 'sort/remove byte-order ORDER BY'
       Rx   = [regex]'\.order_by\(Agent\.hostname\)\.all\(\)'
       New  = '.all()'
       Min  = 1 }

    @{ Name = 'filter/location honours trim, case and the no-location pick'
       Rx   = [regex]'if\s+location\s+and\s+\(\s*loc\s+or\s+""\s*\)\.lower\(\)\s*!=\s*location\.lower\(\)\s*:\s*continue'
       New  = 'if _octo_loc_excluded(loc, location):                        continue'
       Min  = 1 }

    @{ Name = 'filter/department honours trim and case'
       Rx   = [regex]'if\s+department\s+and\s+\(\s*dept\s+or\s+""\s*\)\.lower\(\)\s*!=\s*department\.lower\(\)\s*:\s*continue'
       New  = 'if _octo_dim_excluded(dept, department):                     continue'
       Min  = 1 }

    @{ Name = 'dropdown/one entry per location, not per spelling'
       Rx   = [regex]'locations(\s*)=(\s*)sorted\(loc_set\)'
       New  = 'locations$1=$2_octo_fold_variants(loc_set)'
       Min  = 1 }

    @{ Name = 'dropdown/one entry per department, not per spelling'
       Rx   = [regex]'departments(\s*)=(\s*)sorted\(dept_set\)'
       New  = 'departments$1=$2_octo_fold_variants(dept_set)'
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

# Numeric ordering of the two tables, inserted just before the dropdown block.
# Guarded on both list names actually existing, so nothing is inserted into a
# function that builds its rows differently.
$sortAnchor = [regex]'([ \t]*)#\s*Distinct values for filter dropdowns'
if ($work -match 'rows\.append\(' -and $work -match 'discovered_rows\.append\(' -and $sortAnchor.IsMatch($work)) {
    $sortCode = @'
$1# Human order, not byte order: "TEMA-PC-2" before "TEMA-PC-10". Sorted here
$1# because the two tables are merged in Python and only one of them was ever
$1# a database ordering.   OCTO-ASSETREC
$1rows.sort(key=lambda r: _octo_natural_key(r["hostname"]))
$1discovered_rows.sort(key=lambda r: _octo_natural_key(r["hostname"]))

$1# Distinct values for filter dropdowns
'@
    $work = $sortAnchor.Replace($work, $sortCode, 1)
    $applied += 'sort/numeric ordering of both tables  [1]'
} else {
    $skipped += 'sort/numeric ordering of both tables  [anchor or row lists not found]'
}

# The appended helper block + the export route.
$append = Read-Utf8 $Payload
$work = $work.TrimEnd() + "`r`n`r`n`r`n" + $append.TrimEnd() + "`r`n"
$applied += 'append/helpers and GET /assets/export.csv  [1]'

# ------------------------------------------------------------- the template --
$tmplWork = $tmpl
$tApplied = @(); $tSkipped = @()

# "No location set" entry, right after the generated location options. The
# sentinel is written literally so this needs no new template context.
$optAnchor = [regex]'(\{%\s*for\s+l\s+in\s+locations\s*%\}.*?\{%\s*endfor\s*%\})'
if ($optAnchor.IsMatch($tmplWork) -and $tmplWork -notmatch 'No location set') {
    $tmplWork = $optAnchor.Replace($tmplWork,
        '$1' + "`r`n" + '      <option value="__none__" {% if filter_location == "__none__" %}selected{% endif %}>-- No location set --</option>', 1)
    $tApplied += 'template/"No location set" filter entry'
} else {
    $tSkipped += 'template/"No location set" filter entry  [anchor not found or already present]'
}

# Export button next to Apply. request.url.query passes the live filters
# through, so this needs no new template context either.
$applyAnchor = [regex]'(<button\s+type="submit"\s+class="button">\s*Apply\s*</button>)'
if ($applyAnchor.IsMatch($tmplWork) -and $tmplWork -notmatch 'export\.csv') {
    $tmplWork = $applyAnchor.Replace($tmplWork,
        '$1' + "`r`n" + '  <a class="button" style="text-decoration:none;" title="Downloads exactly the rows shown below, with the current filters applied" href="/assets/export.csv{% if request.url.query %}?{{ request.url.query }}{% endif %}">Export this view (CSV)</a>', 1)
    $tApplied += 'template/export button'
} else {
    $tSkipped += 'template/export button  [anchor not found or already present]'
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
    Say "Some anchors did not match this build. The skipped items simply do not" Yellow
    Say "get made - nothing is forced. Run -Capture and send the files back so" Yellow
    Say "those can be written against the real code." Yellow
}

if ($Check) {
    Say ""
    Say "-Check: nothing was written." Cyan
    exit 0
}

# ------------------------------------------------------------------- write ---
Copy-Item -LiteralPath $ViewsPath -Destination "$ViewsPath.octoassetrec-$Stamp.bak" -Force
Copy-Item -LiteralPath $TmplPath  -Destination "$TmplPath.octoassetrec-$Stamp.bak"  -Force
Say ""
Say "backed up  : *.octoassetrec-$Stamp.bak" Gray

Write-Utf8 $ViewsPath $work
Write-Utf8 $TmplPath  $tmplWork
Say "written    : views.py, assets_list.html" Green

# Compile check - a syntax error here means the service will not start, so
# catch it now while the backup is one command away.
# Prefer the interpreter shipped inside the install, since that is the one the
# service actually runs. Fall back to whatever python is on PATH.
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
        Say "views.py compiles" Green
    } else {
        Say "views.py DOES NOT COMPILE - rolling back" Red
        Copy-Item -LiteralPath "$ViewsPath.octoassetrec-$Stamp.bak" -Destination $ViewsPath -Force
        Copy-Item -LiteralPath "$TmplPath.octoassetrec-$Stamp.bak"  -Destination $TmplPath  -Force
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
    if ($svc) {
        $svc | ForEach-Object { Say "    Restart-Service '$($_.Name)'" White }
    } else {
        Say "    (no service matching *octo* found - restart it by its own name)" White
    }
} catch {
    Say "    (could not enumerate services - restart the OctoAssist service manually)" White
}
Say ""
Say "Then check:  /assets  ->  Location filter  ->  '-- No location set --'" Cyan
Say "             and the 'Export this view (CSV)' button next to Apply." Cyan
Say ""
Say "Undo at any time:  .\Apply-AssetReconcile.ps1 -Rollback" Gray
