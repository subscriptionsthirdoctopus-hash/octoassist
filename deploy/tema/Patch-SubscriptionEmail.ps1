<#
    OctoAssist (TEMA build) — professional software-subscription reminder email.

    Rewrites the daily "[OctoAssist] Software subscription summary" mail built
    by app\services\license_notifier.py: greeting, one-line purpose with the
    tenant name and generation time, a plain-English summary sentence, a
    numbered table with colour-coded status badges (Expired / Expiring this
    week / Expiring soon) and "In N days" / "N days overdue" time frames, a
    "Next steps" paragraph, a clickable portal link, a sign-off and a
    do-not-reply footer. Subject reads "Action required: N software
    subscriptions expired or expiring" when anything has lapsed, otherwise
    "Software subscription renewals due". Same five columns, same recipients
    (To + CC), same once-a-day 09:00 IST gate — only the wording and layout
    change. Equivalent of repo main PR #7 for this build.

    Three edits, all guarded by the OCTO-SUBEMAIL marker:
      1. replaces _status_and_timeframe() and _digest_bodies() with the new
         renderer (adds _plural() and _digest_subject());
      2. the subject line now calls _digest_subject();
      3. _digest_bodies() is passed the tenant so the mail can name it.

    The file is read and written as UTF-8 explicitly (it contains em dashes
    and arrows); Get-Content without -Encoding would mangle them, which is
    the mojibake incident of 19 Aug.

    A backup is written next to the file. Python is loaded at startup, so
    the OctoAssist service must be restarted for the change to take effect.

        .\Patch-SubscriptionEmail.ps1 -Check              # report only
        .\Patch-SubscriptionEmail.ps1 -Restart            # apply + restart service
        .\Patch-SubscriptionEmail.ps1 -Rollback -Restart  # restore newest backup
#>
[CmdletBinding()]
param(
    [string]$Path = 'C:\Program Files\Third Octopus\OctoAssist Server\app\services\license_notifier.py',
    [string]$PortalUrl = 'https://octoassist.temaindia.com',
    [switch]$Check,
    [switch]$Rollback,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Restart-Octo {
    $svc = Get-Service -Name 'OctoAssistServer' -ErrorAction SilentlyContinue
    if (-not $svc) { $svc = Get-Service *octo* -ErrorAction SilentlyContinue | Select-Object -First 1 }
    if (-not $svc) { Write-Host 'Service not found - restart it by hand.' -ForegroundColor Yellow; return }
    Restart-Service -Name $svc.Name -Force
    Write-Host "service     : $($svc.Name) restarted ($((Get-Service $svc.Name).Status))" -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $Path)) { Write-Host "NOT FOUND: $Path" -ForegroundColor Red; exit 1 }

$text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
$already = $text -match 'OCTO-SUBEMAIL'
Write-Host "file        : $Path"
Write-Host "size        : $($text.Length) chars"
Write-Host "already     : $already"

if ($Rollback) {
    $bk = Get-ChildItem -LiteralPath (Split-Path $Path) -Filter 'license_notifier.py.bak.*' -EA SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $bk) { Write-Host 'No backup found.' -ForegroundColor Red; exit 1 }
    Copy-Item -LiteralPath $bk.FullName -Destination $Path -Force
    Write-Host "Rolled back from $($bk.FullName)" -ForegroundColor Yellow
    if ($Restart) { Restart-Octo } else { Write-Host 'Restart the OctoAssist service for it to take effect.' }
    exit 0
}

# The three anchors. Each must match exactly once or the file is left alone.
$reBlock = [regex]'(?s)def _status_and_timeframe\(.*?(?=\ndef _tick_subscriptions_once\()'
$reSubj  = [regex]'subject = f"\[OctoAssist\] Software subscription summary[^\n]*'
$call    = '_digest_bodies(rows)'
$nBlock = $reBlock.Matches($text).Count
$nSubj  = $reSubj.Matches($text).Count
$nCall  = ([regex]::Matches($text, [regex]::Escape($call))).Count
Write-Host "anchors     : renderer=$nBlock subject=$nSubj call=$nCall (each must be 1)"

if ($Check) { exit 0 }
if ($already) { Write-Host 'Nothing to do - already patched.' -ForegroundColor Green; if ($Restart) { Restart-Octo }; exit 0 }
if ($nBlock -ne 1 -or $nSubj -ne 1 -or $nCall -ne 1) {
    Write-Host 'ANCHORS DID NOT MATCH - file left untouched. Send me the file and I will adapt the patch.' -ForegroundColor Red
    exit 1
}

