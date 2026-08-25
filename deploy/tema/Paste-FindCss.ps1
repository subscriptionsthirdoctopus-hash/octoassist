# OctoAssist - which styles.css does production actually serve?  (TEMA, Aug 2026)
# Read-only. Changes nothing. Paste into PowerShell on SRV81.
#
# Why: the OCTO-NOSPARK block was reported applied on 25 Aug, but
# GET /static/styles.css still has no marker - so a different copy got patched.
# The served file is 109302 bytes unpatched, ~110046 patched.

# 1. The process actually listening on the app port (do not use $pid - it is
#    PowerShell's own automatic variable for the current process).
$listenPid = (Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue |
              Select-Object -First 1).OwningProcess
if ($listenPid) {
  Write-Host "--- process serving :8091 ---" -ForegroundColor Cyan
  Get-CimInstance Win32_Process -Filter "ProcessId=$listenPid" |
    Select-Object ProcessId, ExecutablePath, CommandLine | Format-List
} else {
  Write-Host 'Nothing listening on 8091 - check the app port.' -ForegroundColor Yellow
}

# 2. Every static/styles.css on the box, newest first.
Write-Host "--- styles.css copies ---" -ForegroundColor Cyan
Get-ChildItem C:\ -Recurse -Filter 'styles.css' -Depth 8 -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -like '*\static\styles.css' } |
  ForEach-Object {
    [pscustomobject]@{
      Bytes    = $_.Length
      Match    = if ($_.Length -eq 109302) { 'SERVED (unpatched)' }
                 elseif ($_.Length -ge 110000 -and $_.Length -le 110100) { 'patched size' }
                 else { '' }
      NoSpark  = [bool](Select-String -LiteralPath $_.FullName -SimpleMatch 'OCTO-NOSPARK' -Quiet)
      HasSpark = [bool](Select-String -LiteralPath $_.FullName -SimpleMatch 'kpi-spark' -Quiet)
      Modified = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
      Path     = $_.FullName
    }
  } | Sort-Object Modified -Descending | Format-Table -AutoSize -Wrap

# 3. Any backups the earlier paste left behind.
Write-Host "--- octonospark backups ---" -ForegroundColor Cyan
Get-ChildItem C:\ -Recurse -Filter 'styles.css.octonospark-*.bak' -Depth 8 -ErrorAction SilentlyContinue |
  Select-Object Length, LastWriteTime, FullName | Format-Table -AutoSize -Wrap
