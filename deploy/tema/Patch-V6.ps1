<#
    Pre-patch the TEMA "v6" build so a rollout does not regress what is already
    fixed on their live server.

    v6 is a third lineage: it is not in this repository, not on the Third
    Octopus droplet, and not the build running in Program Files. A v6 rollout
    overwrites styles.css and every .py file, which silently reverts both
    patches applied on 14 Aug 2026 — on a client who was told they were fixed.
    This applies the same two fixes to the v6 tree BEFORE it ships.

    Both changes are the ones already verified on production:
      1. Table header / row overlap  (CSS, appended)
      2. Patch Compliance drilldown HTTP 500  (one elif branch replaced)

    Safe by construction: each change is marker-guarded so re-running does
    nothing, writes a timestamped .bak beside the file, and if the Python
    pattern does not match, that file is left untouched and the script says so.

        .\Patch-V6.ps1                 # report, then apply what is missing
        .\Patch-V6.ps1 -Check          # report only, change nothing
        .\Patch-V6.ps1 -Rollback       # restore newest backups
        .\Patch-V6.ps1 -Root 'D:\path\to\v6'
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Users\Administrator\Desktop\Octoassist_v6',
    [switch]$Check,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'

function Say($msg, $colour = 'Gray') { Write-Host $msg -ForegroundColor $colour }

if (-not (Test-Path -LiteralPath $Root)) {
    Say "ROOT NOT FOUND: $Root" 'Red'
    Say 'Pass the right folder:  .\Patch-V6.ps1 -Root "C:\path\to\v6"'
    exit 1
}

Say "v6 root : $Root" 'Cyan'
if (Test-Path "$Root\.git") {
    Say 'git     : IS a checkout — last commits:' 'Cyan'
    try { git -C $Root log --oneline -3 } catch { Say '  (git not on PATH)' }
} else {
    Say 'git     : not a checkout' 'Yellow'
}

# ---------------------------------------------------------------- discovery --
$css = Get-ChildItem $Root -Recurse -Filter 'styles.css' -EA SilentlyContinue |
       Where-Object { $_.FullName -match '[\\/]static[\\/]styles\.css$' } |
       Select-Object -First 1
$vr  = Get-ChildItem $Root -Recurse -Filter 'views_reports.py' -EA SilentlyContinue |
       Select-Object -First 1

Say ''
Say "styles.css       : $(if ($css) { $css.FullName } else { 'NOT FOUND' })"
Say "views_reports.py : $(if ($vr)  { $vr.FullName }  else { 'NOT FOUND' })"

$cssDone = $false; $vrDone = $false
if ($css) { $cssDone = Select-String -LiteralPath $css.FullName -SimpleMatch 'octoTableFadeIn' -Quiet }
if ($vr)  { $vrDone  = Select-String -LiteralPath $vr.FullName  -SimpleMatch 'OCTO-ITEM6'      -Quiet }

Say ''
Say "header overlap fix present : $cssDone"      $(if ($cssDone) { 'Green' } else { 'Yellow' })
Say "patch-compliance fix present: $vrDone"      $(if ($vrDone)  { 'Green' } else { 'Yellow' })
if ($vr) {
    $bad = (Select-String -LiteralPath $vr.FullName -Pattern 'cve_id|detected_at|ip_address').Count
    Say "bad column references      : $bad"
}

if ($Check) { Say ''; Say 'Check only — nothing changed.' 'Cyan'; exit 0 }

# ----------------------------------------------------------------- rollback --
if ($Rollback) {
    foreach ($target in @($css, $vr)) {
        if (-not $target) { continue }
        $bk = Get-ChildItem -LiteralPath $target.DirectoryName -Filter "$($target.Name).bak.*" -EA SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($bk) {
            Copy-Item -LiteralPath $bk.FullName -Destination $target.FullName -Force
            Say "rolled back $($target.Name) from $($bk.Name)" 'Yellow'
        } else {
            Say "no backup for $($target.Name)" 'Yellow'
        }
    }
    exit 0
}

$utf8 = New-Object System.Text.UTF8Encoding($false)

# ------------------------------------------------------- 1. header overlap ---
if ($css -and -not $cssDone) {
    $patch = @'

/* ===========================================================================
   OctoAssist - table header / row overlap fix   (carried into v6)

   1. table.data sat in the slideUp entrance animation, which animates
      `transform`, with fill-mode `both` - the final keyframe stays applied for
      the life of the page. An identity translateY(0) is still a transform, and
      a transformed element becomes the containing block for its descendants,
      displacing the sticky header 16px down into the first row.
   2. `overflow: hidden` (for the rounded corners) made the table its own
      scrollport, confining the sticky header to the table's box so it stopped
      pinning and scrolled away. `clip` clips identically without a scrollport.
   3. The header background was translucent, so rows read through it.
   =========================================================================== */

@keyframes octoTableFadeIn { from { opacity: 0; } to { opacity: 1; } }

table.data {
  animation: octoTableFadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) both !important;
  transform: none !important;
  overflow: hidden !important;
  overflow: clip !important;
}

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