$block = @'
# OCTO-SUBEMAIL: professional subscription reminder (ported from repo main,
# PR #7). Replaces the bare "need attention" table with a business email:
# greeting, one-line purpose, summary sentence, numbered table with status
# badges, next steps, portal link, sign-off. Same five columns this build
# already uses (S.No / Software / Expiry date / Status / Time frame), same
# recipients, same once-a-day gate.
PORTAL_URL = "__PORTAL_URL__"


def _status_and_timeframe(days_left: int) -> tuple[str, str]:
    if days_left < 0:
        n = -days_left
        return "Expired", f"{n} day{'s' if n != 1 else ''} overdue"
    if days_left == 0:
        return "Expires today", "Today"
    if days_left <= 7:
        return "Expiring this week", f"In {days_left} day{'s' if days_left != 1 else ''}"
    return "Expiring soon", f"In {days_left} day{'s' if days_left != 1 else ''}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _digest_subject(rows, expired: int, expiring: int) -> str:
    today = datetime.now(timezone.utc).astimezone(IST)
    if expired:
        return (f"[OctoAssist] Action required: {_plural(len(rows), 'software subscription')} "
                f"expired or expiring — {today.strftime('%d %b %Y')}")
    horizon = max((sub.notify_days_before or 30) for sub, _ in rows)
    return (f"[OctoAssist] Software subscription renewals due — "
            f"{_plural(len(rows), 'item')} expiring within {horizon} days")


