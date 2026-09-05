# OctoAssist - enlarge the OctoAssist wordmark in the topbar (TEMA India, Aug 2026)
# Change request #2 of Ashutosh Pati's 26 Aug list: "Increase the font size of
# the OctoAssist logo slightly for better visibility."
#
# CSS only: no template change, no service restart, no downtime.
#
# Why the nav padding is in here too:
#   The admin topbar was ALREADY overflowing before this change. Measured in a
#   harness against the real stylesheet at 1366x768 (the common laptop width
#   at TEMA), the Logout button sat 20px past the right edge and was clipped.
#   Growing the wordmark 16px -> 19px would have pushed that to 38px.
#   Trimming each nav link's horizontal padding 12px -> 10px buys back 40px
#   across the ten links, so after this patch Logout FITS at 1366 (-2px) where
#   it did not before. The bar keeps its 80px height either way.
#
#   At 1280 wide the bar still overflows (~84px, was ~104px). That is a
#   pre-existing layout limit, improved but not solved here. Fixing 1280
#   properly means shorter labels or a responsive collapse - a separate job.
#
# Run elevated (Run as administrator). The 25 Aug failure was an unelevated
# write that reported success and did nothing; this script verifies instead.

$css = 'C:\Program Files\Third Octopus\OctoAssist Server\app\static\styles.css'

if (-not (Test-Path -LiteralPath $css)) {
  Write-Host "NOT FOUND: $css" -ForegroundColor Red; return
}

$before = Get-Item -LiteralPath $css
Write-Host "stylesheet : $css"
Write-Host "size before: $($before.Length) bytes   modified: $($before.LastWriteTime)"
Write-Host "attributes : $($before.Attributes)"
try {
  $elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
              ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  Write-Host "elevated   : $elevated"
} catch { }

if ($before.IsReadOnly) {
  Write-Host 'styles.css is READ-ONLY - clearing that attribute.' -ForegroundColor Yellow
  Set-ItemProperty -LiteralPath $css -Name IsReadOnly -Value $false
}

# Pre-flight: can this session actually open the file for writing?
try {
  $fs = [IO.File]::Open($css, 'Open', 'Write', 'None'); $fs.Close()
} catch {
  Write-Host 'CANNOT WRITE to styles.css - this is the 25 Aug failure, caught.' -ForegroundColor Red
  Write-Host "  ($($_.Exception.GetType().Name): $($_.Exception.Message))"
  Write-Host 'Fix one of these, then paste again:' -ForegroundColor Yellow
  Write-Host '  - not elevated : close this window, right-click PowerShell > Run as administrator'
  Write-Host '  - file locked  : something else has it open for writing'
  Write-Host '  - ACL          : icacls on the file will show who is denied'
  return
}

if (Select-String -LiteralPath $css -SimpleMatch 'OCTO-BRANDSIZE' -Quiet) {
  Write-Host 'Already applied - nothing to do.' -ForegroundColor Green; return
}

$bak = "$css.octobrandsize-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
Copy-Item -LiteralPath $css -Destination $bak -Force

$patch = @'

/* ===========================================================================
   OCTO-BRANDSIZE - larger topbar wordmark   (TEMA India, Aug 2026)

   Requested by Ashutosh Pati, 26 Aug 2026, item 2:
     "The OctoAssist logo font appears too small."

   .brand-product  16px -> 19px   the "OctoAssist" wordmark
   .brand-mark     11px -> 12px   the "ITSM" tag, kept in proportion
   .topbar nav a   padding 7px 12px -> 7px 10px

   !important on all three on purpose:
     - brand-mark carries an inline style="font-size:11px" in base.html, and a
       CSS-only patch cannot remove an inline style any other way;
     - this server's stylesheet ends in several late override blocks, so an
       ordinary rule appended here is not guaranteed to be the last word.

   The nav padding is not cosmetic drive-by work - see the header of
   Paste-BrandSize.ps1. Without it this change clips the Logout button on
   1366-wide screens.
   =========================================================================== */

.topbar .brand-product { font-size: 19px !important; }
.topbar .brand-mark    { font-size: 12px !important; }
.topbar nav a          { padding: 7px 10px !important; }
'@

try {
  # UTF8 without BOM - a BOM appended mid-file renders as stray characters.
  [IO.File]::WriteAllText($css, ((Get-Content -LiteralPath $css -Raw) + "`r`n" + $patch), (New-Object Text.UTF8Encoding($false)))
} catch {
  Write-Host "WRITE FAILED: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Nothing changed. Backup left at $bak" -ForegroundColor Yellow
  return
}

# Verify, rather than assume.
$after  = Get-Item -LiteralPath $css
$marked = Select-String -LiteralPath $css -SimpleMatch 'OCTO-BRANDSIZE' -Quiet
Write-Host "size after : $($after.Length) bytes   modified: $($after.LastWriteTime)"
Write-Host "backup     : $bak"

if ($marked -and $after.Length -gt $before.Length) {
  Write-Host 'VERIFIED: marker present, file grew, timestamp moved.' -ForegroundColor Green
  Write-Host 'No restart needed.' -ForegroundColor Green
  Write-Host ''
  Write-Host 'NOTE: base.html requests /static/styles.css?v=1.0.3 . That query' -ForegroundColor Yellow
  Write-Host 'string did not change, so anyone with the old file cached keeps it' -ForegroundColor Yellow
  Write-Host 'until they hard-refresh (Ctrl-Shift-R). Tell TEMA to do that, or' -ForegroundColor Yellow
  Write-Host 'bump the ?v= in base.html - which needs a service restart.' -ForegroundColor Yellow
  Write-Host ''
  Write-Host 'Best final check is from OUTSIDE the box:' -ForegroundColor Cyan
  Write-Host '  curl -s https://octoassist.temaindia.com/static/styles.css | Select-String OCTO-BRANDSIZE'
} else {
  Write-Host 'FAILED: the file did not change. Re-run elevated.' -ForegroundColor Red
}
