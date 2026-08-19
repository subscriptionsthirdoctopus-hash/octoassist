<#
    Repair mojibake introduced into TEMA production on 14 Aug 2026.

    Cause: the stylesheet cache-bust and the CSS append both read the file with
    `Get-Content -Raw`. In Windows PowerShell 5.1 that decodes a BOM-less UTF-8
    file using the system ANSI codepage, so every multi-byte character came
    back as mojibake and was then written out as UTF-8 — corrupting it for
    good. The bell and moon icons in the topbar became "ðŸ"", and the sort
    arrows became "â–¾".

    Repair: restore each file from the byte-for-byte .bak taken before the
    change, then re-apply the change reading and writing as UTF-8 explicitly.
    Nothing that was fixed is lost — the CSS patch and the cache-bust are both
    re-applied, correctly this time.

        .\Fix-Encoding.ps1 -Check     # report only
        .\Fix-Encoding.ps1            # repair
#>
[CmdletBinding()]
param(
    [string]$Root  = 'C:\Program Files\Third Octopus\OctoAssist Server',
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)

# How UTF-8 bytes look once decoded as CP1252 and re-encoded as UTF-8:
#   emoji  F0 9F ...  -> "ð" + "Ÿ"   (U+00F0 U+0178)
#   ▾      E2 96 BE   -> "â" + "–"   (U+00E2 U+2013)
#   —      E2 80 94   -> "â" + "€"   (U+00E2 U+20AC)
#   ·      C2 B7      -> "Â" + ...   (U+00C2)
$MOJI = @(
    ([char]0x00F0 + [char]0x0178),
    ([char]0x00E2 + [char]0x2013),
    ([char]0x00E2 + [char]0x20AC),
    ([char]0x00C2)
)

function Test-Mojibake([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $t = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    $n = 0
    foreach ($m in $MOJI) { $n += ([regex]::Matches($t, [regex]::Escape($m))).Count }
    return $n
}

function Newest-Backup([string]$path) {
    $dir  = Split-Path $path
    $name = Split-Path $path -Leaf
    Get-ChildItem -LiteralPath $dir -Filter "$name.bak.*" -EA SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -First 1      # OLDEST = pre-change
}

$base = Join-Path $Root 'app\templates\base.html'
$css  = Join-Path $Root 'app\static\styles.css'

Write-Host "base.html  : $base"
Write-Host "  mojibake sequences: $(Test-Mojibake $base)"
Write-Host "styles.css : $css"
Write-Host "  mojibake sequences: $(Test-Mojibake $css)"

# Any other template that got hit
$others = Get-ChildItem (Join-Path $Root 'app\templates') -Filter *.html -EA SilentlyContinue |
          ForEach-Object { $n = Test-Mojibake $_.FullName; if ($n -gt 0) { "$($_.Name): $n" } }
if ($others) { Write-Host 'other templates with mojibake:'; $others | % { Write-Host "  $_" } }

if ($Check) { Write-Host ''; Write-Host 'Check only — nothing changed.' -ForegroundColor Cyan; exit 0 }

# ---- base.html: restore, then re-bump the cache-buster UTF-8-safely --------
$bk = Newest-Backup $base
if ($bk) {
    Copy-Item -LiteralPath $bk.FullName -Destination $base -Force
    Write-Host "restored base.html from $($bk.Name)" -ForegroundColor Yellow

    $t = [System.IO.File]::ReadAllText($base, [System.Text.Encoding]::UTF8)
    $v = Get-Date -Format 'yyyyMMddHHmm'
    $n = [regex]::Replace($t, '(?<=styles\.css)(\?v=[^"'']*)?', '?v=' + $v, 1)
    [System.IO.File]::WriteAllText($base, $n, $utf8)
    Write-Host "re-bumped stylesheet to ?v=$v (read/written as UTF-8)" -ForegroundColor Green
} else {
    Write-Host 'NO BACKUP for base.html — cannot restore automatically.' -ForegroundColor Red
}

# ---- styles.css: restore, then re-append the header fix UTF-8-safely -------
$bkc = Newest-Backup $css
if ($bkc) {
    Copy-Item -LiteralPath $bkc.FullName -Destination $css -Force
    Write-Host "restored styles.css from $($bkc.Name)" -ForegroundColor Yellow

    $patch = @'

/* OctoAssist - table header / row overlap fix (TEMA, Aug 2026) */
@keyframes octoTableFadeIn { from { opacity: 0; } to { opacity: 1; } }
table.data {
  animation: octoTableFadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) both !important;
  transform: none !important;
  overflow: hidden !important;
  overflow: clip !important;
}
table.data thead th,
table.data thead tr,
table.data thead { background: #eef2f8 !important; }
html[data-theme="dark"] table.data thead th,
html[data-theme="dark"] table.data thead tr,
html[data-theme="dark"] table.data thead { background: #161e38 !important; }
table.data thead th {
  position: sticky !important;
  top: var(--topbar-h, 75px) !important;
  z-index: 5 !important;
}
'@
    $t = [System.IO.File]::ReadAllText($css, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($css, $t + "`r`n" + $patch, $utf8)
    Write-Host 're-applied the header fix (read/written as UTF-8)' -ForegroundColor Green
} else {
    Write-Host 'NO BACKUP for styles.css — cannot restore automatically.' -ForegroundColor Red
}

Write-Host ''
Write-Host "base.html  mojibake now: $(Test-Mojibake $base)"
Write-Host "styles.css mojibake now: $(Test-Mojibake $css)"
Write-Host ''
Write-Host 'Restart the service, then hard-refresh the browser:' -ForegroundColor Cyan
Write-Host '  Get-Service *octo* | Restart-Service -Force'
