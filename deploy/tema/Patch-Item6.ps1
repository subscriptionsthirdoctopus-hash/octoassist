<#
    OctoAssist item 6 — Patch Compliance drilldown returns HTTP 500.

    The `patch_compliance` branch of /reports/api/drilldown reads four columns
    that do not exist on the models: PatchObservation.cve_id, .detected_at,
    .description and Agent.ip_address. Every row raises AttributeError, the
    endpoint 500s, and the widget shows its generic "Failed to retrieve
    records". It only fires once at least one patch exists, which is why it
    looked data-dependent.

    This replaces that one branch with a version built on the real schema. The
    replacement imports what it needs locally, so it does not depend on the
    file's existing import list.

    Blast radius is one elif branch of one endpoint. If the pattern does not
    match, the file is left untouched and the script says so. Worst case the
    drilldown stays broken, which is where it is today.

    A backup is written next to the file. Python changes need the service
    restarted to take effect — see the end of the output.

        .\Patch-Item6.ps1                # apply
        .\Patch-Item6.ps1 -Check         # report only
        .\Patch-Item6.ps1 -Rollback      # restore newest backup
#>
[CmdletBinding()]
param(
    [string]$Path = 'C:\Program Files\Third Octopus\OctoAssist Server\app\web\views_reports.py',
    [switch]$Check,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path)) { Write-Host "NOT FOUND: $Path" -ForegroundColor Red; exit 1 }

$text = Get-Content -LiteralPath $Path -Raw
$already = $text -match 'OCTO-ITEM6'
Write-Host "file        : $Path"
Write-Host "size        : $($text.Length) bytes"
Write-Host "already     : $already"
Write-Host "broken cols : $(([regex]::Matches($text,'cve_id|detected_at|ip_address')).Count) reference(s)"

if ($Check) { exit 0 }

if ($Rollback) {
    $bk = Get-ChildItem -LiteralPath (Split-Path $Path) -Filter 'views_reports.py.bak.*' -EA SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $bk) { Write-Host 'No backup found.' -ForegroundColor Red; exit 1 }
    Copy-Item -LiteralPath $bk.FullName -Destination $Path -Force
    Write-Host "Rolled back from $($bk.FullName)" -ForegroundColor Yellow
    Write-Host 'Restart the OctoAssist service for it to take effect.'
    exit 0
}

if ($already) { Write-Host 'Nothing to do - already patched.' -ForegroundColor Green; exit 0 }

# Match the whole branch: from `elif kpi == "patch_compliance":` up to the next
# `elif kpi ==` at the same indent, or the trailing empty-response return.
$pattern = '(?s)[ \t]*elif kpi == "patch_compliance":.*?(?=\n[ \t]*elif kpi ==|\n[ \t]*return \{"type": "empty")'
$m = [regex]::Match($text, $pattern)
if (-not $m.Success) {
    Write-Host 'PATTERN DID NOT MATCH - file left untouched.' -ForegroundColor Red
    Write-Host 'Send me the block and I will adapt the patch.'
    exit 1
}
Write-Host "matched     : $($m.Value.Length) chars"

$replacement = @'
    elif kpi == "patch_compliance":
        # OCTO-ITEM6: rebuilt on the real schema. The previous version read
        # PatchObservation.cve_id / .detected_at / .description and
        # Agent.ip_address, none of which exist, so every row raised
        # AttributeError and the endpoint returned 500 ("Failed to retrieve
        # records" in the UI). Also scoped to critical + Windows + unresolved
        # so the list reconciles with the card's "N critical patches
        # outstanding" instead of listing every severity.
        from ..models import PatchObservation as _PO, Agent as _Ag, PatchSeverity as _Sev

        # Windows-sourced only: the Linux agent reports apt/dpkg/snap origins.
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

$bk = "$Path.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item -LiteralPath $Path -Destination $bk -Force
Write-Host "backup      : $bk"

$new = $text.Remove($m.Index, $m.Length).Insert($m.Index, $replacement)
[System.IO.File]::WriteAllText($Path, $new, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "new size    : $((Get-Item -LiteralPath $Path).Length) bytes"
Write-Host ''
Write-Host 'Applied. Python is loaded at startup, so RESTART the service:' -ForegroundColor Green
Write-Host '  Get-Service *octo* | Restart-Service -Force'
Write-Host 'To undo:  .\Patch-Item6.ps1 -Rollback   (then restart again)'
