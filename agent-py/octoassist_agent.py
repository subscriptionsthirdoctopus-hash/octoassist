#!/usr/bin/env python3
# octoassist_agent.py
# Cross-platform OctoAssist asset agent.
# Standard library only — no pip deps.
#
# Tested on:
#   - Ubuntu 22.04 / 24.04
#   - Debian 12
#   - Windows 10 / 11 (Python 3.10+)
#
# Usage:
#   sudo octoassist-agent --bootstrap \
#        --server-url=https://octoassist.thirdoctopus.com \
#        --enrolment-key=XXXXXXXX
#   octoassist-agent --once     # single check-in
#   octoassist-agent            # run as daemon (every 6h)
"""
Config:
  /etc/octoassist/agent.json                   (Linux / macOS)
  C:\\ProgramData\\OctoAssist\\agent.json      (Windows)

  {
    "server_url": "https://octoassist.thirdoctopus.com",
    "enrolment_key": "...",
    "agent_id": 17,
    "agent_token": "...",
    "machine_id": "...",
    "checkin_interval_hours": 6
  }
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.1.0"

# --------------------------------------------------------------------- paths

if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "OctoAssist"
else:
    CONFIG_DIR = Path("/etc/octoassist")

CONFIG_PATH = CONFIG_DIR / "agent.json"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------- logging setup

log = logging.getLogger("octoassist-agent")


def setup_logging(level: int = logging.INFO) -> None:
    handlers = [logging.StreamHandler(sys.stderr)]
    # Best-effort log file
    try:
        log_dir = Path("/var/log/octoassist") if os.name != "nt" else (CONFIG_DIR / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "agent.log"))
    except Exception:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------- config IO

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"Agent not bootstrapped. Run: octoassist-agent --bootstrap "
            f"--server-url=URL --enrolment-key=KEY  (config path: {CONFIG_PATH})"
        )
    return json.loads(CONFIG_PATH.read_text())


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)


# ---------------------------------------------------------- HTTP

def _http(url: str, method: str = "GET", body: dict | None = None,
          token: str | None = None, timeout: int = 30) -> tuple[int, str]:
    headers = {"Content-Type": "application/json", "User-Agent": f"OctoAssistAgent/{VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        log.error("HTTP error %s: %s", url, e)
        return 0, str(e)


# ---------------------------------------------------------- machine id

def get_machine_id() -> str:
    """Stable identifier for this host. Best effort across platforms."""
    if os.name != "nt":
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                v = Path(path).read_text().strip()
                if v:
                    return v
            except Exception:
                continue
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["ioreg", "-d2", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10,
            )
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]+)"', r.stdout)
            if m:
                return m.group(1).lower()
        except Exception:
            pass
    if os.name == "nt":
        try:
            r = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=10,
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line and line.upper() != "UUID":
                    return line.lower()
        except Exception:
            pass
        # Fallback via PowerShell
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                capture_output=True, text=True, timeout=10,
            )
            v = r.stdout.strip()
            if v:
                return v.lower()
        except Exception:
            pass
    # Last resort: hostname
    return socket.gethostname().lower() + "-fallback"


# ---------------------------------------------------------- inventory

def _read_os_release() -> dict:
    out: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _linux_os_info() -> dict:
    rel = _read_os_release()
    return {
        "caption": rel.get("PRETTY_NAME") or platform.platform(),
        "version": rel.get("VERSION_ID") or platform.release(),
        "build_number": platform.release(),
        "architecture": platform.machine(),
        "install_date": None,
    }


def _macos_os_info() -> dict:
    try:
        v = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=5).stdout.strip()
        b = subprocess.run(["sw_vers", "-buildVersion"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        v, b = "", ""
    return {
        "caption": f"macOS {v}".strip(),
        "version": v,
        "build_number": b,
        "architecture": platform.machine(),
        "install_date": None,
    }


def _windows_os_info() -> dict:
    """OS info on Windows. Pulls in one PowerShell hop:
      - Caption / Version / Build / Architecture
      - InstallDate (ISO yyyy-MM-dd — server re-formats to dd/mm/yyyy)
      - OEM (BIOS-embedded) product key via OA3xOriginalProductKey
      - Activation status (Licensed / OOB Grace / Unlicensed / …)
    Each piece is in one try block — partial failures fall back to a
    minimal platform.* dict so the agent never bombs on this collector.
    """
    out = {
        "caption": "", "version": "", "build_number": "", "architecture": "",
        "install_date": None, "product_key": None, "activation_status": None,
    }
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os  = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1
$lic = Get-CimInstance SoftwareLicensingService | Select-Object -First 1
$oa3 = if ($lic) { $lic.OA3xOriginalProductKey } else { '' }
$slp = Get-CimInstance SoftwareLicensingProduct |
       Where-Object { $_.PartialProductKey -and $_.Name -like 'Windows*' } |
       Select-Object -First 1
$states = @{ 0='Unlicensed'; 1='Licensed'; 2='OOB Grace';
             3='OOT Grace'; 4='Non-Genuine Grace'; 5='Notification'; 6='Extended Grace' }
$state  = if ($slp) { $states[[int]$slp.LicenseStatus] } else { 'Unknown' }
$install = if ($os.InstallDate) { $os.InstallDate.ToString('yyyy-MM-dd') } else { '' }
@{
    Caption        = $os.Caption
    Version        = $os.Version
    BuildNumber    = "$($os.BuildNumber)"
    OSArchitecture = $os.OSArchitecture
    InstallDate    = $install
    ProductKey     = $oa3
    Activation     = $state
} | ConvertTo-Json -Compress
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        d = json.loads(r.stdout or "{}")
        out["caption"]      = (d.get("Caption") or "").strip()
        out["version"]      = d.get("Version") or ""
        out["build_number"] = str(d.get("BuildNumber") or "")
        out["architecture"] = d.get("OSArchitecture") or ""
        out["install_date"] = d.get("InstallDate") or None
        pk = (d.get("ProductKey") or "").strip()
        out["product_key"]  = pk if pk else None
        out["activation_status"] = (d.get("Activation") or "").strip() or None
    except Exception:
        out["caption"]      = platform.platform()
        out["version"]      = platform.release()
        out["architecture"] = platform.machine()
    return out


def os_info() -> dict:
    if platform.system() == "Linux":   return _linux_os_info()
    if platform.system() == "Darwin":  return _macos_os_info()
    if platform.system() == "Windows": return _windows_os_info()
    return {"caption": platform.platform(), "version": platform.release(),
            "build_number": "", "architecture": platform.machine(), "install_date": None}


def cpu_info() -> dict:
    cores = os.cpu_count() or 0
    name = ""
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor)[0].Name"],
                capture_output=True, text=True, timeout=10,
            )
            name = r.stdout.strip()
        except Exception:
            pass
    return {"name": name or platform.processor(), "cores": cores,
            "logical_processors": cores, "max_clock_mhz": None}


def memory_info() -> dict:
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return {"total_gb": round(kb / 1024 / 1024, 2)}
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return {"total_gb": round(int(r.stdout.strip()) / 1024 / 1024 / 1024, 2)}
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=10,
            )
            return {"total_gb": round(int(r.stdout.strip()) / 1024 / 1024 / 1024, 2)}
        except Exception:
            pass
    return {"total_gb": None}


def disks_info() -> list[dict]:
    disks: list[dict] = []
    if platform.system() in ("Linux", "Darwin"):
        try:
            r = subprocess.run(["df", "-Pk"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 6:
                    continue
                fs, total_kb, _used, free_kb, _pct, mountpoint = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                # Skip pseudo filesystems
                if fs in ("tmpfs", "devtmpfs", "overlay", "shm", "udev") or mountpoint.startswith(("/proc", "/sys", "/run", "/dev")):
                    continue
                try:
                    size_gb = round(int(total_kb) / 1024 / 1024, 2)
                    free_gb = round(int(free_kb) / 1024 / 1024, 2)
                except ValueError:
                    continue
                disks.append({"drive": mountpoint, "filesystem": fs, "size_gb": size_gb, "free_gb": free_gb})
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
                 "Select-Object DeviceID,FileSystem,Size,FreeSpace | "
                 "ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for d in data:
                size = int(d.get("Size") or 0)
                free = int(d.get("FreeSpace") or 0)
                disks.append({
                    "drive": d.get("DeviceID", ""),
                    "filesystem": d.get("FileSystem"),
                    "size_gb": round(size / 1024 / 1024 / 1024, 2) if size else None,
                    "free_gb": round(free / 1024 / 1024 / 1024, 2) if free else None,
                })
        except Exception:
            pass
    return disks


def network_info() -> list[dict]:
    nets: list[dict] = []
    if platform.system() == "Linux":
        try:
            r = subprocess.run(["ip", "-json", "addr"], capture_output=True, text=True, timeout=10)
            for nic in json.loads(r.stdout or "[]"):
                if nic.get("operstate") != "UP" or nic.get("link_type") == "loopback":
                    continue
                mac = nic.get("address", "")
                ip = next((a.get("local") for a in nic.get("addr_info", []) if a.get("family") == "inet"), None)
                nets.append({"name": nic.get("ifname"), "mac": mac.upper() if mac else None, "ip": ip})
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            r = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=10)
            cur = None
            for line in r.stdout.splitlines():
                if line and not line.startswith("\t") and ":" in line:
                    cur = line.split(":", 1)[0]
                if cur and cur.startswith("lo"):
                    cur = None
                    continue
                if cur and "ether" in line:
                    mac = line.split()[1].upper()
                elif cur and " inet " in line and "broadcast" in line:
                    ip = line.split()[1]
                    nets.append({"name": cur, "mac": mac if "mac" in locals() else None, "ip": ip})
                    cur = None
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetAdapter | Where-Object Status -eq 'Up' | "
                 "ForEach-Object { $ip = (Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress; "
                 "[pscustomobject]@{ name=$_.Name; mac=$_.MacAddress; ip=$ip } } | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for d in data:
                nets.append({"name": d.get("name"), "mac": d.get("mac"), "ip": d.get("ip")})
        except Exception:
            pass
    return nets


def bios_info() -> dict:
    if platform.system() == "Linux":
        info = {"manufacturer": None, "version": None, "serial": None}
        for fld, path in (("manufacturer", "/sys/class/dmi/id/sys_vendor"),
                          ("version", "/sys/class/dmi/id/bios_version"),
                          ("serial", "/sys/class/dmi/id/product_serial")):
            try:
                info[fld] = Path(path).read_text().strip()
            except Exception:
                pass
        return info
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,SerialNumber | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15,
            )
            d = json.loads(r.stdout or "{}")
            return {"manufacturer": d.get("Manufacturer"), "version": d.get("SMBIOSBIOSVersion"), "serial": d.get("SerialNumber")}
        except Exception:
            pass
    return {"manufacturer": None, "version": None, "serial": None}


def system_info() -> dict:
    if platform.system() == "Linux":
        d = {"manufacturer": None, "model": None, "domain": None, "workgroup": None}
        for fld, path in (("manufacturer", "/sys/class/dmi/id/sys_vendor"),
                          ("model", "/sys/class/dmi/id/product_name")):
            try:
                d[fld] = Path(path).read_text().strip()
            except Exception:
                pass
        return d
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_ComputerSystem | "
                 "Select-Object Manufacturer,Model,Domain,Workgroup,PartOfDomain | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15,
            )
            d = json.loads(r.stdout or "{}")
            part_of = bool(d.get("PartOfDomain"))
            return {"manufacturer": d.get("Manufacturer"), "model": d.get("Model"),
                    "domain": d.get("Domain") if part_of else None,
                    "workgroup": d.get("Workgroup") if not part_of else None}
        except Exception:
            pass
    return {"manufacturer": None, "model": None, "domain": None, "workgroup": None}


def logged_in_user() -> str | None:
    if platform.system() in ("Linux", "Darwin"):
        try:
            r = subprocess.run(["who"], capture_output=True, text=True, timeout=5)
            users = []
            for line in r.stdout.splitlines():
                u = line.split()[0] if line.split() else ""
                if u and u not in users:
                    users.append(u)
            return ", ".join(users) if users else None
        except Exception:
            return os.environ.get("USER")
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystem).UserName"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() or None
        except Exception:
            return os.environ.get("USERNAME")
    return None


def installed_software() -> list[dict]:
    if platform.system() == "Linux":
        out = []
        try:
            r = subprocess.run(
                ["dpkg-query", "-W", "-f=${Package}|||${Version}|||${Maintainer}\\n"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split("|||")
                    if len(parts) == 3:
                        out.append({"name": parts[0], "version": parts[1],
                                    "publisher": parts[2], "install_date": None})
                return out
        except Exception:
            pass
        try:
            r = subprocess.run(["rpm", "-qa", "--queryformat",
                                "%{NAME}|||%{VERSION}|||%{VENDOR}\n"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split("|||")
                    if len(parts) == 3:
                        out.append({"name": parts[0], "version": parts[1],
                                    "publisher": parts[2], "install_date": None})
        except Exception:
            pass
        return out
    if platform.system() == "Darwin":
        out = []
        try:
            r = subprocess.run(["pkgutil", "--pkgs"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    out.append({"name": line, "version": "", "publisher": "", "install_date": None})
        except Exception:
            pass
        return out
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                 "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
                 "-ErrorAction SilentlyContinue | Where-Object DisplayName | "
                 "Select-Object DisplayName, DisplayVersion, Publisher, InstallDate | "
                 "ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            return [{"name": d.get("DisplayName"), "version": d.get("DisplayVersion"),
                     "publisher": d.get("Publisher"), "install_date": d.get("InstallDate")}
                    for d in data if d.get("DisplayName")]
        except Exception:
            return []
    return []


def patches_available() -> list[dict]:
    """Collect patches/updates that haven't been installed on this host."""
    out: list[dict] = []
    if platform.system() == "Linux":
        # apt list --upgradable (Debian/Ubuntu).
        try:
            env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
            # apt-get update first if recent metadata isn't present (best-effort).
            # We deliberately skip auto-update — it requires root and writes lock files.
            r = subprocess.run(
                ["apt", "list", "--upgradable"],
                capture_output=True, text=True, timeout=60, env=env,
            )
            for line in r.stdout.splitlines():
                # Format:  pkg/repo,suite version arch [upgradable from: oldver]
                if "upgradable from:" not in line or "/" not in line:
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                pkg_repo = parts[0]
                name = pkg_repo.split("/", 1)[0]
                repo = pkg_repo.split("/", 1)[1] if "/" in pkg_repo else ""
                new_ver = parts[1]
                old_ver = parts[-1].rstrip("]").strip()
                # Heuristic: anything from a *-security repo is at least "important".
                sev = "important" if "-security" in repo.lower() else "moderate"
                out.append({
                    "name": name,
                    "current_version": old_ver if old_ver else None,
                    "available_version": new_ver,
                    "severity": sev,
                    "source": "apt:" + (repo.split(",", 1)[0] or "unknown"),
                    "title": f"Upgrade {name} {old_ver} → {new_ver}",
                })
        except Exception as e:
            log.warning("apt list --upgradable failed: %s", e)
        # Also try dnf check-update for RHEL-family.
        if not out:
            try:
                r = subprocess.run(
                    ["dnf", "-q", "check-update"],
                    capture_output=True, text=True, timeout=60,
                )
                # Exit code 100 is "updates available" — not a failure.
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] and not parts[0].startswith("Last"):
                        name, ver, repo = parts[0], parts[1], parts[2]
                        sev = "important" if "security" in repo.lower() else "moderate"
                        out.append({
                            "name": name,
                            "current_version": None,
                            "available_version": ver,
                            "severity": sev,
                            "source": f"dnf:{repo}",
                            "title": f"Upgrade {name} → {ver}",
                        })
            except Exception:
                pass
    elif platform.system() == "Windows":
        # Two sources on Windows:
        #   1. Windows Update via PSWindowsUpdate (KBs)
        #   2. Generic third-party software via winget (Chrome, Adobe, Zoom, etc.)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "if (Get-Module -ListAvailable PSWindowsUpdate) { "
                 "Import-Module PSWindowsUpdate; "
                 "Get-WindowsUpdate -ErrorAction SilentlyContinue | "
                 "Select-Object KB, Title, MsrcSeverity | ConvertTo-Json -Compress "
                 "} else { '[]' }"],
                capture_output=True, text=True, timeout=120,
            )
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for u in data:
                kb = u.get("KB", "") or ""
                title = u.get("Title", "")
                sev = (u.get("MsrcSeverity") or "unknown").lower()
                if sev not in ("critical", "important", "moderate", "low", "unknown"):
                    sev = "unknown"
                out.append({
                    "name": kb or (title[:80] or "update"),
                    "current_version": None,
                    "available_version": None,
                    "severity": sev,
                    "source": "windows-update",
                    "title": title,
                })
        except Exception:
            pass
        # Phase 7: winget — generic third-party software updates.
        out.extend(_winget_available_updates())
    return out[:5000]


