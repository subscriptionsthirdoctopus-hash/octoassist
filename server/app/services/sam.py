"""Software Asset Management (SAM) service.

Aggregates installed-software inventory across all latest endpoint snapshots
for a tenant. Powers the /software fleet view and per-product detail pages.

Data source: AssetSnapshot.payload['software'] — a list of dicts with keys:
    name, version, publisher, install_date

The agent collects this on Windows from HKLM\\...\\Uninstall (Win32 + WOW6432)
and on Linux via dpkg-query (Phase 1 still supports both).

Categorisation is rule-based (publisher + name regex). It's intentionally
opinionated — Arun can tune the rules in CATEGORY_RULES as the fleet grows.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Agent, AssetSnapshot


# ---------------------------------------------------------------------------
# Categorisation rules
# ---------------------------------------------------------------------------
# Each rule is (category, publisher_regex, name_regex). First match wins.
# publisher_regex and name_regex are case-insensitive; either can be None to
# skip that check.
#
# Categories are the SAM-audit dimensions Arun cares about:
#   - Operating System & Drivers   (OS components, OEM drivers, runtime libs)
#   - Productivity                 (Office, mail, browsers, PDF)
#   - Developer Tools              (IDEs, SDKs, source control)
#   - Security                     (AV, EDR, VPN)
#   - Communication                (Teams, Slack, Zoom, Meet)
#   - Cloud / Storage              (OneDrive, Dropbox, Google Drive)
#   - Media                        (VLC, Spotify, iTunes)
#   - Utilities                    (7-Zip, Notepad++, WinRAR, PuTTY)
#   - Business / ERP               (SAP, Tally, Zoho, Salesforce)
#   - Other                        (uncategorised — review for SAM audit)
#
# License posture is derived in license_posture() based on publisher.

CATEGORY_RULES: list[tuple[str, str | None, str | None]] = [
    # OS / drivers / runtimes — usually pre-installed, not separately licensed
    ("Operating System & Drivers", r"^(microsoft corporation|microsoft)$",
     r"(windows|\.net|visual c\+\+|directx|onedrive sync engine)"),
    ("Operating System & Drivers", r"(intel|amd|nvidia|realtek|qualcomm|broadcom|conexant|synaptics|elan|dell inc|hp inc|lenovo|asus)",
     r"(driver|chipset|graphics|audio|wireless|bluetooth|lan|management engine|smart connect)"),

    # Security
    ("Security",         r"(symantec|mcafee|kaspersky|trend micro|sophos|crowdstrike|sentinelone|cylance|carbon black|bitdefender|malwarebytes|eset|webroot|fortinet)", None),
    ("Security",         None, r"(antivirus|endpoint protection|firewall|edr|vpn client|ironkey)"),
    ("Security",         r"(cisco|palo alto|check point|pulse secure|openvpn|wireguard|nordvpn|expressvpn)", r"(vpn|anyconnect|globalprotect|secure access)"),

    # Communication
    ("Communication",    None, r"^(microsoft teams|slack|zoom|webex|google meet|skype|whatsapp|discord)\b"),

    # Productivity (Office, mail, browsers, PDF)
    ("Productivity",     r"^(microsoft corporation|microsoft)$", r"(office|excel|word|powerpoint|outlook|onenote|access|publisher|visio|project|365|copilot)"),
    ("Productivity",     r"(adobe|foxit|nitro)", r"(acrobat|reader|pdf)"),
    ("Productivity",     None, r"^(google chrome|mozilla firefox|microsoft edge|brave|opera|vivaldi|safari)\b"),
    ("Productivity",     None, r"^(libreoffice|openoffice|wps office|onlyoffice)\b"),
    ("Productivity",     None, r"^(notion|evernote|todoist|trello|asana|monday)\b"),

    # Developer tools
    ("Developer Tools",  None, r"^(visual studio|vs code|jetbrains|intellij|pycharm|webstorm|goland|rider|clion|datagrip|android studio|xcode|eclipse|netbeans|sublime text|atom)\b"),
    ("Developer Tools",  None, r"^(git|github|gitlab|sourcetree|tortoise(git|svn)|fork)\b"),
    ("Developer Tools",  None, r"^(node\.?js|npm|python|ruby|go programming|openjdk|oracle jdk|java se|.net sdk|dotnet sdk|docker|kubernetes|minikube|podman|vagrant|powershell)\b"),
    ("Developer Tools",  None, r"^(postman|insomnia|wireshark|fiddler|charles|burp suite|datagrip|mysql workbench|pgadmin|dbeaver|tableplus)\b"),

    # Cloud / Storage
    ("Cloud / Storage",  None, r"^(onedrive|dropbox|google drive|box drive|backup and sync|aws cli|azure cli|gcloud|s3 browser|cyberduck)\b"),

    # Media
    ("Media",            None, r"^(vlc|spotify|itunes|windows media player|quicktime|audacity|obs|handbrake|kodi|plex|netflix|prime video)\b"),
    ("Media",            r"(adobe)", r"(photoshop|illustrator|premiere|after effects|lightroom|indesign|creative cloud|xd)"),

    # Utilities
    ("Utilities",        None, r"^(7-zip|winrar|winzip|peazip|notepad\+\+|putty|filezilla|teamviewer|anydesk|logmein|chrome remote desktop|ccleaner|rufus|balenaetcher|powertoys)\b"),
    ("Utilities",        r"^(microsoft corporation|microsoft)$", r"(autoupdate)"),

    # Business / ERP
    ("Business / ERP",   r"(sap|oracle|tally|zoho|salesforce|quickbooks|sage|microsoft dynamics|netsuite)", None),
    ("Business / ERP",   None, r"^(tally\.erp|zoho books|zoho one|salesforce|workday|servicenow)\b"),
]


def categorise(name: str, publisher: str | None) -> str:
    """Return the SAM category for a (name, publisher) pair."""
    n = (name or "").strip()
    p = (publisher or "").strip()
    if not n:
        return "Other"
    for category, pub_re, name_re in CATEGORY_RULES:
        if pub_re and not re.search(pub_re, p, re.IGNORECASE):
            continue
        if name_re and not re.search(name_re, n, re.IGNORECASE):
            continue
        return category
    return "Other"


# ---------------------------------------------------------------------------
# License posture
# ---------------------------------------------------------------------------
# Tag each (publisher, product) with a coarse posture so audit reviews can
# prioritise. These are rules of thumb — finance always has the final word.
#
#   licensed_paid     — commercial licence almost certainly required
#   licensed_oem      — covered by OEM bundle / Windows licence
#   free_personal     — free for personal use, paid for business (Slack, Zoom, etc.)
#   freeware_oss      — free / open-source / GPL
#   unknown           — needs human review

LICENSE_RULES: list[tuple[str, str | None, str | None]] = [
    # OS / OEM
    ("licensed_oem",  r"^(microsoft corporation|microsoft)$", r"(windows( \d+| server|))"),
    ("licensed_oem",  r"(intel|amd|nvidia|realtek|dell|hp|lenovo|asus|qualcomm|broadcom)", r"(driver|chipset|graphics|audio|wireless|bluetooth|smart connect|management engine)"),

    # Commercial / paid licences (most common SAM audit risks)
    ("licensed_paid", r"^(microsoft corporation|microsoft)$", r"(office|excel|word|powerpoint|outlook|onenote|access|publisher|visio|project|365)"),
    ("licensed_paid", r"(adobe)", r"(acrobat|photoshop|illustrator|premiere|after effects|lightroom|indesign|creative cloud|xd)"),
    ("licensed_paid", r"(jetbrains)", None),
    ("licensed_paid", r"(autodesk)", r"(autocad|revit|fusion|maya|3ds max)"),
    ("licensed_paid", r"(sap|oracle|tally|sage|salesforce|quickbooks|microsoft dynamics|netsuite)", None),
    ("licensed_paid", r"(symantec|mcafee|kaspersky|trend micro|sophos|crowdstrike|sentinelone|cylance|carbon black|bitdefender|webroot|fortinet)", None),
    ("licensed_paid", r"(vmware|citrix|nutanix|veeam)", None),

    # Free for personal, paid for business
    ("free_personal", None, r"^(microsoft teams|slack|zoom|webex|teamviewer|anydesk|logmein|notion|evernote|todoist|trello|asana|monday|netflix|spotify|prime video)\b"),

    # Freeware / OSS — usually safe in audits
    ("freeware_oss",  None, r"^(google chrome|mozilla firefox|brave|opera|vivaldi|libreoffice|openoffice|onlyoffice)\b"),
    ("freeware_oss",  None, r"^(7-zip|winrar trial|peazip|notepad\+\+|putty|filezilla|powertoys|powershell|git|github desktop|sourcetree|vs code|visual studio code)\b"),
    ("freeware_oss",  None, r"^(node\.?js|npm|python|ruby|go programming|openjdk|docker desktop|kubernetes|minikube|podman|vagrant|wireshark|audacity|obs studio|handbrake|kodi|vlc)\b"),

    # Microsoft AutoUpdate — bundled with Office, covered by the same licence
    ("licensed_oem",  r"^(microsoft corporation|microsoft)$", r"(autoupdate)"),
]


def license_posture(name: str, publisher: str | None) -> str:
    n = (name or "").strip()
    p = (publisher or "").strip()
    for posture, pub_re, name_re in LICENSE_RULES:
        if pub_re and not re.search(pub_re, p, re.IGNORECASE):
            continue
        if name_re and not re.search(name_re, n, re.IGNORECASE):
            continue
        return posture
    return "unknown"


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _latest_snapshots(db: Session, tenant_id: int):
    """Yield (Agent, payload-dict) for the latest snapshot per agent."""
    sub = (db.query(AssetSnapshot.agent_id,
                    func.max(AssetSnapshot.snapshot_at).label("latest"))
             .group_by(AssetSnapshot.agent_id).subquery())
    return (db.query(Agent, AssetSnapshot.payload)
              .join(AssetSnapshot, AssetSnapshot.agent_id == Agent.id)
              .join(sub, (AssetSnapshot.agent_id == sub.c.agent_id) &
                        (AssetSnapshot.snapshot_at == sub.c.latest))
              .filter(Agent.tenant_id == tenant_id)
              .all())


def _norm_publisher(p: str | None) -> str:
    s = (p or "").strip()
    if not s:
        return "Unknown publisher"
    # Trim common corporate suffixes so "Google LLC" / "Google Inc" collapse
    s = re.sub(r"[,]?\s+(inc|inc\.|llc|llc\.|ltd|ltd\.|corp|corp\.|corporation|gmbh|s\.a\.|s\.r\.l\.|co\.|co)$",
               "", s, flags=re.IGNORECASE).strip()
    return s


def _is_os_package(name: str, publisher: str) -> bool:
    """Skip Debian/Ubuntu dpkg entries — those are OS-level packages reported
    by the Linux agent and would drown out the actual SAM signal (which is
    installed *applications* on Windows endpoints).

    Heuristic: a Debian source-package maintainer is always an email address
    in angle brackets, e.g. "Benjamin Drung <bdrung@ubuntu.com>". HKLM
    uninstall keys on Windows never contain that pattern.
    """
    if "<" in publisher and "@" in publisher and publisher.endswith(">"):
        return True
    # Common dpkg / OS-package name prefixes we never want to surface as SAM
    name_l = name.lower()
    for prefix in ("linux-image-", "linux-headers-", "linux-modules-",
                   "linux-tools-", "linux-cloud-tools-",
                   "lib", "python3-", "perl-", "ruby-", "golang-",
                   "grub-", "initramfs-", "systemd-", "openssh-",
                   "ca-certificates", "apt-", "dpkg", "ubuntu-",
                   # macOS PKG receipts (system-level, not user-installed apps)
                   "com.apple.",
                   # DigitalOcean / cloud-agent infrastructure
                   "do-agent", "droplet-agent", "cloud-init",
                   "containerd", "snapd"):
        if name_l.startswith(prefix):
            return True
    # macOS Mac App Store receipts
    if "MASReceipt" in name:
        return True
    return False


# ---------------------------------------------------------------------------
# Bundle-ID canonicalisation
# ---------------------------------------------------------------------------
# Agents on macOS (and some Windows MSIs) report reverse-DNS bundle identifiers
# instead of human display names. Without normalisation, the SAM table is full
# of rows like "Unknown publisher / com.microsoft.teams2 / Other / Unknown".
#
# We solve this in two layers:
#   1. BUNDLE_CANONICAL — exact-match lookup table for known bundles, maps
#      to a curated (publisher, product) pair so the categoriser + licence
#      rules downstream fire correctly.
#   2. BUNDLE_VENDOR_PREFIX — when an exact match misses, derive the publisher
#      from the reverse-DNS prefix ("com.microsoft.*" -> "Microsoft") and
#      Title-Case the last segment as the product. Far better than "Unknown".

BUNDLE_CANONICAL: dict[str, tuple[str, str]] = {
    # Microsoft (Mac PKG receipts use these exact strings)
    "com.microsoft.teams":                              ("Microsoft", "Microsoft Teams"),
    "com.microsoft.teams2":                             ("Microsoft", "Microsoft Teams"),
    "com.microsoft.MSTeamsAudioDevice":                 ("Microsoft", "Microsoft Teams"),
    "com.microsoft.m365copilot":                        ("Microsoft", "Microsoft 365 Copilot"),
    "com.microsoft.powershell":                         ("Microsoft", "PowerShell"),
    "com.microsoft.package.Microsoft_AutoUpdate.app":   ("Microsoft", "Microsoft AutoUpdate"),
    "com.microsoft.autoupdate2":                        ("Microsoft", "Microsoft AutoUpdate"),
    "com.microsoft.Excel":                              ("Microsoft", "Microsoft Excel"),
    "com.microsoft.Word":                               ("Microsoft", "Microsoft Word"),
    "com.microsoft.Powerpoint":                         ("Microsoft", "Microsoft PowerPoint"),
    "com.microsoft.Outlook":                            ("Microsoft", "Microsoft Outlook"),
    "com.microsoft.onenote.mac":                        ("Microsoft", "Microsoft OneNote"),
    "com.microsoft.OneDrive":                           ("Microsoft", "OneDrive"),
    "com.microsoft.OneDrive-mac":                       ("Microsoft", "OneDrive"),
    "com.microsoft.edgemac":                            ("Microsoft", "Microsoft Edge"),
    "com.microsoft.VSCode":                             ("Microsoft", "Visual Studio Code"),
    "com.microsoft.copilot":                            ("Microsoft", "Microsoft Copilot"),
    "com.microsoft.RDC.macos":                          ("Microsoft", "Microsoft Remote Desktop"),
    "com.microsoft.SkypeForBusiness":                   ("Microsoft", "Skype for Business"),
    # Google
    "com.google.Chrome":                                ("Google", "Google Chrome"),
    "com.google.drivefs":                               ("Google", "Google Drive"),
    "com.google.driveeditfs":                           ("Google", "Google Drive"),
    "com.google.Gmail":                                 ("Google", "Gmail"),
    # Adobe
    "com.adobe.Acrobat.Pro":                            ("Adobe", "Adobe Acrobat Pro"),
    "com.adobe.Reader":                                 ("Adobe", "Adobe Acrobat Reader"),
    "com.adobe.acc.AdobeCreativeCloud":                 ("Adobe", "Adobe Creative Cloud"),
    "com.adobe.Photoshop":                              ("Adobe", "Adobe Photoshop"),
    "com.adobe.Illustrator":                            ("Adobe", "Adobe Illustrator"),
    "com.adobe.InDesign":                               ("Adobe", "Adobe InDesign"),
    "com.adobe.AfterEffects":                           ("Adobe", "Adobe After Effects"),
    "com.adobe.PremierePro":                            ("Adobe", "Adobe Premiere Pro"),
    # Mozilla
    "com.mozilla.firefox":                              ("Mozilla", "Firefox"),
    "org.mozilla.firefox":                              ("Mozilla", "Firefox"),
    "org.mozilla.thunderbird":                          ("Mozilla", "Thunderbird"),
    # Sophos
    "com.sophos.connect":                               ("Sophos", "Sophos Connect"),
    "com.sophos.endpoint":                              ("Sophos", "Sophos Endpoint"),
    "com.sophos.macendpoint":                           ("Sophos", "Sophos Endpoint"),
    # Node.js / dev
    "org.nodejs.node":                                  ("Node.js Foundation", "Node.js"),
    "org.nodejs.node.pkg":                              ("Node.js Foundation", "Node.js"),
    "org.nodejs.npm":                                   ("Node.js Foundation", "npm"),
    "org.nodejs.npm.pkg":                               ("Node.js Foundation", "npm"),
    "org.python.python":                                ("Python Software Foundation", "Python"),
    "com.docker.docker":                                ("Docker", "Docker Desktop"),
    "com.github.GitHubDesktop":                         ("GitHub", "GitHub Desktop"),
    "com.jetbrains.intellij":                           ("JetBrains", "IntelliJ IDEA"),
    "com.jetbrains.pycharm":                            ("JetBrains", "PyCharm"),
    "com.jetbrains.WebStorm":                           ("JetBrains", "WebStorm"),
    "com.jetbrains.goland":                             ("JetBrains", "GoLand"),
    "com.jetbrains.toolbox":                            ("JetBrains", "JetBrains Toolbox"),
    "com.sublimetext.4":                                ("Sublime HQ", "Sublime Text"),
    # Communication
    "us.zoom.xos":                                      ("Zoom", "Zoom"),
    "us.zoom.ZoomClips":                                ("Zoom", "Zoom"),
    "com.tinyspeck.slackmacgap":                        ("Slack", "Slack"),
    "com.cisco.webex.meetings":                         ("Cisco", "Webex"),
    "com.discord.Discord":                              ("Discord", "Discord"),
    "com.skype.skype":                                  ("Microsoft", "Skype"),
    "net.whatsapp.WhatsApp":                            ("Meta", "WhatsApp"),
    # Cloud / storage
    "com.dropbox.Dropbox":                              ("Dropbox", "Dropbox"),
    "com.box.desktop":                                  ("Box", "Box Drive"),
    # Security
    "com.crowdstrike.falcon.Agent":                     ("CrowdStrike", "Falcon Agent"),
    "com.sentinelone.S1Agent":                          ("SentinelOne", "SentinelOne Agent"),
    "com.bitdefender.Antivirus":                        ("Bitdefender", "Bitdefender Antivirus"),
    "com.mcafee.endpointsecurity":                      ("McAfee", "McAfee Endpoint Security"),
    "com.symantec.endpoint.protection":                 ("Symantec", "Symantec Endpoint Protection"),
    # Media / utilities
    "com.spotify.client":                               ("Spotify", "Spotify"),
    "org.videolan.vlc":                                 ("VideoLAN", "VLC"),
    "com.brave.Browser":                                ("Brave Software", "Brave"),
    "com.operasoftware.Opera":                          ("Opera", "Opera"),
}

# Prefix-to-publisher fallback for bundles we don't have an exact map for.
# Ordered: longest prefix wins (Python dict iteration is insertion-order so
# put more specific prefixes first).
BUNDLE_VENDOR_PREFIX: dict[str, str] = {
    "com.microsoft.": "Microsoft",
    "com.google.":    "Google",
    "com.adobe.":     "Adobe",
    "com.sophos.":    "Sophos",
    "com.mozilla.":   "Mozilla",
    "org.mozilla.":   "Mozilla",
    "org.nodejs.":    "Node.js Foundation",
    "org.python.":    "Python Software Foundation",
    "com.docker.":    "Docker",
    "com.github.":    "GitHub",
    "com.jetbrains.": "JetBrains",
    "com.atlassian.": "Atlassian",
    "com.tinyspeck.": "Slack",
    "us.zoom.":       "Zoom",
    "com.cisco.":     "Cisco",
    "com.discord.":   "Discord",
    "com.skype.":     "Microsoft",
    "net.whatsapp.":  "Meta",
    "com.dropbox.":   "Dropbox",
    "com.box.":       "Box",
    "com.crowdstrike.": "CrowdStrike",
    "com.sentinelone.": "SentinelOne",
    "com.bitdefender.": "Bitdefender",
    "com.mcafee.":      "McAfee",
    "com.symantec.":    "Symantec",
    "com.spotify.":     "Spotify",
    "org.videolan.":    "VideoLAN",
    "com.brave.":       "Brave Software",
    "com.operasoftware.": "Opera",
    "com.opera.":       "Opera",
    "com.vmware.":      "VMware",
    "com.citrix.":      "Citrix",
}

_BUNDLE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")


def _is_bundle_id(name: str) -> bool:
    """Reverse-DNS bundle ID: ≥3 dot-separated segments, lowercase TLD prefix."""
    if not name or " " in name:
        return False
    return bool(_BUNDLE_RE.match(name))


def _titlecase_segment(s: str) -> str:
    # Split camelCase first ("MSTeamsAudioDevice" -> "MS Teams Audio Device")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = s.replace("_", " ").replace("-", " ").strip()
    # Title-case but keep acronyms (≤3 letters all-caps) intact
    out = []
    for w in s.split():
        if len(w) <= 3 and w.isupper():
            out.append(w)
        elif w.isupper():
            out.append(w.title())
        else:
            out.append(w[0].upper() + w[1:] if w else w)
    return " ".join(out)


def _normalise_bundle(raw_name: str) -> tuple[str, str] | None:
    """If raw_name looks like a reverse-DNS bundle identifier, return a
    canonical (publisher, product). Else None.
    """
    if not _is_bundle_id(raw_name):
        return None
    # 1. Exact match
    if raw_name in BUNDLE_CANONICAL:
        return BUNDLE_CANONICAL[raw_name]
    # 2. Strip trailing .pkg / .app and retry
    trimmed = re.sub(r"\.(pkg|app)$", "", raw_name)
    if trimmed != raw_name and trimmed in BUNDLE_CANONICAL:
        return BUNDLE_CANONICAL[trimmed]
    # 3. Prefix-vendor + Title-Cased final segment
    for prefix, vendor in BUNDLE_VENDOR_PREFIX.items():
        if raw_name.startswith(prefix):
            parts = [p for p in raw_name.split(".") if p not in ("pkg", "app")]
            product = _titlecase_segment(parts[-1])
            return vendor, product
    return None


def _norm_name(n: str | None) -> str:
    """Collapse trivial version-in-name variations so different versions of
    the same product roll up.

      "Google Chrome"              -> "Google Chrome"
      "Microsoft Edge WebView2 Runtime" -> "Microsoft Edge WebView2 Runtime"
      "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.38..." -> base
    """
    s = (n or "").strip()
    if not s:
        return ""
    # Drop trailing version numbers like "Acrobat Reader 25.001.20458"
    s = re.sub(r"\s+\d+\.\d+(\.\d+){0,3}.*$", "", s).strip()
    # Drop architecture suffixes that fragment the same product
    s = re.sub(r"\s*\((x86|x64|32-bit|64-bit)\).*$", "", s, flags=re.IGNORECASE).strip()
    return s


# ---------------------------------------------------------------------------
# Public aggregates
# ---------------------------------------------------------------------------

def fleet_software(db: Session, tenant_id: int) -> list[dict]:
    """Roll up software across the fleet by (publisher, normalised name).

    Returns a list of dicts with:
        publisher, product, category, license_posture,
        install_count, endpoint_count, versions (sorted set),
        sample_hostnames (first 3), agent_ids (full)
    """
    rows = _latest_snapshots(db, tenant_id)

    # {(publisher, product): {versions:set, agent_ids:set, installs:set, hostnames:list}}
    #
    # `installs` is keyed on (agent_id, version), not on the raw registry row:
    # the 32-bit and 64-bit uninstall keys for one product are two rows that
    # normalise to the same (publisher, product, version), and counting both
    # inflated the Installs column and the category/publisher breakdowns fed
    # from it. `hostnames` is kept distinct so the sample never repeats a host.
    acc: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "versions": set(), "agent_ids": set(), "installs": set(),
        "seen_hosts": set(), "hostnames": [],
    })

    for agent, payload in rows:
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
            # Bundle-ID canonicalisation first — macOS PKG receipts use
            # reverse-DNS identifiers that would otherwise show up as
            # "Unknown publisher / com.foo.bar / Other / Unknown".
            bundle = _normalise_bundle(raw_name)
            if bundle is not None:
                publisher, name = bundle
            else:
                name = _norm_name(raw_name)
                if not name:
                    continue
                publisher = _norm_publisher(sw.get("publisher"))
            if _is_os_package(name, publisher):
                continue
            version = (sw.get("version") or "").strip() or "—"
            key = (publisher, name)
            bucket = acc[key]
            bucket["versions"].add(version)
            bucket["agent_ids"].add(agent.id)
            bucket["installs"].add((agent.id, version))
            if agent.hostname not in bucket["seen_hosts"]:
                bucket["seen_hosts"].add(agent.hostname)
                bucket["hostnames"].append(agent.hostname)

    out = []
    for (publisher, product), bucket in acc.items():
        out.append({
            "publisher":        publisher,
            "product":          product,
            "category":         categorise(product, publisher),
            "license_posture":  license_posture(product, publisher),
            "install_count":    len(bucket["installs"]),
            "endpoint_count":   len(bucket["agent_ids"]),
            "versions":         sorted(bucket["versions"]),
            "sample_hostnames": bucket["hostnames"][:3],
            "agent_ids":        sorted(bucket["agent_ids"]),
        })
    out.sort(key=lambda r: (-r["install_count"], r["publisher"].lower(), r["product"].lower()))
    return out


def fleet_kpis(db: Session, tenant_id: int) -> dict:
    rows = fleet_software(db, tenant_id)
    endpoint_count = (db.query(func.count(Agent.id))
                        .filter(Agent.tenant_id == tenant_id).scalar()) or 0
    distinct_products = len(rows)
    distinct_publishers = len({r["publisher"] for r in rows})
    licensed_paid = sum(1 for r in rows if r["license_posture"] == "licensed_paid")
    licensed_paid_installs = sum(r["install_count"] for r in rows if r["license_posture"] == "licensed_paid")
    unknown_posture = sum(1 for r in rows if r["license_posture"] == "unknown")
    return {
        "endpoint_count":        endpoint_count,
        "distinct_products":     distinct_products,
        "distinct_publishers":   distinct_publishers,
        "licensed_paid":         licensed_paid,
        "licensed_paid_installs": licensed_paid_installs,
        "unknown_posture":       unknown_posture,
    }


def category_breakdown(rows: Iterable[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["category"]] += r["install_count"]
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def publisher_breakdown(rows: Iterable[dict], top: int = 15) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["publisher"]] += r["install_count"]
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]


def license_breakdown(rows: Iterable[dict]) -> list[tuple[str, int]]:
    order = ["licensed_paid", "licensed_oem", "free_personal", "freeware_oss", "unknown"]
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["license_posture"]] += r["install_count"]
    return [(k, counts.get(k, 0)) for k in order if counts.get(k)]


def product_detail(db: Session, tenant_id: int, publisher: str, product: str) -> dict | None:
    """Per-product detail page: one row per endpoint that has this software.

    A single machine routinely reports the same product more than once — the
    32-bit and 64-bit uninstall keys are separate registry entries, and
    _norm_name() deliberately collapses the "(x64)" / "(x86)" suffixes so both
    roll up to one product. That is correct for the fleet roll-up, but it used
    to emit one row per registry entry here, so the same hostname appeared two
    or three times over on the product page.

    Endpoints are now keyed by agent id. Where a machine does carry genuinely
    different versions side by side, they are joined into the one row rather
    than duplicating the host.
    """
    rows = _latest_snapshots(db, tenant_id)
    by_agent: dict[int, dict] = {}
    for agent, payload in rows:
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
            bundle = _normalise_bundle(raw_name)
            if bundle is not None:
                pub, name = bundle
            else:
                name = _norm_name(raw_name)
                pub  = _norm_publisher(sw.get("publisher"))
            if name != product or pub != publisher:
                continue
            version = (sw.get("version") or "").strip() or "—"
            entry = by_agent.get(agent.id)
            if entry is None:
                entry = by_agent[agent.id] = {
                    "agent_id":     agent.id,
                    "hostname":     agent.hostname,
                    "last_seen_at": agent.last_seen_at,
                    "raw_names":    [],
                    "versions":     [],
                    "install_date": None,
                }
            if version not in entry["versions"]:
                entry["versions"].append(version)
            if raw_name and raw_name not in entry["raw_names"]:
                entry["raw_names"].append(raw_name)
            # Keep the first install date we see — the duplicate keys carry the
            # same date, and an empty one shouldn't overwrite a real one.
            if not entry["install_date"] and sw.get("install_date"):
                entry["install_date"] = sw.get("install_date")
    if not by_agent:
        return None

    # Version spread is counted per endpoint, so a machine with duplicate
    # registry entries for one version no longer inflates that version's share.
    versions: dict[str, int] = defaultdict(int)
    matches = []
    for entry in by_agent.values():
        for v in entry["versions"]:
            versions[v] += 1
        matches.append({
            "agent_id":     entry["agent_id"],
            "hostname":     entry["hostname"],
            "last_seen_at": entry["last_seen_at"],
            "raw_name":     ", ".join(entry["raw_names"]) or "—",
            "version":      ", ".join(entry["versions"]) or "—",
            "install_date": entry["install_date"] or "—",
        })
    matches.sort(key=lambda m: m["hostname"].lower())
    return {
        "publisher":       publisher,
        "product":         product,
        "category":        categorise(product, publisher),
        "license_posture": license_posture(product, publisher),
        "install_count":   len(matches),
        "version_breakdown": sorted(versions.items(), key=lambda kv: kv[1], reverse=True),
        "endpoints":       matches,
    }


# ---------------------------------------------------------------------------
# CSV export for SAM audit submissions
# ---------------------------------------------------------------------------

def export_csv(db: Session, tenant_id: int) -> str:
    """Long-form CSV: one row per (endpoint, product, version).

    Suitable for handing to a SAM auditor or importing into a license tool.
    """
    rows = _latest_snapshots(db, tenant_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "hostname", "agent_id", "publisher", "product", "version", "install_date",
        "category", "license_posture", "raw_name", "snapshot_at",
    ])
    for agent, payload in rows:
        snap_at = (payload or {}).get("collected_at") or ""
        # One row per (endpoint, product, version), as the docstring promises.
        # Without this the x86/x64 uninstall keys emit two rows for the same
        # install and an auditor double-counts the licence.
        seen: set[tuple[str, str, str]] = set()
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
            bundle = _normalise_bundle(raw_name)
            if bundle is not None:
                publisher, name = bundle
            else:
                name = _norm_name(raw_name)
                if not name:
                    continue
                publisher = _norm_publisher(sw.get("publisher"))
            if _is_os_package(name, publisher):
                continue
            version = (sw.get("version") or "").strip() or ""
            key = (publisher, name, version)
            if key in seen:
                continue
            seen.add(key)
            w.writerow([
                agent.hostname, agent.id, publisher, name, version,
                sw.get("install_date") or "",
                categorise(name, publisher),
                license_posture(name, publisher),
                raw_name, snap_at,
            ])
    buf.seek(0)
    return buf.read()
