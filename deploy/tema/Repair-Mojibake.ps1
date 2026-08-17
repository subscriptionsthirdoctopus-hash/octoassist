<#
    Repair double-encoded (mojibake) characters in OctoAssist templates.

    Symptom: the topbar bell and theme toggle render as "ðŸ" and the sortable
    column arrows as "â–¾" / "Â–¾", on a page that is correctly served as UTF-8
    with <meta charset="utf-8">. The corruption is therefore in the files on
    disk: at some point a template was read as UTF-8 and written back as
    Windows-1252 (or vice versa), so each multi-byte character became a run of
    Latin-1 look-alikes.

    IMPORTANT — why this is not a whole-file re-encode. The same files also
    contain characters that are stored CORRECTLY (the em dash in "Sign in —
    OctoAssist" renders fine). Re-interpreting an entire file as Latin-1 would
    repair the broken characters and break the intact ones in the same pass.

    So only the corrupted runs are touched: sequences that look like a UTF-8
    lead byte followed by continuation bytes, once the file is read as UTF-8
    (U+00C2..U+00F4 followed by U+0080..U+00BF). A correct character such as
    U+2014 (—) or U+25BE (▾) is a single code point outside that range and is
    left exactly as it is. Every candidate run is also round-trip checked: it is
    only rewritten if it decodes to something valid and different.

        .\Repair-Mojibake.ps1 -Check     # report what would change
        .\Repair-Mojibake.ps1            # repair
        .\Repair-Mojibake.ps1 -Rollback  # restore newest backups
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Program Files\Third Octopus\OctoAssist Server\app\templates',
    [switch]$Check,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Root)) { Write-Host "NOT FOUND: $Root" -ForegroundColor Red; exit 1 }

$utf8    = New-Object System.Text.UTF8Encoding($false)
$latin1  = [System.Text.Encoding]::GetEncoding(28591)   # ISO-8859-1, byte-for-byte
# A UTF-8 lead byte (C2..F4) plus one or more continuation bytes (80..BF), as
# they appear once the file has been read as text.
$rx      = [regex]'[\u00C2-\u00F4][\u0080-\u00BF]+'

if ($Rollback) {
    $n = 0
    Get-ChildItem -LiteralPath $Root -Filter '*.html.mojibake.bak.*' -Recurse -EA SilentlyContinue |
      Group-Object { $_.FullName -replace '\.mojibake\.bak\..*$', '' } | ForEach-Object {
        $newest = $_.Group | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        Copy-Item -LiteralPath $newest.FullName -Destination $_.Name -Force
        Write-Host "restored $(Split-Path $_.Name -Leaf) from $($newest.Name)" -ForegroundColor Yellow
        $n++
      }
    Write-Host "$n file(s) restored"
    exit 0
}

$files   = Get-ChildItem -LiteralPath $Root -Filter '*.html' -Recurse -EA SilentlyContinue
$touched = 0
$totalFixes = 0

foreach ($f in $files) {
    $text = [System.IO.File]::ReadAllText($f.FullName, $utf8)
    # An ArrayList, not @(): a MatchEvaluator scriptblock runs in its own scope,
    # so `$fixes += ...` would mutate a copy and every count would read as 0.
    # Mutating a reference type works across that boundary.
    $fixes = New-Object System.Collections.ArrayList

    $new = $rx.Replace($text, {
        param($m)
        $run = $m.Value
        try {
            $bytes   = $latin1.GetBytes($run)
            $decoded = [System.Text.Encoding]::UTF8.GetString($bytes)
        } catch { return $run }
        # Reject anything that did not actually improve: a failed decode leaves
        # U+FFFD, and an unchanged result means this run was never mojibake.
        if ($decoded -eq $run -or $decoded.Contains([char]0xFFFD) -or $decoded.Length -eq 0) { return $run }
        [void]$fixes.Add([pscustomobject]@{ From = $run; To = $decoded })
        return $decoded
    })

    if ($new -ne $text) {
        $touched++
        $totalFixes += $fixes.Count
        Write-Host ''
        Write-Host "$($f.Name)  ($($fixes.Count) run(s))" -ForegroundColor Cyan
        $fixes | Group-Object To | Select-Object -First 6 | ForEach-Object {
            Write-Host ("    {0,-12} -> {1}" -f $_.Group[0].From, $_.Name)
        }
        if (-not $Check) {
            $bk = "$($f.FullName).mojibake.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
            Copy-Item -LiteralPath $f.FullName -Destination $bk -Force
            [System.IO.File]::WriteAllText($f.FullName, $new, $utf8)
        }
    }
}

Write-Host ''
if ($touched -eq 0) {
    Write-Host 'No mojibake found — nothing to do.' -ForegroundColor Green
} elseif ($Check) {
    Write-Host "$totalFixes run(s) across $touched file(s) WOULD be repaired. Nothing changed." -ForegroundColor Yellow
    Write-Host 'Re-run without -Check to apply.'
} else {
    Write-Host "Repaired $totalFixes run(s) across $touched file(s)." -ForegroundColor Green
    Write-Host 'Templates are read at render time, so a restart is usually not needed;'
    Write-Host 'restart the service if the pages still show the old characters.'
    Write-Host 'Undo:  .\Repair-Mojibake.ps1 -Rollback'
}