def _winget_available_updates() -> list[dict]:
    """List packages with upgrades available via winget.

    winget output is (sadly) text-mode. We parse the table:
        Name      Id                  Version  Available  Source
        --------  ------------------  -------  ---------  ------
        Chrome    Google.Chrome       130.0.x  131.0.y    winget

    severity is `moderate` for everything (winget doesn't surface CVE info);
    admin can re-classify in OctoAssist if needed.
    """
    if platform.system() != "Windows":
        return []
    out: list[dict] = []
    try:
        # Auto-accept source agreements so first-run doesn't prompt.
        r = subprocess.run(
            ["winget", "upgrade",
             "--include-unknown",
             "--accept-source-agreements"],
            capture_output=True, text=True, timeout=120,
        )
        # Find the header separator line ("---  ---  ...") and parse fixed-width columns.
        lines = r.stdout.splitlines()
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("---"):
                header_idx = i
                break
        if header_idx is None or header_idx == 0:
            return out
        header = lines[header_idx - 1]
        # Discover column starts by scanning the dash line
        sep = lines[header_idx]
        # Build (start, end) ranges from the header line
        starts = []
        in_dash = False
        for j, ch in enumerate(sep):
            if ch == "-" and not in_dash:
                starts.append(j); in_dash = True
            elif ch != "-" and in_dash:
                in_dash = False
        starts.append(len(sep) + 1)
        ranges = list(zip(starts, starts[1:]))

        def col(line: str, idx: int) -> str:
            s, e = ranges[idx] if idx < len(ranges) else (0, 0)
            return line[s:e].strip() if s < len(line) else ""

        # Determine column-name → idx
        cols = [col(header, i) for i in range(len(ranges))]
        try:
            i_name      = cols.index("Name")
            i_id        = cols.index("Id")
            i_version   = cols.index("Version")
            i_available = cols.index("Available")
            i_source    = cols.index("Source") if "Source" in cols else -1
        except ValueError:
            return out

        for line in lines[header_idx + 1:]:
            if not line.strip() or line.startswith("Total ") or line.startswith("upgrades"):
                break
            name      = col(line, i_name)
            pkg_id    = col(line, i_id)
            ver       = col(line, i_version)
            available = col(line, i_available)
            source    = col(line, i_source) if i_source >= 0 else "winget"
            if not pkg_id or available in ("", "<", ">"):
                continue
            out.append({
                "name":             pkg_id,            # use Id (unique) as the key
                "current_version":  ver or None,
                "available_version": available,
                "severity":         "moderate",        # winget doesn't surface CVE/severity
                "source":           f"winget:{source}" if source else "winget",
                "title":            f"Upgrade {name or pkg_id} {ver} → {available}",
            })
    except FileNotFoundError:
        # winget not installed — Win10 < 1809 or not yet installed via Store
        pass
    except Exception as e:
        log.warning("winget upgrade parse failed: %s", e)
    return out


