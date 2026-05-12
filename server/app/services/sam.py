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
    ("Productivity",     r"^(microsoft corporation|microsoft)$", r"(office|excel|word|powerpoint|outlook|onenote|access|publisher|visio|project)"),
    ("Productivity",     r"(adobe|foxit|nitro)", r"(acrobat|reader|pdf)"),
    ("Productivity",     None, r"^(google chrome|mozilla firefox|microsoft edge|brave|opera|vivaldi|safari)\b"),
    ("Productivity",     None, r"^(libreoffice|openoffice|wps office|onlyoffice)\b"),
    ("Productivity",     None, r"^(notion|evernote|todoist|trello|asana|monday)\b"),

    # Developer tools
    ("Developer Tools",  None, r"^(visual studio|vs code|jetbrains|intellij|pycharm|webstorm|goland|rider|clion|datagrip|android studio|xcode|eclipse|netbeans|sublime text|atom)\b"),
    ("Developer Tools",  None, r"^(git|github|gitlab|sourcetree|tortoise(git|svn)|fork)\b"),
    ("Developer Tools",  None, r"^(node\.?js|python|ruby|go programming|openjdk|oracle jdk|java se|.net sdk|dotnet sdk|docker|kubernetes|minikube|podman|vagrant)\b"),
    ("Developer Tools",  None, r"^(postman|insomnia|wireshark|fiddler|charles|burp suite|datagrip|mysql workbench|pgadmin|dbeaver|tableplus)\b"),

    # Cloud / Storage
    ("Cloud / Storage",  None, r"^(onedrive|dropbox|google drive|box drive|backup and sync|aws cli|azure cli|gcloud|s3 browser|cyberduck)\b"),

    # Media
    ("Media",            None, r"^(vlc|spotify|itunes|windows media player|quicktime|audacity|obs|handbrake|kodi|plex|netflix|prime video)\b"),
    ("Media",            r"(adobe)", r"(photoshop|illustrator|premiere|after effects|lightroom|indesign|creative cloud|xd)"),

    # Utilities
    ("Utilities",        None, r"^(7-zip|winrar|winzip|peazip|notepad\+\+|putty|filezilla|teamviewer|anydesk|logmein|chrome remote desktop|ccleaner|rufus|balenaetcher|powertoys)\b"),

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
    ("freeware_oss",  None, r"^(node\.?js|python|ruby|go programming|openjdk|docker desktop|kubernetes|minikube|podman|vagrant|wireshark|audacity|obs studio|handbrake|kodi|vlc)\b"),
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

    # {(publisher, product): {versions:set, agent_ids:set, hostnames:list[(hn,ver)]}}
    acc: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "versions": set(), "agent_ids": set(), "hostnames": [],
    })

    for agent, payload in rows:
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
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
            bucket["hostnames"].append((agent.hostname, version))

    out = []
    for (publisher, product), bucket in acc.items():
        out.append({
            "publisher":        publisher,
            "product":          product,
            "category":         categorise(product, publisher),
            "license_posture":  license_posture(product, publisher),
            "install_count":    len(bucket["hostnames"]),
            "endpoint_count":   len(bucket["agent_ids"]),
            "versions":         sorted(bucket["versions"]),
            "sample_hostnames": [h for h, _ in bucket["hostnames"][:3]],
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
    """Per-product detail page: every endpoint that has this software, with version."""
    rows = _latest_snapshots(db, tenant_id)
    matches = []
    versions: dict[str, int] = defaultdict(int)
    for agent, payload in rows:
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
            name = _norm_name(raw_name)
            pub  = _norm_publisher(sw.get("publisher"))
            if name == product and pub == publisher:
                version = (sw.get("version") or "").strip() or "—"
                versions[version] += 1
                matches.append({
                    "agent_id":     agent.id,
                    "hostname":     agent.hostname,
                    "last_seen_at": agent.last_seen_at,
                    "raw_name":     raw_name,
                    "version":      version,
                    "install_date": sw.get("install_date") or "—",
                })
    if not matches:
        return None
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
        for sw in (payload or {}).get("software", []) or []:
            raw_name = sw.get("name") or ""
            name = _norm_name(raw_name)
            if not name:
                continue
            publisher = _norm_publisher(sw.get("publisher"))
            if _is_os_package(name, publisher):
                continue
            version = (sw.get("version") or "").strip() or ""
            w.writerow([
                agent.hostname, agent.id, publisher, name, version,
                sw.get("install_date") or "",
                categorise(name, publisher),
                license_posture(name, publisher),
                raw_name, snap_at,
            ])
    buf.seek(0)
    return buf.read()
