# Undo Paste-BrandSize.ps1 - restore the topbar wordmark to 16px.
#
# Strips the OCTO-BRANDSIZE block from styles.css. Does not touch anything
# else, so it is safe to run even if other marked patches (OCTO-NOSPARK etc.)
# were applied after it.
#
# Run elevated.

$css = 'C:\Program Files\Third Octopus\OctoAssist Server\app\static\styles.css'

if (-not (Test-Path -LiteralPath $css)) {
  Write-Host "NOT FOUND: $css" -ForegroundColor Red; return
}

$before = Get-Item -LiteralPath $css
Write-Host "size before: $($before.Length) bytes   modified: $($before.LastWriteTime)"

if (-not (Select-String -LiteralPath $css -SimpleMatch 'OCTO-BRANDSIZE' -Quiet)) {
  Write-Host 'OCTO-BRANDSIZE not present - nothing to undo.' -ForegroundColor Green; return
}

if ($before.IsReadOnly) { Set-ItemProperty -LiteralPath $css -Name IsReadOnly -Value $false }

try {
  $fs = [IO.File]::Open($css, 'Open', 'Write', 'None'); $fs.Close()
} catch {
  Write-Host 'CANNOT WRITE - re-run elevated.' -ForegroundColor Red; return
}

$bak = "$css.octobrandsize-undo-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
Copy-Item -LiteralPath $css -Destination $bak -Force

$text = Get-Content -LiteralPath $css -Raw

# The block runs from its banner comment to the last rule it adds. Anchored on
# both ends so a partial match cannot eat the rest of the stylesheet.
$pattern = '(?s)\r?\n/\* =+\r?\n\s*OCTO-BRANDSIZE.*?\.topbar nav a\s*\{[^}]*\}(\r?\n)?'
$new = [regex]::Replace($text, $pattern, "`r`n")

if ($new -eq $text) {
  Write-Host 'Marker found but the block did not match the expected shape.' -ForegroundColor Red
  Write-Host 'Edit styles.css by hand, or restore one of the .bak files.' -ForegroundColor Yellow
  Write-Host "Backup of current state: $bak"
  return
}

[IO.File]::WriteAllText($css, $new, (New-Object Text.UTF8Encoding($false)))

$after = Get-Item -LiteralPath $css
Write-Host "size after : $($after.Length) bytes   modified: $($after.LastWriteTime)"
Write-Host "backup     : $bak"

if (-not (Select-String -LiteralPath $css -SimpleMatch 'OCTO-BRANDSIZE' -Quiet)) {
  Write-Host 'VERIFIED: block removed.' -ForegroundColor Green
  Write-Host 'Hard-refresh with Ctrl-Shift-R to see it.' -ForegroundColor Green
} else {
  Write-Host 'FAILED: marker still present.' -ForegroundColor Red
}