def _windows_enforce_managed_update_policy() -> dict:
    """Set WindowsUpdate Group-Policy registry keys so the end user sees
    "Some settings are managed by your organisation" and can't run Windows
    Update manually. Idempotent — safe to call every check-in.

    OctoAssist (running as SYSTEM via Scheduled Task) keeps applying
    Get-WindowsUpdate / Install-WindowsUpdate programmatically through
    PSWindowsUpdate, so updates STILL happen — just centrally controlled.
    """
    if platform.system() != "Windows":
        return {"applied": False, "reason": "not windows"}
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$base = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$au   = "$base\AU"
foreach ($p in @($base, $au)) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }
# Hide the Windows Update page in Settings — triggers the "managed by your organisation" banner.
Set-ItemProperty -Path $base -Name 'SetDisableUXWUAccess' -Type DWord -Value 1
# Block automatic install — agent drives the schedule via PSWindowsUpdate.
Set-ItemProperty -Path $au   -Name 'NoAutoUpdate'        -Type DWord -Value 1
# Don't auto-restart while a user is signed in (the agent picks reboot timing).
Set-ItemProperty -Path $au   -Name 'NoAutoRebootWithLoggedOnUsers' -Type DWord -Value 1
# Defer feature + quality updates so end users can't fast-ring themselves.
Set-ItemProperty -Path $base -Name 'DeferFeatureUpdates'  -Type DWord -Value 1
Set-ItemProperty -Path $base -Name 'DeferQualityUpdates'  -Type DWord -Value 1
'OK'
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        ok = "OK" in (r.stdout or "")
        return {"applied": ok, "reason": (r.stderr or "")[:200] if not ok else ""}
    except Exception as e:  # noqa: BLE001
        return {"applied": False, "reason": str(e)[:200]}


