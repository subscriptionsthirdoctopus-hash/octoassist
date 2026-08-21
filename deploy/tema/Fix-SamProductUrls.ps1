<#
    OctoAssist - address SAM product pages by query string, not by path.

    A product name is arbitrary vendor text. Putting it in a URL PATH means
    every character it contains has to survive the web server's path filtering.
    Across TEMA's 2,419 distinct product names:

        125 contain "+"  -> IIS rejected %2B as double escaping (unblocked on
                            21 Aug by Fix-PlusUrl404.ps1)
         31 contain "/"  -> %2F in a path is rejected by http.sys, BELOW
                            request filtering; no site-level setting reaches it
         17 contain "?", "#" or "%"

    Query strings are not path-filtered, so

        /software/product?publisher=...&product=...

    fixes all three groups at once, instead of chasing one character class at a
    time at the web-server layer. Once this is in, the allowDoubleEscaping
    setting is belt-and-braces rather than load-bearing.

    Two new routes are APPENDED and the six links in the templates are pointed
    at them. The existing path routes stay registered and working, so any
    bookmark or emailed link still resolves. The new routes delegate to the
    existing handlers - one implementation of the page, reachable two ways.

        .\Fix-SamProductUrls.ps1 -Capture    # copy current files out
        .\Fix-SamProductUrls.ps1 -Check      # report only, change nothing
        .\Fix-SamProductUrls.ps1             # apply
        .\Fix-SamProductUrls.ps1 -Verify     # probe the hard names through IIS
        .\Fix-SamProductUrls.ps1 -Rollback   # restore the newest backups

    Python change - restart the OctoAssist service afterwards. Safe to re-run:
    the OCTO-SAMURL marker makes a second run a no-op.
#>
[CmdletBinding()]
param(
    [string]$Root     = 'C:\Program Files\Third Octopus\OctoAssist Server',
    [string]$SiteHost = 'octoassist.temaindia.com',
    [switch]$Capture,
    [switch]$Check,
    [switch]$Verify,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$Marker = 'OCTO-SAMURL'
$Stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'

# PowerShell 5.1's Get-Content -Raw decodes with the system ANSI code page,
# which double-encodes every non-ASCII character. Read and write UTF-8
# explicitly, without a BOM. See Repair-Encoding.ps1 for the incident.
$UTF8 = New-Object System.Text.UTF8Encoding($false)
function Read-Utf8  ([string]$p) { [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) }
function Write-Utf8 ([string]$p, [string]$s) { [System.IO.File]::WriteAllText($p, $s, $UTF8) }
function Say ([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }

function Probe ([string]$path) {
    $req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1' + $path)
    $req.Host = $SiteHost
    $req.AllowAutoRedirect = $false
    $req.Timeout = 20000
    try { $r = $req.GetResponse(); $c = [int]$r.StatusCode; $r.Close() }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) { $c = [int]$_.Exception.Response.StatusCode }
        else { $c = $_.Exception.Status }
    }
    return $c
}

$ViewsPath = Join-Path $Root 'app\web\views_software.py'
$Templates = @('software_list.html', 'software_dashboard.html', 'software_detail.html')
$Payload   = Join-Path $PSScriptRoot 'sam-product-urls-append.py'

Say ""
Say "OctoAssist - SAM product URLs by query string" Cyan
Say "root : $Root"
Say ""

