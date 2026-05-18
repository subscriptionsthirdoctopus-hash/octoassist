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

# Windows: hide every subprocess console window. SYSTEM-launched processes
# (which is how the daemon runs in production) won't show desktop windows
# to the interactive user anyway, but CREATE_NO_WINDOW is a belt-and-braces
# guarantee for both SYSTEM and the manual `--once` mode that admins run
# during refresh, where a brief PowerShell flash COULD otherwise appear.
_CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0


def _run(cmd, **kwargs):
    """Wrapper around subprocess.run that hides any console window on Windows.

    Identical contract to subprocess.run; just sets creationflags. Use this
    everywhere instead of subprocess.run when invoking PowerShell / cmd /
    msg / shutdown etc.
    """
    if _CREATE_NO_WINDOW:
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def _user_visible_reboot_prompt() -> None:
    """Show the end user a 'restart your computer' notification and schedule
    an automatic restart 2 hours from now (cancelable with `shutdown /a`).

    Runs in two layers:
      1. msg.exe sends a dialog to every active session — appears as a
         standard Windows alert with an OK button. Times out after 10 min.
      2. shutdown.exe /r /t 7200 schedules an OS-level restart with the
         standard Windows balloon countdown ('Windows will restart in 2
         hours to install updates'). User sees the system tray notification
         and can save work; an admin can abort with `shutdown /a` if needed.

    Both calls fire as SYSTEM (the daemon's context) but msg.exe + shutdown
    are designed to surface in interactive user sessions.
    """
    if platform.system() != "Windows":
        return
    try:
        _run(["msg.exe", "*", "/TIME:600", "/W",
              "OctoAssist: Security updates have been installed. "
              "Please save your work and restart your computer to "
              "complete installation. An automatic restart is scheduled "
              "in 2 hours."],
             timeout=20)
    except Exception as e:  # noqa: BLE001
        log.warning("msg.exe popup failed: %s", e)
    try:
        # /f forces close of apps (with grace period); /t 7200 = 2 hours.
        # Use /a to abort. /c is the user-visible reason text.
        _run(["shutdown.exe", "/r", "/t", "7200", "/f",
              "/c", "OctoAssist: Security updates installed. "
                    "Restart in 2 hours. Save your work."],
             timeout=15)
        log.info("Scheduled automatic restart in 2 hours (shutdown /r /t 7200)")
    except Exception as e:  # noqa: BLE001
        log.warning("shutdown.exe schedule failed: %s", e)

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
            r = _run(
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
            r = _run(
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
            r = _run(
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
        v = _run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=5).stdout.strip()
        b = _run(["sw_vers", "-buildVersion"], capture_output=True, text=True, timeout=5).stdout.strip()
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
        r = _run(
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
            name = _run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                   capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = _run(
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
            r = _run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return {"total_gb": round(int(r.stdout.strip()) / 1024 / 1024 / 1024, 2)}
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            r = _run(
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
            r = _run(["df", "-Pk"], capture_output=True, text=True, timeout=10)
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
            r = _run(
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
            r = _run(["ip", "-json", "addr"], capture_output=True, text=True, timeout=10)
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
            r = _run(["ifconfig"], capture_output=True, text=True, timeout=10)
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
            r = _run(
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
            r = _run(
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
            r = _run(
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
            r = _run(["who"], capture_output=True, text=True, timeout=5)
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
            r = _run(
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
            r = _run(
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
            r = _run(["rpm", "-qa", "--queryformat",
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
            r = _run(["pkgutil", "--pkgs"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    out.append({"name": line, "version": "", "publisher": "", "install_date": None})
        except Exception:
            pass
        return out
    if platform.system() == "Windows":
        try:
            r = _run(
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
            r = _run(
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
                r = _run(
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
        # Windows patches come from THREE sources:
        #   1. Windows Update + Microsoft Update catalog (drivers, .NET, OEM
        #      hardware, AI components) via PSWindowsUpdate -MicrosoftUpdate
        #   2. Direct COM Microsoft.Update.Searcher (fallback / completeness)
        #   3. winget upgrade — third-party software (Chrome, Adobe, Zoom, …)
        #
        # The default `Get-WindowsUpdate` (no -MicrosoftUpdate) ONLY scans the
        # Windows Update service and misses drivers, .NET Framework updates,
        # ASUS/Dell/HP hardware updates, Phi Silica AI components, etc. —
        # exactly the items end-users see in Settings → Windows Update.
        ps_win_update = r"""
$ErrorActionPreference = 'SilentlyContinue'
if (-not (Get-Module -ListAvailable PSWindowsUpdate)) { Write-Output '[]'; exit 0 }
Import-Module PSWindowsUpdate

# Make sure the Microsoft Update service (broader catalog) is registered.
try {
    $sm = New-Object -ComObject Microsoft.Update.ServiceManager
    if (-not ($sm.Services | Where-Object { $_.ServiceID -eq '7971f918-a847-4430-9279-4a52d1efe18d' })) {
        # 7 = AllowOnlineRegistration + RegisterServiceWithAU + AddServiceFlag
        $sm.AddService2('7971f918-a847-4430-9279-4a52d1efe18d', 7, '') | Out-Null
    }
} catch {}

# Scan both Windows Update + Microsoft Update.
$rows = Get-WindowsUpdate -MicrosoftUpdate -ErrorAction SilentlyContinue
if (-not $rows) { Write-Output '[]'; exit 0 }
if ($rows -isnot [System.Array]) { $rows = @($rows) }
$rows | ForEach-Object {
    $cat = ''
    try { $cat = ($_.Categories | ForEach-Object { $_.Name }) -join ',' } catch {}
    [pscustomobject]@{
        KB           = ($_.KB -as [string]) -replace '^KB',''
        Title        = $_.Title
        MsrcSeverity = "$($_.MsrcSeverity)"
        Categories   = $cat
        SizeMB       = if ($_.Size) { [math]::Round($_.Size/1MB, 1) } else { $null }
    }
} | ConvertTo-Json -Compress -Depth 3
"""
        try:
            r = _run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_win_update],
                capture_output=True, text=True, timeout=180,
            )
            data = json.loads(r.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            for u in data:
                kb = (u.get("KB") or "").strip()
                title = u.get("Title") or ""
                cats = (u.get("Categories") or "").lower()
                sev = (u.get("MsrcSeverity") or "").strip().lower()
                # Derive severity if MsrcSeverity is blank — drivers, .NET
                # Framework, Phi Silica etc. typically come without severity.
                if sev not in ("critical", "important", "moderate", "low"):
                    if "security" in cats:    sev = "important"
                    elif "critical" in cats:  sev = "critical"
                    elif "driver" in cats:    sev = "moderate"
                    else:                     sev = "moderate"
                name = (f"KB{kb}" if kb else title[:80]) or "update"
                # Tag the source so the dashboard can distinguish driver /
                # Windows / .NET / OEM updates.
                if "driver" in cats:
                    source = "windows-update:driver"
                elif ".net" in title.lower() or ".net" in cats:
                    source = "windows-update:dotnet"
                elif kb:
                    source = "windows-update:kb"
                else:
                    source = "windows-update:other"
                out.append({
                    "name":              name,
                    "current_version":   None,
                    "available_version": None,
                    "severity":          sev,
                    "source":            source,
                    "title":             title,
                })
        except Exception as e:  # noqa: BLE001
            log.warning("Windows Update scan failed: %s", e)

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
        r = _run(
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
Set-ItemProperty -Path $base -Name 'SetDisableUXWUAccess'                        -Type DWord -Value 1
# Block ALL user access to the Windows Update control panel / settings entry.
Set-ItemProperty -Path $base -Name 'DisableWindowsUpdateAccess'                  -Type DWord -Value 1
# Don't connect to the Windows Update internet locations — even if user gets to
# the UI somehow, "Check for updates" can't reach Microsoft.
Set-ItemProperty -Path $base -Name 'DoNotConnectToWindowsUpdateInternetLocations' -Type DWord -Value 1
# Hide the "Get the latest updates as soon as they're available" toggle.
Set-ItemProperty -Path $base -Name 'IsContinuousInnovationOptedIn'               -Type DWord -Value 0
# Defer feature + quality updates so end users can't fast-ring themselves.
Set-ItemProperty -Path $base -Name 'DeferFeatureUpdates'                         -Type DWord -Value 1
Set-ItemProperty -Path $base -Name 'DeferQualityUpdates'                         -Type DWord -Value 1
# Block automatic install — agent drives the schedule via PSWindowsUpdate.
# AUOptions=1 = "Never check for updates (not recommended)" — the most
# restrictive setting; the OS WU service goes idle.
Set-ItemProperty -Path $au   -Name 'AUOptions'                                   -Type DWord -Value 1
Set-ItemProperty -Path $au   -Name 'NoAutoUpdate'                                -Type DWord -Value 1
# Don't auto-restart while a user is signed in (the agent picks reboot timing).
Set-ItemProperty -Path $au   -Name 'NoAutoRebootWithLoggedOnUsers'               -Type DWord -Value 1
'OK'
"""
    try:
        r = _run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        ok = "OK" in (r.stdout or "")
        return {"applied": ok, "reason": (r.stderr or "")[:200] if not ok else ""}
    except Exception as e:  # noqa: BLE001
        return {"applied": False, "reason": str(e)[:200]}


def _ensure_pswindowsupdate(timeout: int = 300) -> bool:
    """Install PSWindowsUpdate + its NuGet prerequisite if missing.

    Called at the top of every check-in / patch scan so endpoints
    self-heal when the original install.ps1 didn't manage to lay it
    down (transient PSGallery outage, NuGet not yet bootstrapped, AV
    interference, etc.). Returns True if the module is available
    afterwards.

    Idempotent — quick no-op when the module is already present.
    """
    if platform.system() != "Windows":
        return False
    try:
        r = _run(["powershell", "-NoProfile", "-Command",
                  "if (Get-Module -ListAvailable PSWindowsUpdate) { 'yes' } else { 'no' }"],
                 capture_output=True, text=True, timeout=20)
        if "yes" in (r.stdout or ""):
            return True
    except Exception:
        pass

    log.info("PSWindowsUpdate not installed; auto-installing...")
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
if (-not (Get-PackageProvider NuGet -ErrorAction SilentlyContinue)) {
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope AllUsers | Out-Null
}
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue
if (-not (Get-Module -ListAvailable PSWindowsUpdate)) {
    Install-Module PSWindowsUpdate -Scope AllUsers -Force -AllowClobber -ErrorAction Stop
}
if (Get-Module -ListAvailable PSWindowsUpdate) { 'INSTALLED' } else { 'FAILED' }
"""
    try:
        r = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                 capture_output=True, text=True, timeout=timeout)
        if "INSTALLED" in (r.stdout or ""):
            log.info("PSWindowsUpdate auto-installed successfully")
            return True
        log.warning("PSWindowsUpdate auto-install attempted but module still missing. "
                    "stderr tail: %s", ((r.stderr or "") + (r.stdout or ""))[-400:])
    except Exception as e:  # noqa: BLE001
        log.warning("PSWindowsUpdate auto-install failed: %s", e)
    return False


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
        r = _run(
            ["powershell", "-NoProfile", "-Command",
             "if (Get-Module -ListAvailable PSWindowsUpdate) { 'yes' } else { 'no' }"],
            capture_output=True, text=True, timeout=15,
        )
        psw = "yes" in (r.stdout or "")
    except Exception:
        pass
    try:
        r = _run(["winget", "--version"], capture_output=True, text=True, timeout=8)
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
    # Self-heal: install PSWindowsUpdate if missing. Endpoints that ended up
    # with the module missing (transient PSGallery issue at install time,
    # NuGet bootstrap failure, AV interference) get fixed automatically on
    # the next check-in. No admin action required.
    _ensure_pswindowsupdate()
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
    proc = _run(
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
    """PSWindowsUpdate path. Endpoint must have the module installed.

    Two install modes:
      - KB-based (e.g. 'KB5087051')      → -KBArticleID 'KB5087051'
      - Title-based (e.g. 'ASUSTeK...')  → wildcard -Title '*ASUSTeK*' so a
        minor version mismatch (200.0.4.0 in DB vs 200.0.4.1 in catalog
        when MS re-released) still matches.

    Timeout is 2 hours: cumulative security updates + .NET Framework can
    each take 30-60 min; the previous 30-min cap killed them prematurely.
    """
    started = _now_iso()
    p = kb_or_name.strip()
    if p.upper().startswith("KB") and p[2:].split(maxsplit=1)[0].isdigit():
        ps_arg = f"-KBArticleID '{p}'"
    else:
        # PSWindowsUpdate's -Title parameter does a REGEX match against each
        # update's Title property (NOT a glob). So we have to:
        #   (1) Strip the trailing "(version)" off the recorded title — minor
        #       version drift between scan + install would otherwise miss.
        #   (2) Regex-escape every character so the title matches literally.
        #   (3) PowerShell-escape single quotes for the surrounding ' '.
        # The escaped string then acts as a substring regex (PSWindowsUpdate
        # uses [regex]::IsMatch which returns true if the pattern is found
        # anywhere in Title) — exactly the behaviour we want.
        import re as _re
        title_core = _re.sub(r"\s*\([^)]+\)\s*$", "", p).strip()
        regex_safe = _re.escape(title_core)            # escape regex specials
        safe       = regex_safe.replace("'", "''")     # ps-escape single quotes
        ps_arg = f"-Title '{safe}'"
    # Install via PSWindowsUpdate. -MicrosoftUpdate matches the scanner so
    # we can deploy drivers, .NET, OEM hardware, AI components — same set
    # the end-user would see in Settings → Windows Update.
    proc = _run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         "if (-not (Get-Module -ListAvailable PSWindowsUpdate)) { "
         "  Write-Error 'PSWindowsUpdate module not installed'; exit 2 }; "
         "Import-Module PSWindowsUpdate; "
         "try { $sm = New-Object -ComObject Microsoft.Update.ServiceManager; "
         "  if (-not ($sm.Services | ? { $_.ServiceID -eq '7971f918-a847-4430-9279-4a52d1efe18d' })) { "
         "    $sm.AddService2('7971f918-a847-4430-9279-4a52d1efe18d', 7, '') | Out-Null } } catch {}; "
         f"Install-WindowsUpdate -MicrosoftUpdate {ps_arg} -AcceptAll -IgnoreReboot -Confirm:$false"],
        capture_output=True, text=True, timeout=7200,
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
        proc = _run(
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

    Linux                         → apt-get
    Windows + 'KB12345'           → Install-WindowsUpdate -KBArticleID
    Windows + 'Vendor.Product'    → winget (one dot, no spaces, no KB)
    Windows + everything else     → Install-WindowsUpdate -Title (Microsoft
                                    Update catalog items like driver / ASUS /
                                    .NET / Phi Silica that come in by Title)
    """
    if platform.system() == "Linux":
        return _install_apt(package_name)
    # Windows
    p = package_name.strip()
    if p.upper().startswith("KB") and p[2:].split(maxsplit=1)[0].isdigit():
        return _install_windows_update(p)
    # winget Vendor.Product Id heuristic: at least one dot, no spaces, alnum/-
    if "." in p and " " not in p and "/" not in p and "(" not in p:
        return _install_winget(p)
    # Microsoft Update catalog item identified by Title (no KB, has spaces)
    return _install_windows_update(p)


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
        any_reboot_needed = False
        any_success = False
        for pkg in packages:
            # Safety: skip packages that clearly belong to a different OS.
            # If the server-side filter ever regresses, the agent refuses
            # rather than burning 10s per package on doomed installs.
            if is_windows:
                p_low = pkg.lower()
                if any(p_low.startswith(prefix) for prefix in (
                        "systemd", "libsystemd", "snapd", "sosreport",
                        "software-properties-common", "ubuntu-", "linux-",
                        "lib", "python3-", "perl-", "ruby-", "golang-",
                        "grub-", "initramfs-", "openssh-", "ca-certificates",
                        "apt-", "dpkg")):
                    log.warning("Skipping non-Windows package on Windows host: %s", pkg)
                    _http(f"{base}/api/v1/agent/deployments/{target_id}/attempt",
                          method="POST",
                          body={"package_name": pkg, "started_at": _now_iso(),
                                "finished_at": _now_iso(), "exit_code": 0,
                                "success": False, "needs_reboot": False,
                                "stdout": None,
                                "stderr": "skipped: non-Windows package name",
                                "method": "skipped"},
                          token=token)
                    continue
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
            else:
                any_success = True
            if attempt.get("needs_reboot"):
                any_reboot_needed = True
            _http(f"{base}/api/v1/agent/deployments/{target_id}/attempt",
                  method="POST", body=attempt, token=token)

        # Finish marker — server reads attempts table to compute final status
        linux_reboot = Path("/var/run/reboot-required").exists() if is_linux else False
        _http(f"{base}/api/v1/agent/deployments/{target_id}/finish",
              method="POST",
              body={"note": ("Linux apt-get" if is_linux else "Windows Update via PSWindowsUpdate"),
                    "needs_reboot": any_reboot_needed or linux_reboot},
              token=token)

        # Phase 9: end-user notification + scheduled restart. Fires only if
        # something installed successfully AND a reboot is genuinely needed.
        # Skipped on Linux (the reboot prompt is Windows-specific).
        if any_success and any_reboot_needed and is_windows:
            try:
                _user_visible_reboot_prompt()
            except Exception as e:  # noqa: BLE001
                log.warning("reboot prompt failed: %s", e)

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
        body={"enrolment_key": cfg["enrolment_key"],
              "machine_id": machine_id, "hostname": hostname,
              "os_family": platform.system()},  # "Windows" / "Linux" / "Darwin"
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
    If the hashes differ, atomically replace the local script AND re-execute
    this Python process so the new code takes effect immediately.

    Without the re-exec, the on-disk file gets updated but the in-memory
    Python interpreter keeps running the old imports until the daemon is
    killed and restarted — meaning admin still has to do something on the
    endpoint to pick up agent fixes. With execv() the daemon swaps itself
    out: same Python process replaces its memory image with the new script,
    keeps the same PID + Scheduled Task lineage, and immediately runs the
    new code on the very next loop iteration. Truly hands-off.

    Safe by design:
      - Never replaces if the download is shorter than 5 KB (clearly broken)
      - Verifies the downloaded blob parses as Python before replacing
      - Atomic os.replace so a power-fail can't leave a half-written file
      - execv only happens AFTER os.replace has succeeded
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
                 "Re-executing in-place to load new code.",
                 own_hash[:12], new_hash[:12])
        # Hand control to the new code. os.execv replaces the current
        # process image — same PID, no restart of the Scheduled Task,
        # no service interruption visible to anyone. Keeps original argv.
        try:
            os.execv(sys.executable, [sys.executable, str(own)] + sys.argv[1:])
        except Exception as e:  # noqa: BLE001
            log.warning("self-update: execv failed (will pick up new code on next reboot): %s", e)
            return True
        # Should never reach here — execv replaces the process.
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


# ---------------------------------------------------------- remote actions

def execute_pending_actions(cfg: dict) -> int:
    """Poll /api/v1/agent/actions, dispatch each to a handler, post results.

    Generic command channel — every "do X on the endpoint" feature in OctoAssist
    flows through here. The dashboard creates a RemoteAction row; the agent
    sees it on next 30-sec poll; the right handler runs; result is posted back.

    Adding a new action kind = new handler function + dict entry below.
    Returns count of actions processed (succeeded or failed).
    """
    if platform.system() != "Windows":
        return 0  # remote-action surface is Windows-only by design

    base = cfg["server_url"].rstrip("/")
    token = cfg["agent_token"]
    try:
        code, body = _http(base + "/api/v1/agent/actions", method="GET", token=token)
    except Exception as e:  # noqa: BLE001
        log.warning("actions: pickup failed: %s", e)
        return 0
    if code != 200:
        return 0
    try:
        actions = json.loads(body)
    except Exception:
        return 0
    if not actions:
        return 0

    processed = 0
    for a in actions:
        action_id = a["id"]
        kind      = a.get("kind", "")
        params    = a.get("params") or {}
        log.info("action %s: %s — running", action_id, kind)

        # Mark in_progress server-side
        try:
            _http(f"{base}/api/v1/agent/actions/{action_id}/start",
                  method="POST", token=token)
        except Exception:
            pass

        # Dispatch — each handler returns a dict with success/exit_code/stdout/stderr/result
        handler = _ACTION_HANDLERS.get(kind)
        if handler is None:
            result = {"success": False, "exit_code": -1,
                      "stderr": f"unknown action kind: {kind}", "stdout": None,
                      "result": None}
        else:
            try:
                result = handler(params, cfg)
            except subprocess.TimeoutExpired:
                result = {"success": False, "exit_code": 124,
                          "stderr": "timeout", "stdout": None, "result": None}
            except Exception as e:  # noqa: BLE001
                result = {"success": False, "exit_code": 1,
                          "stderr": f"{type(e).__name__}: {e}",
                          "stdout": None, "result": None}

        # Truncate output to keep payloads small
        if isinstance(result.get("stdout"), str):
            result["stdout"] = result["stdout"][-15000:]
        if isinstance(result.get("stderr"), str):
            result["stderr"] = result["stderr"][-4000:]

        try:
            _http(f"{base}/api/v1/agent/actions/{action_id}/result",
                  method="POST", body=result, token=token)
        except Exception as e:  # noqa: BLE001
            log.warning("action %s: result POST failed: %s", action_id, e)
        log.info("action %s: done success=%s exit=%s",
                 action_id, result.get("success"), result.get("exit_code"))
        processed += 1

    return processed


# -------------------- action handlers --------------------

def _action_run_executable(params: dict, cfg: dict) -> dict:
    """Download a URL (.exe / .msi / .msu), run it with the given args, capture output."""
    url = params.get("url")
    args = params.get("args", "")
    if not url:
        return {"success": False, "exit_code": -1, "stderr": "missing 'url' param",
                "stdout": None, "result": None}

    # Pick a filename from the URL or default to .exe
    import urllib.parse, hashlib
    fname = (urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
             or f"octo_install_{hashlib.sha256(url.encode()).hexdigest()[:10]}.exe")
    if not fname.lower().endswith((".exe", ".msi", ".msu", ".bat", ".cmd")):
        fname += ".exe"
    target = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / fname

    log.info("run_executable: downloading %s -> %s", url, target)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OctoAssist-Agent/1"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(target, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "exit_code": -1,
                "stderr": f"download failed: {e}", "stdout": None, "result": None}

    size = target.stat().st_size if target.exists() else 0
    log.info("run_executable: downloaded %d bytes; running with args=%r", size, args)

    is_msi = target.suffix.lower() == ".msi"
    if is_msi:
        # msiexec must run msi files
        cmd = ["msiexec", "/i", str(target)] + (args.split() if args else ["/quiet", "/norestart"])
    else:
        # Treat as a generic installer / exe
        cmd = [str(target)] + (args.split() if args else [])

    proc = _run(cmd, capture_output=True, text=True, timeout=3600)

    # Best-effort cleanup of the downloaded file
    try:
        target.unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "result": {"downloaded_bytes": size, "filename": fname, "args": args},
    }


def _action_reboot(params: dict, cfg: dict) -> dict:
    delay = int(params.get("delay_seconds", 60))
    reason = (params.get("reason") or "OctoAssist-initiated reboot")[:127]
    proc = _run(["shutdown.exe", "/r", "/t", str(delay), "/f", "/c", reason],
                capture_output=True, text=True, timeout=15)
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": {"delay_seconds": delay, "reason": reason},
    }


def _action_lock_workstation(_params: dict, _cfg: dict) -> dict:
    proc = _run(["rundll32.exe", "user32.dll,LockWorkStation"],
                capture_output=True, text=True, timeout=10)
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": None,
    }


def _action_send_toast(params: dict, cfg: dict) -> dict:
    """Show a Windows alert message in every active session via msg.exe."""
    title = (params.get("title") or "OctoAssist")[:80]
    body  = (params.get("body")  or "")[:500]
    text  = (f"{title}\n\n{body}" if body else title)
    proc = _run(["msg.exe", "*", "/TIME:600", text],
                capture_output=True, text=True, timeout=20)
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": None,
    }


def _action_list_processes(_params: dict, _cfg: dict) -> dict:
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Process | Where-Object { $_.MainWindowTitle -ne '' -or $_.WorkingSet -gt 50MB } |
  Sort-Object -Property WorkingSet -Descending |
  Select-Object -First 60 -Property Name, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet/1MB,1)}}, CPU, Path |
  ConvertTo-Json -Compress
"""
    proc = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = []
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": None, "stderr": proc.stderr or "",
        "result": {"processes": data},
    }


def _action_kill_process(params: dict, cfg: dict) -> dict:
    name = params.get("name")
    pid  = params.get("pid")
    if not name and not pid:
        return {"success": False, "exit_code": -1,
                "stderr": "missing name or pid", "stdout": None, "result": None}
    if pid:
        ps = f"Stop-Process -Id {int(pid)} -Force -ErrorAction Stop"
    else:
        # Strip .exe; Stop-Process -Name doesn't want the extension
        safe = str(name).strip().rstrip(".exeEXE")
        ps = f"Stop-Process -Name '{safe}' -Force -ErrorAction Stop"
    proc = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True, text=True, timeout=20)
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": {"target": name or f"pid={pid}"},
    }


def _action_set_wallpaper(params: dict, cfg: dict) -> dict:
    """Download an image URL, save to %ProgramData%\\OctoAssist\\wallpaper.jpg,
    set it as the per-user wallpaper for every logged-in user via registry +
    SystemParametersInfo. Safe to re-run.
    """
    url = params.get("url")
    if not url:
        return {"success": False, "exit_code": -1,
                "stderr": "missing 'url' param", "stdout": None, "result": None}

    wp_dir = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "OctoAssist"
    wp_dir.mkdir(parents=True, exist_ok=True)
    target = wp_dir / "wallpaper.jpg"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OctoAssist-Agent/1"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(target, "wb") as f:
            f.write(resp.read())
    except Exception as e:  # noqa: BLE001
        return {"success": False, "exit_code": -1,
                "stderr": f"download failed: {e}", "stdout": None, "result": None}

    ps = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$img = '{target}'
# Loop every active user profile; set their Desktop wallpaper registry value.
Get-ChildItem 'Registry::HKEY_USERS' | Where-Object {{ $_.Name -match 'S-1-5-21-' -and $_.Name -notlike '*_Classes' }} |
  ForEach-Object {{
    $sid = $_.PSChildName
    $key = "Registry::HKEY_USERS\$sid\Control Panel\Desktop"
    if (Test-Path $key) {{
      Set-ItemProperty -Path $key -Name 'Wallpaper' -Value $img -ErrorAction SilentlyContinue
      Set-ItemProperty -Path $key -Name 'WallpaperStyle' -Value '10' -ErrorAction SilentlyContinue
    }}
  }}
# Force refresh for currently signed-in user (SystemParametersInfo)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class W {{
  [DllImport("user32.dll", CharSet=CharSet.Auto)]
  public static extern int SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}}
"@
[W]::SystemParametersInfo(20, 0, $img, 3) | Out-Null
'OK'
"""
    proc = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                capture_output=True, text=True, timeout=30)
    return {
        "success": proc.returncode == 0 and "OK" in (proc.stdout or ""),
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": {"saved_to": str(target)},
    }


def _action_reset_password(params: dict, cfg: dict) -> dict:
    """Reset a LOCAL user's password via `net user`. Domain accounts unaffected.
    Audit-trail-friendly: the new password isn't echoed back in stdout.
    """
    username = (params.get("username") or "").strip()
    new_password = params.get("new_password") or ""
    if not username or not new_password:
        return {"success": False, "exit_code": -1,
                "stderr": "missing username or new_password",
                "stdout": None, "result": None}
    # Use net user <name> <pw>. /domain explicitly excluded -> local only.
    proc = _run(["net.exe", "user", username, new_password],
                capture_output=True, text=True, timeout=30)
    # Scrub stdout so the password never appears in the audit log
    out = (proc.stdout or "").replace(new_password, "***")
    err = (proc.stderr or "").replace(new_password, "***")
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": out, "stderr": err,
        "result": {"username": username, "scope": "local"},
    }


def _action_custom_powershell(params: dict, cfg: dict) -> dict:
    """Break-glass: run admin-supplied PowerShell. Audit-logged on the server.
    Capped at 10 min timeout. ExecutionPolicy Bypass; runs as SYSTEM.
    """
    script = params.get("script") or ""
    if not script.strip():
        return {"success": False, "exit_code": -1,
                "stderr": "missing 'script' param", "stdout": None, "result": None}
    proc = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True, text=True, timeout=600)
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "result": {"script_length": len(script)},
    }


def _action_force_refresh(_params: dict, cfg: dict) -> dict:
    """Admin-triggered immediate full check-in. Runs the same heavy work
    as the slow cycle (self-update + full inventory + patch scan + WU
    policy enforcement) without waiting for the next scheduled tick.

    Posts a fresh AssetSnapshot so the dashboard reflects the endpoint's
    current state within seconds of the admin clicking "↻ Refresh now".
    """
    try:
        _self_update_if_newer(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("force_refresh: self-update step skipped: %s", e)
    try:
        snap = collect_snapshot()
        base = cfg["server_url"].rstrip("/")
        code, body = _http(base + "/api/v1/agent/checkin",
                           method="POST", body=snap, token=cfg["agent_token"])
        if code != 200:
            return {"success": False, "exit_code": code,
                    "stdout": None, "stderr": (body or "")[:1500],
                    "result": None}
        return {
            "success": True, "exit_code": 0,
            "stdout": None, "stderr": None,
            "result": {
                "os":             (snap.get("os") or {}).get("caption"),
                "software_count": len(snap.get("software") or []),
                "patches_count":  len(snap.get("patches") or []),
                "snapshot_at":    snap.get("snapshot_at"),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "exit_code": 1,
                "stdout": None, "stderr": f"{type(e).__name__}: {e}",
                "result": None}


_ACTION_HANDLERS = {
    "run_executable":    _action_run_executable,
    "reboot":            _action_reboot,
    "lock_workstation":  _action_lock_workstation,
    "send_toast":        _action_send_toast,
    "list_processes":    _action_list_processes,
    "kill_process":      _action_kill_process,
    "set_wallpaper":     _action_set_wallpaper,
    "reset_password":    _action_reset_password,
    "custom_powershell": _action_custom_powershell,
    "force_refresh":     _action_force_refresh,
}


def cmd_daemon(_args: argparse.Namespace) -> int:
    """Long-running daemon with two cadences:

      1. FAST  — every DEPLOY_POLL_SECONDS (default 300s = 5 min):
                 lightweight GET /api/v1/agent/deployments and execute any
                 pending work. No inventory snapshot, no patch scan. Result:
                 the moment an admin clicks "Push" in the UI, the endpoint
                 picks up the deployment within 5 minutes — no PowerShell
                 needed on the user's laptop.

      2. SLOW  — every checkin_interval_hours (default 6h):
                 full inventory + patch_scan + Windows Update lock-down
                 + self-update of the agent script. Heavier; runs on the
                 6-hour cycle. Idempotent.

    The first iteration runs a full slow check-in immediately so a freshly
    booted machine reports inventory + applies the WU policy before the
    first hour-long sleep.
    """
    cfg = load_config()
    cfg = register_if_needed(cfg)
    # Slow cycle: full inventory + patch scan + WU policy enforcement.
    # Default 2 hours. Now that self-update lives in the fast cycle, the
    # slow cycle's only unique job is the heavy inventory snapshot.
    # Admin can also force an immediate refresh from the dashboard via
    # the force_refresh remote action — no need to wait for the schedule.
    slow_interval_h = max(0.25, float(cfg.get("checkin_interval_hours", 2)))
    # Fast cycle: self-update + patches + actions. 30 s = sub-minute
    # propagation of every new admin click + every server-side fix.
    fast_interval_s = max(15, int(cfg.get("deploy_poll_seconds", 30)))

    log.info("Daemon mode: full check-in every %.2f h, fast poll every %d s",
             slow_interval_h, fast_interval_s)

    # Run a full check-in immediately on start.
    checkin_once(cfg)
    last_full_checkin = time.time()

    while True:
        try:
            time.sleep(fast_interval_s)
            now = time.time()
            if now - last_full_checkin >= slow_interval_h * 3600:
                # Time for the heavy cycle: full inventory + patch scan +
                # self-update + deployment execution.
                checkin_once(cfg)
                last_full_checkin = now
            else:
                # Fast cycle: every poll we check for THREE kinds of work, all
                # cheap HTTP GETs that 200-OK with empty body when there's
                # nothing to do.
                #   0. Self-update     — agent script bump (auto re-exec)
                #   1. Patch deploys   — Install-WindowsUpdate / winget
                #   2. Remote actions  — run .exe, reboot, lock, toast, ...
                # Self-update FIRST so a brand-new handler lands and is
                # immediately callable on this same iteration (post-execv).
                try:
                    _self_update_if_newer(cfg)
                except Exception as e:  # noqa: BLE001
                    log.warning("Fast self-update poll failed: %s", e)

                deploys = 0
                actions = 0
                try:
                    deploys = execute_pending_deployments(cfg)
                except Exception as e:  # noqa: BLE001
                    log.warning("Fast deployment poll failed: %s", e)
                try:
                    actions = execute_pending_actions(cfg)
                except Exception as e:  # noqa: BLE001
                    log.warning("Fast action poll failed: %s", e)

                if deploys or actions:
                    log.info("Fast cycle: %d deployment(s) + %d action(s) processed",
                             deploys, actions)
                    # Push fresh inventory after side-effects (new software
                    # installed, processes killed, etc. should be reflected).
                    try:
                        snap = collect_snapshot()
                        _http(cfg["server_url"].rstrip("/") + "/api/v1/agent/checkin",
                              method="POST", body=snap, token=cfg["agent_token"])
                    except Exception as e:  # noqa: BLE001
                        log.warning("post-action resync failed: %s", e)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Daemon loop error: %s", e)
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