def _patch_scan_metadata() -> dict:
    """Probe whether the patch scan completed successfully (was PSWindowsUpdate
    installed? did Get-WindowsUpdate return data? when?). Used by the server
    to render '✓ Fully Updated' vs '○ N patches pending' vs '⚠ Scan failed'.
    """
    if platform.system() != "Windows":
        return {"scanned_at": _now_iso(), "scan_success": True,
                "psw_installed": False, "winget_available": False,
                "sources_checked": ["apt" if platform.system() == "Linux" else "n/a"]}
    psw = False
    winget_ok = False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-Module -ListAvailable PSWindowsUpdate) { 'yes' } else { 'no' }"],
            capture_output=True, text=True, timeout=15,
        )
        psw = "yes" in (r.stdout or "")
    except Exception:
        pass
    try:
        r = subprocess.run(["winget", "--version"], capture_output=True, text=True, timeout=8)
        winget_ok = r.returncode == 0
    except Exception:
        pass
    sources = []
    if psw: sources.append("PSWindowsUpdate")
    if winget_ok: sources.append("winget")
    return {
        "scanned_at": _now_iso(),
        "scan_success": (psw or winget_ok),
        "psw_installed": psw,
        "winget_available": winget_ok,
        "sources_checked": sources,
    }


def collect_snapshot() -> dict:
    # Apply the WindowsUpdate lock-down policy on every check-in (idempotent).
    # If a user disables it via gpedit, the next check-in re-applies it.
    update_policy = _windows_enforce_managed_update_policy()
    patches = patches_available()
    scan = _patch_scan_metadata()
    scan["pending_count"] = len(patches)
    scan["fully_updated"] = (scan["scan_success"] and scan["pending_count"] == 0)
    return {
        "snapshot_at": _now_iso(),
        "os": os_info(),
        "cpu": cpu_info(),
        "memory": memory_info(),
        "disks": disks_info(),
        "network": network_info(),
        "bios": bios_info(),
        "system": system_info(),
        "logged_in_user": logged_in_user(),
        "software": installed_software(),
        "patches": patches,
        "patch_scan": scan,
        "update_policy": update_policy,
    }