if (-not (Test-Path -LiteralPath $ViewsPath)) { Say "NOT FOUND: $ViewsPath" Red; exit 1 }
$TmplPaths = @()
foreach ($t in $Templates) {
    $tp = Join-Path $Root ('app\templates\' + $t)
    if (-not (Test-Path -LiteralPath $tp)) { Say "NOT FOUND: $tp" Red; exit 1 }
    $TmplPaths += $tp
}

# A "/" name and a "+" name. Both must reach the app once this is in.
$SlashUrl = '/software/product?publisher=Microsoft%20Corporation&product=Microsoft%20Visual%20Basic%2FC%2B%2B%20Runtime%20(x86)'
$PlusUrl  = '/software/product?publisher=Microsoft&product=Microsoft%20Visual%20C%2B%2B%202015-2022%20Redistributable'
$OldPath  = '/software/product/Microsoft/Microsoft%20Edge'

if ($Verify) {
    Say ("query-string, name with '/' : {0}" -f (Probe $SlashUrl))
    Say ("query-string, name with '+' : {0}" -f (Probe $PlusUrl))
    Say ("old path route still works  : {0}" -f (Probe $OldPath))
    Say ""
    Say "303 = reached the application (redirect to login). 404 = still blocked." Gray
    exit 0
}

if ($Capture) {
    $out = Join-Path $PSScriptRoot "captured-samurl-$Stamp"
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    Copy-Item -LiteralPath $ViewsPath -Destination $out
    foreach ($tp in $TmplPaths) { Copy-Item -LiteralPath $tp -Destination $out }
    Get-ChildItem -Path $out -File | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Write-Host
    Say "Captured to: $out" Green
    exit 0
}

if ($Rollback) {
    $restored = 0
    foreach ($p in (@($ViewsPath) + $TmplPaths)) {
        $bak = Get-ChildItem -Path (Split-Path $p) -Filter ((Split-Path $p -Leaf) + '.samurl-*.bak') -ErrorAction SilentlyContinue |
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
Say ("views_software.py : {0,8:N0} bytes" -f $views.Length)

if ($views -match $Marker) {
    Say ""
    Say "Already applied - views_software.py carries the $Marker marker. Nothing to do." Green
    exit 0
}
if (-not (Test-Path -LiteralPath $Payload)) {
    Say "NOT FOUND: $Payload" Red
    Say "sam-product-urls-append.py must sit next to this script." Yellow
    exit 1
}

# Names the appended routes delegate to. Without them there is nothing to
# delegate to and nothing should be touched.
$required = @('router', 'require_staff', 'get_db', 'User', 'Request',
              'software_product_detail', 'software_product_export')
$missing  = $required | Where-Object { $views -notmatch [regex]::Escape($_) }
if ($missing) {
    Say ""
    Say "views_software.py does not reference: $($missing -join ', ')" Red
    Say "This build differs too much to patch blind. Run -Capture and review." Yellow
    exit 1
}

# A route registered elsewhere as /software/{something} would shadow
# /software/product. Refuse rather than register a route that never fires.
if ($views -match '@router\.get\("/software/\{[^/}]+\}"') {
    Say ""
    Say "views_software.py has a single-segment /software/{x} route, which would" Red
    Say "shadow /software/product. Not applying." Red
    exit 1
}

$applied = @(); $skipped = @()
$work = $views.TrimEnd() + "`r`n`r`n`r`n" + (Read-Utf8 $Payload).TrimEnd() + "`r`n"
$applied += 'append/query-string routes for product detail and export  [1]'

# ------------------------------------------------------------- the templates --
# One regex per link shape. The export link is matched first because its path
# is the detail path plus a suffix, and the looser pattern would otherwise
# claim it and leave "/export.csv" stranded on the end of a query string.
$ExportRx = [regex]'/software/product/\{\{\s*([A-Za-z_][\w.]*)\s*\|\s*urlencode\s*\}\}/\{\{\s*([A-Za-z_][\w.]*)\s*\|\s*urlencode\s*\}\}/export\.csv'
$DetailRx = [regex]'/software/product/\{\{\s*([A-Za-z_][\w.]*)\s*\|\s*urlencode\s*\}\}/\{\{\s*([A-Za-z_][\w.]*)\s*\|\s*urlencode\s*\}\}'
# &amp; not & : this lands inside an HTML attribute.
$ExportNew = '/software/product/export.csv?publisher={{ $1|urlencode }}&amp;product={{ $2|urlencode }}'
$DetailNew = '/software/product?publisher={{ $1|urlencode }}&amp;product={{ $2|urlencode }}'

$tmplWork = @{}
foreach ($tp in $TmplPaths) {
    $name = Split-Path $tp -Leaf
    $t = Read-Utf8 $tp
    $nExp = $ExportRx.Matches($t).Count
    $t = $ExportRx.Replace($t, $ExportNew)
    $nDet = $DetailRx.Matches($t).Count
    $t = $DetailRx.Replace($t, $DetailNew)
    if ($nExp + $nDet -gt 0) {
        $applied += ("template/{0}: {1} link(s) -> query string" -f $name, ($nExp + $nDet))
        $tmplWork[$tp] = $t
    } else {
        $skipped += ("template/{0}: no path-style product links found" -f $name)
    }
}

Say ""
Say "would apply:" Cyan
foreach ($a in $applied) { Say "  OK    $a" Green }
foreach ($s in $skipped) { Say "  SKIP  $s" Yellow }

if ($skipped.Count) {
    Say ""
    Say "Skipped items simply do not get made - nothing is forced." Yellow
}

if ($Check) {
    Say ""
    Say "-Check: nothing was written." Cyan
    exit 0
}

# ------------------------------------------------------------------- write ---
Copy-Item -LiteralPath $ViewsPath -Destination "$ViewsPath.samurl-$Stamp.bak" -Force
foreach ($tp in $tmplWork.Keys) { Copy-Item -LiteralPath $tp -Destination "$tp.samurl-$Stamp.bak" -Force }
Say ""
Say "backed up  : *.samurl-$Stamp.bak" Gray

Write-Utf8 $ViewsPath $work
foreach ($tp in $tmplWork.Keys) { Write-Utf8 $tp $tmplWork[$tp] }
Say ("written    : views_software.py + {0} template(s)" -f $tmplWork.Count) Green

$pyExe = $null
$bundled = Get-ChildItem -Path $Root -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bundled) { $pyExe = $bundled.FullName }
else {
    foreach ($cand in @('python.exe', 'python3', 'python')) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c) { $pyExe = $c.Source; break }
    }
}
if ($pyExe) {
    Say "compiling with: $pyExe" Gray
    & $pyExe -m py_compile $ViewsPath 2>&1 | Out-String | Write-Host
    if ($LASTEXITCODE -eq 0) {
        Say "views_software.py compiles" Green
    } else {
        Say "DOES NOT COMPILE - rolling back" Red
        Copy-Item -LiteralPath "$ViewsPath.samurl-$Stamp.bak" -Destination $ViewsPath -Force
        foreach ($tp in $tmplWork.Keys) { Copy-Item -LiteralPath "$tp.samurl-$Stamp.bak" -Destination $tp -Force }
        Say "restored from backup. Nothing changed." Yellow
        exit 1
    }
} else {
    Say "no python interpreter found - skipping the compile check" Yellow
}

Say ""
Say "Restart the service, then re-run with -Verify:" Yellow
Say "    Restart-Service OctoAssistServer" White
Say "    .\Fix-SamProductUrls.ps1 -Verify" White
Say ""
Say "Undo at any time:  .\Fix-SamProductUrls.ps1 -Rollback" Gray