def _digest_bodies(rows: list[tuple[SoftwareSubscription, int]], tenant=None) -> tuple[str, str]:
    """(plain-text, html) renderings of the reminder.
    rows = [(subscription, days_left)] sorted most-urgent first."""
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    tenant_name = (getattr(tenant, "name", "") or "").strip() or "your organisation"
    expired = [r for r in rows if r[1] < 0]
    upcoming = [r for r in rows if r[1] >= 0]
    horizon = max((sub.notify_days_before or 30) for sub, _ in rows)

    if expired and upcoming:
        summary = (f"{_plural(len(expired), 'subscription')} "
                   f"{'has' if len(expired) == 1 else 'have'} already expired and "
                   f"{len(upcoming)} more {'is' if len(upcoming) == 1 else 'are'} due to expire "
                   f"within the next {horizon} days.")
    elif expired:
        summary = (f"{_plural(len(expired), 'subscription')} "
                   f"{'has' if len(expired) == 1 else 'have'} already expired and "
                   f"{'needs' if len(expired) == 1 else 'need'} immediate renewal.")
    else:
        summary = (f"{_plural(len(upcoming), 'subscription')} "
                   f"{'is' if len(upcoming) == 1 else 'are'} due to expire within the next "
                   f"{horizon} days.")

    intro = (f"This is the daily software subscription summary from OctoAssist for "
             f"{tenant_name}, generated on {now_ist.strftime('%A, %d %B %Y at %I:%M %p IST')}.")
    next_step = ("Please review the items listed above and raise the renewal purchase orders "
                 "with the respective vendors. Once a renewal is confirmed, update the expiry "
                 "date in OctoAssist so the item drops off this reminder.")
    cadence = ("This summary is sent once a day at 09:00 IST and only while at least one "
               "subscription needs attention.")

    # ---- plain text (fixed-width) ----
    head = f"{'#':>3}  {'Software':<40} {'Expiry date':<12} {'Status':<19} Time frame"
    lines = ["Dear Team,", "", intro, "", summary, "", head, "-" * len(head)]
    for n, (sub, days_left) in enumerate(rows, start=1):
        status, frame = _status_and_timeframe(days_left)
        lines.append(f"{n:>3}  {(sub.name or '')[:40]:<40} "
                     f"{sub.renewal_date.strftime('%d %b %Y'):<12} {status:<19} {frame}")
    lines += ["-" * len(head), "", "Next steps", next_step, "",
              f"Review and renew: {PORTAL_URL}",
              "(OctoAssist > Settings > Software Subscriptions)", "", cadence, "",
              "Best regards,", "IT Service Desk", "OctoAssist", "",
              "This is an automated notification. Please do not reply to this email."]
    text = "\n".join(lines) + "\n"

    # ---- HTML ----
    td = "padding:9px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;vertical-align:top;"
    html_rows = []
    for n, (sub, days_left) in enumerate(rows, start=1):
        status, frame = _status_and_timeframe(days_left)
        if days_left < 0:
            colour, badge_bg = "#b91c1c", "#fee2e2"
        elif days_left <= 7:
            colour, badge_bg = "#b45309", "#fef3c7"
        else:
            colour, badge_bg = "#166534", "#dcfce7"
        stripe = "background:#ffffff;" if n % 2 else "background:#f9fafb;"
        html_rows.append(
            f"<tr style='{stripe}'>"
            f"<td style='{td}text-align:center;color:#6b7280;'>{n}</td>"
            f"<td style='{td}font-weight:600;color:#111827;'>{escape(sub.name or '')}</td>"
            f"<td style='{td}white-space:nowrap;'>{sub.renewal_date.strftime('%d %b %Y')}</td>"
            f"<td style='{td}'><span style='display:inline-block;padding:2px 8px;"
            f"border-radius:10px;background:{badge_bg};color:{colour};font-weight:600;"
            f"font-size:12px;white-space:nowrap;'>{escape(status)}</span></td>"
            f"<td style='{td}white-space:nowrap;color:{colour};font-weight:600;'>{escape(frame)}</td>"
            f"</tr>")
    th = ("padding:10px 12px;text-align:left;background:#0c64c0;color:#ffffff;font-size:12px;"
          "font-weight:600;text-transform:uppercase;letter-spacing:.4px;white-space:nowrap;")
    header = "".join(
        f"<th style='{th}{extra}'>{h}</th>"
        for h, extra in (("#", "text-align:center;"), ("Software", ""),
                         ("Expiry date", ""), ("Status", ""), ("Time frame", "")))
    p = "margin:0 0 14px;font-size:15px;line-height:1.55;color:#1f2937;"
    html = (
        "<div style=\"font-family:'Aptos Display','Aptos','Segoe UI',Arial,sans-serif;"
        "color:#1f2937;max-width:900px;\">"
        f"<p style='{p}'>Dear Team,</p>"
        f"<p style='{p}'>This is the daily software subscription summary from OctoAssist for "
        f"<strong>{escape(tenant_name)}</strong>, generated on "
        f"{now_ist.strftime('%A, %d %B %Y at %I:%M %p IST')}.</p>"
        f"<p style='{p}'><strong>{escape(summary)}</strong></p>"
        "<table cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;"
        "border:1px solid #e5e7eb;margin:4px 0 18px;'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(html_rows)}</tbody></table>"
        f"<p style='{p}'><strong>Next steps</strong><br>{escape(next_step)}</p>"
        f"<p style='{p}'>Review and renew: <a href='{PORTAL_URL}' style='color:#0c64c0;'>"
        f"{PORTAL_URL}</a><br><span style='color:#6b7280;font-size:13px;'>OctoAssist &rarr; "
        "Settings &rarr; Software Subscriptions</span></p>"
        f"<p style='margin:0 0 18px;font-size:13px;color:#6b7280;'>{escape(cadence)}</p>"
        f"<p style='{p}'>Best regards,<br><strong>IT Service Desk</strong><br>OctoAssist</p>"
        "<p style='margin:0;font-size:12px;color:#9ca3af;'>This is an automated notification. "
        "Please do not reply to this email.</p></div>")
    return text, html

'@
$block = $block.Replace('__PORTAL_URL__', $PortalUrl).Replace("`r`n", "`n")

$bk = "$Path.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item -LiteralPath $Path -Destination $bk -Force
Write-Host "backup      : $bk"

$new = $reBlock.Replace($text, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block + "`n" }, 1)
$new = $reSubj.Replace($new, 'subject = _digest_subject(rows, expired, expiring)', 1)
$idx = $new.IndexOf($call)
$new = $new.Substring(0, $idx) + '_digest_bodies(rows, tenant)' + $new.Substring($idx + $call.Length)
[System.IO.File]::WriteAllText($Path, $new, $utf8)

Write-Host "new size    : $((Get-Item -LiteralPath $Path).Length) bytes"
Write-Host 'Applied.' -ForegroundColor Green
if ($Restart) { Restart-Octo } else {
    Write-Host 'Python is loaded at startup, so RESTART the service:'
    Write-Host '  Get-Service *octo* | Restart-Service -Force'
}
Write-Host 'To undo:  .\Patch-SubscriptionEmail.ps1 -Rollback -Restart'