# ---------------------------------------------------------- Phase 6 — auto-install

def _install_apt(pkg: str) -> dict:
    """apt-get install -y <pkg>. Returns attempt dict ready to POST."""
    started = _now_iso()
    proc = subprocess.run(
        ["apt-get", "-q", "-y", "-o", "Dpkg::Options::=--force-confold",
         "-o", "Dpkg::Options::=--force-confdef", "install", pkg],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LANG": "C", "LC_ALL": "C"},
    )
    finished = _now_iso()
    needs_reboot = Path("/var/run/reboot-required").exists()
    return {
        "package_name": pkg,
        "started_at": started,
        "finished_at": finished,
        "exit_code": proc.returncode,
        "success": proc.returncode == 0,
        "needs_reboot": needs_reboot,
        "stdout": (proc.stdout or "")[-7000:],
        "stderr": (proc.stderr or "")[-1000:],
        "method": "apt",
    }


def _install_windows_update(kb_or_name: str) -> dict:
    """PSWindowsUpdate path. Endpoint must have the module installed."""
    started = _now_iso()
    if kb_or_name.upper().startswith("KB"):
        ps_arg = f"-KBArticleID '{kb_or_name}'"
    else:
        ps_arg = f"-Title '{kb_or_name}'"
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "if (-not (Get-Module -ListAvailable PSWindowsUpdate)) { "
         "  Write-Error 'PSWindowsUpdate module not installed'; exit 2 }; "
         "Import-Module PSWindowsUpdate; "
         f"Install-WindowsUpdate {ps_arg} -AcceptAll -IgnoreReboot -Confirm:$false"],
        capture_output=True, text=True, timeout=1200,
    )
    finished = _now_iso()
    return {
        "package_name": kb_or_name,
        "started_at": started,
        "finished_at": finished,
        "exit_code": proc.returncode,
        "success": proc.returncode == 0,
        "needs_reboot": "reboot" in (proc.stdout + proc.stderr).lower(),
        "stdout": (proc.stdout or "")[-7000:],
        "stderr": (proc.stderr or "")[-1000:],
        "method": "windows-update",
    }