table.data thead th {
  position: sticky !important;
  top: var(--topbar-h, 75px) !important;
  z-index: 5 !important;
}
'@
    $bk = "$($css.FullName).bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -LiteralPath $css.FullName -Destination $bk -Force
    $existing = Get-Content -LiteralPath $css.FullName -Raw
    [System.IO.File]::WriteAllText($css.FullName, $existing + "`r`n" + $patch, $utf8)
    Say ''
    Say "APPLIED header fix to $($css.FullName)" 'Green'
    Say "  backup: $bk"
} elseif ($css) {
    Say ''; Say 'header fix already present — skipped.' 'Green'
}

# -------------------------------------------------- 2. patch-compliance 500 --
if ($vr -and -not $vrDone) {
    $text = Get-Content -LiteralPath $vr.FullName -Raw
    $pattern = '(?s)[ \t]*elif kpi == "patch_compliance":.*?(?=\n[ \t]*elif kpi ==|\n[ \t]*return \{"type": "empty")'
    $m = [regex]::Match($text, $pattern)
    if (-not $m.Success) {
        Say ''
        Say 'patch_compliance branch NOT FOUND in v6 — file left untouched.' 'Red'
        Say 'Send me the block from this file and I will adapt it.'
    } else {
        $replacement = @'
    elif kpi == "patch_compliance":
        # OCTO-ITEM6: rebuilt on the real schema. The previous version read
        # PatchObservation.cve_id / .detected_at / .description and
        # Agent.ip_address, none of which exist, so every row raised
        # AttributeError and the endpoint returned 500 ("Failed to retrieve
        # records" in the UI). Scoped to critical + Windows + unresolved.
        from ..models import PatchObservation as _PO, Agent as _Ag, PatchSeverity as _Sev

        _linux = ("apt", "dpkg", "snap", "yum", "dnf", "zypper", "apk")
        _q = (db.query(_PO)
                .join(_Ag, _Ag.id == _PO.agent_id)
                .filter(_Ag.tenant_id == tenant_id,
                        _PO.resolved_at.is_(None),
                        _PO.severity == _Sev.critical))
        for _p in _linux:
            _q = _q.filter(~_PO.source.ilike(_p + "%"))
        rows = _q.order_by(_PO.first_seen_at.asc()).limit(100).all()

        def _ts(v):
            return v.astimezone(IST).strftime("%Y-%m-%d %H:%M") if v else "—"

        return {
            "type": "patches",
            "columns": ["Asset", "Patch", "Severity", "Package", "First Seen"],
            "data": [
                {
                    "id": p.id,
                    "asset": p.agent.hostname if p.agent else "—",
                    "title": p.title or p.package_name,
                    "severity": p.severity.value.upper(),
                    "package": p.package_name,
                    "detected": _ts(p.first_seen_at),
                    "detail": {
                        "Package": p.package_name,
                        "Version": f"{p.current_version or 'unknown'} \u2192 {p.available_version or 'unknown'}",
                        "Severity / Source": f"{p.severity.value} \u00b7 {p.source}",
                        "First seen missing": _ts(p.first_seen_at),
                        "Last seen missing": _ts(p.last_seen_at),
                        "Remediation": "Deploy via the Patches module (Bulk Deploy) or run a remote install action on the endpoint.",
                    }
                } for p in rows
            ]
        }
'@
        $bk = "$($vr.FullName).bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
        Copy-Item -LiteralPath $vr.FullName -Destination $bk -Force
        $new = $text.Remove($m.Index, $m.Length).Insert($m.Index, $replacement)
        [System.IO.File]::WriteAllText($vr.FullName, $new, $utf8)
        Say ''
        Say "APPLIED patch-compliance fix to $($vr.FullName)" 'Green'
        Say "  matched $($m.Value.Length) chars, backup: $bk"
    }
} elseif ($vr) {
    Say 'patch-compliance fix already present — skipped.' 'Green'
}

Say ''
Say 'Done. v6 is not running, so no restart is needed now — but when v6 is' 'Cyan'
Say 'deployed, verify both fixes on the live site before telling the client.' 'Cyan'
Say 'Undo:  .\Patch-V6.ps1 -Rollback'