def _install_winget(package_id: str) -> dict:
    """winget upgrade --silent path. Generic third-party software (Chrome / Adobe / Zoom etc.)."""
    started = _now_iso()
    try:
        proc = subprocess.run(
            ["winget", "upgrade",
             "--id", package_id,
             "--silent",
             "--accept-source-agreements",
             "--accept-package-agreements",
             "--disable-interactivity"],
            capture_output=True, text=True, timeout=1800,
        )
    except FileNotFoundError:
        return {
            "package_name": package_id,
            "started_at": started, "finished_at": _now_iso(),
            "exit_code": 127, "success": False, "needs_reboot": False,
            "stdout": None, "stderr": "winget not installed on this host",
            "method": "winget",
        }
    finished = _now_iso()
    return {
        "package_name": package_id,
        "started_at": started,
        "finished_at": finished,
        "exit_code": proc.returncode,
        "success": proc.returncode == 0,
        "needs_reboot": False,  # winget doesn't surface this clearly
        "stdout": (proc.stdout or "")[-7000:],
        "stderr": (proc.stderr or "")[-1000:],
        "method": "winget",
    }


def _install_dispatch(package_name: str) -> dict:
    """Pick install method based on package name + OS heuristics.

    Linux           → apt-get
    Windows + KB    → PSWindowsUpdate
    Windows + other → winget (Vendor.Product Id like Google.Chrome)
    """
    if platform.system() == "Linux":
        return _install_apt(package_name)
    # Windows
    if package_name.upper().startswith("KB"):
        return _install_windows_update(package_name)
    return _install_winget(package_name)


def execute_pending_deployments(cfg: dict) -> int:
    """Pull pending deployments from the server, execute, post results.

    Returns the number of targets fully processed (succeeded or failed).
    """
    base = cfg["server_url"].rstrip("/")
    token = cfg["agent_token"]
    code, body = _http(base + "/api/v1/agent/deployments", method="GET", token=token)
    if code != 200:
        log.warning("deployments: pickup failed %s %s", code, body[:200])
        return 0
    try:
        deployments = json.loads(body)
    except Exception:
        log.warning("deployments: bad JSON in response")
        return 0
    if not deployments:
        return 0

    is_linux = platform.system() == "Linux"
    is_windows = platform.system() == "Windows"
    if not (is_linux or is_windows):
        log.warning("deployments: unsupported OS %s — skipping all", platform.system())
        return 0

    # Linux: must be root for apt-get install. EUID=0 check — if not, log + skip.
    if is_linux and os.geteuid() != 0:
        log.warning("deployments: not running as root, cannot apt-get install")
        return 0

    processed = 0
    for d in deployments:
        target_id = d["target_id"]
        win_name = d.get("window_name", "")
        packages = d.get("selected_packages") or []
        log.info("Executing deployment target=%s window=%r packages=%d",
                 target_id, win_name, len(packages))

        # Mark target as in_progress server-side
        _http(f"{base}/api/v1/agent/deployments/{target_id}/start", method="POST", token=token)

        any_failed = False
        for pkg in packages:
            try:
                attempt = _install_dispatch(pkg)
            except subprocess.TimeoutExpired:
                attempt = {"package_name": pkg, "started_at": _now_iso(), "finished_at": _now_iso(),
                           "exit_code": 124, "success": False, "needs_reboot": False,
                           "stdout": None, "stderr": "timeout",
                           "method": "apt" if is_linux else ("windows-update" if pkg.upper().startswith("KB") else "winget")}
            except Exception as e:  # noqa: BLE001
                attempt = {"package_name": pkg, "started_at": _now_iso(), "finished_at": _now_iso(),
                           "exit_code": 1, "success": False, "needs_reboot": False,
                           "stdout": None, "stderr": f"{type(e).__name__}: {e}",
                           "method": "apt" if is_linux else ("windows-update" if pkg.upper().startswith("KB") else "winget")}

            log.info("  %s → exit=%s success=%s",
                     pkg, attempt["exit_code"], attempt["success"])
            if not attempt["success"]:
                any_failed = True
            _http(f"{base}/api/v1/agent/deployments/{target_id}/attempt",
                  method="POST", body=attempt, token=token)

        # Finish marker — server reads attempts table to compute final status
        _http(f"{base}/api/v1/agent/deployments/{target_id}/finish",
              method="POST",
              body={"note": ("Linux apt-get" if is_linux else "Windows Update via PSWindowsUpdate"),
                    "needs_reboot": Path("/var/run/reboot-required").exists() if is_linux else False},
              token=token)
        processed += 1

    return processed


# ---------------------------------------------------------- registration

def register_if_needed(cfg: dict) -> dict:
    if cfg.get("agent_token") and cfg.get("agent_id"):
        return cfg
    if not cfg.get("server_url") or not cfg.get("enrolment_key"):
        raise SystemExit("Config missing server_url and enrolment_key. Re-run --bootstrap.")

    machine_id = cfg.get("machine_id") or get_machine_id()
    cfg["machine_id"] = machine_id
    hostname = socket.gethostname()
    log.info("Registering with %s as %s (machine_id=%s)", cfg["server_url"], hostname, machine_id)
    code, body = _http(
        cfg["server_url"].rstrip("/") + "/api/v1/agent/register",
        method="POST",
        body={"enrolment_key": cfg["enrolment_key"], "machine_id": machine_id, "hostname": hostname},
    )
    if code != 200:
        raise SystemExit(f"Registration failed: {code} {body}")
    data = json.loads(body)
    cfg["agent_id"] = data["agent_id"]
    cfg["agent_token"] = data["agent_token"]
    save_config(cfg)
    log.info("Registered as agent_id=%s", cfg["agent_id"])
    return cfg


def _self_update_if_newer(cfg: dict) -> bool:
    """Compare the on-disk agent script against /agent/files/octoassist_agent.py.
    If the hashes differ, atomically replace the local script. The currently-
    running process keeps using the OLD code (Python's already imported it),
    but the scheduled task's NEXT invocation in 6 hours picks up the new one.

    Returns True if a replacement was written.

    Safe by design:
      - Never replaces if the download is shorter than 5 KB (clearly broken)
      - Verifies the downloaded blob parses as Python before replacing
      - Atomic os.replace so a power-fail can't leave a half-written file
    """
    if platform.system() != "Windows":
        return False  # only auto-update on Windows endpoints; Linux uses dpkg
    try:
        import hashlib, ast
        own = Path(__file__).resolve()
        if not own.exists():
            return False
        own_hash = hashlib.sha256(own.read_bytes()).hexdigest()
        base = cfg["server_url"].rstrip("/")
        code, body = _http(base + "/agent/files/octoassist_agent.py",
                           method="GET", token=cfg.get("agent_token"))
        if code != 200 or not body or len(body) < 5000:
            return False
        new_bytes = body.encode("utf-8") if isinstance(body, str) else body
        new_hash = hashlib.sha256(new_bytes).hexdigest()
        if new_hash == own_hash:
            return False
        # Make sure the new script is syntactically valid Python before swapping
        try:
            ast.parse(new_bytes.decode("utf-8", errors="strict"))
        except (SyntaxError, UnicodeDecodeError) as e:
            log.warning("self-update: downloaded script failed syntax check: %s", e)
            return False
        tmp = own.with_suffix(".py.new")
        tmp.write_bytes(new_bytes)
        os.replace(tmp, own)
        log.info("self-update: replaced agent script (sha256 %s -> %s). "
                 "Scheduled task will use new code on next invocation.",
                 own_hash[:12], new_hash[:12])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("self-update check failed (continuing with current code): %s", e)
        return False


def checkin_once(cfg: dict) -> bool:
    # Auto-update first — picks up any server-side agent fixes without admin
    # action. The new code lands but the running process keeps the old import;
    # next scheduled run picks it up.
    _self_update_if_newer(cfg)
    snap = collect_snapshot()
    log.info("Collected snapshot: OS=%s, CPU=%s, software=%d, patches=%d",
             snap["os"].get("caption"), snap["cpu"].get("name"),
             len(snap["software"]), len(snap.get("patches") or []))
    code, body = _http(
        cfg["server_url"].rstrip("/") + "/api/v1/agent/checkin",
        method="POST",
        body=snap,
        token=cfg["agent_token"],
    )
    if code != 200:
        log.error("Check-in failed: %s %s", code, body[:300])
        return False
    log.info("Check-in OK")

    # Phase 6: pull and execute any pending deployments. Best-effort —
    # never let a deployment failure block the next check-in cycle.
    try:
        n = execute_pending_deployments(cfg)
        if n:
            log.info("Processed %d deployment target(s)", n)
            # Re-collect so the server's patch state reflects what we just installed
            snap2 = collect_snapshot()
            _http(cfg["server_url"].rstrip("/") + "/api/v1/agent/checkin",
                  method="POST", body=snap2, token=cfg["agent_token"])
    except Exception as e:  # noqa: BLE001
        log.exception("deployments pickup loop failed: %s", e)

    return True


# ---------------------------------------------------------- CLI

def cmd_bootstrap(args: argparse.Namespace) -> int:
    cfg = {
        "server_url": args.server_url.rstrip("/"),
        "enrolment_key": args.enrolment_key,
        "agent_id": None,
        "agent_token": None,
        "machine_id": None,
        "checkin_interval_hours": args.interval_hours or 6,
    }
    save_config(cfg)
    print(f"Bootstrapped config at {CONFIG_PATH}")
    print("Run `octoassist-agent --once` to verify, or start the systemd / scheduled-task service.")
    return 0


def cmd_once(_args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg = register_if_needed(cfg)
    return 0 if checkin_once(cfg) else 1


def cmd_daemon(_args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg = register_if_needed(cfg)
    interval = max(1, int(cfg.get("checkin_interval_hours", 6)))
    log.info("Daemon mode: checking in every %d h", interval)
    # Initial check-in immediately
    checkin_once(cfg)
    while True:
        try:
            time.sleep(interval * 3600)
            checkin_once(cfg)
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            log.exception("Loop error: %s", e)
            time.sleep(60)


def main() -> int:
    setup_logging()
    p = argparse.ArgumentParser(prog="octoassist-agent",
                                description="OctoAssist cross-platform asset agent.")
    sub = p.add_subparsers(dest="cmd")

    bp = sub.add_parser("--bootstrap", aliases=["bootstrap"])
    # also accept top-level flags for the bootstrap subcommand
    p.add_argument("--bootstrap", action="store_true", help="Write the agent config and exit")
    p.add_argument("--once", action="store_true", help="Single check-in then exit")
    p.add_argument("--server-url", default=None)
    p.add_argument("--enrolment-key", default=None)
    p.add_argument("--interval-hours", type=int, default=None)
    p.add_argument("--version", action="store_true")

    args = p.parse_args()

    if args.version:
        print(f"octoassist-agent {VERSION}")
        return 0
    if args.bootstrap:
        if not args.server_url or not args.enrolment_key:
            print("--bootstrap requires --server-url and --enrolment-key", file=sys.stderr)
            return 2
        return cmd_bootstrap(args)
    if args.once:
        return cmd_once(args)
    return cmd_daemon(args)


if __name__ == "__main__":
    sys.exit(main())
