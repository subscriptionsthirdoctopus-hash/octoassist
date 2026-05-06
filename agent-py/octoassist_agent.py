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
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_OperatingSystem | "
             "Select-Object Caption,Version,BuildNumber,OSArchitecture | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15,
        )
        d = json.loads(r.stdout or "{}")
        return {
            "caption": d.get("Caption", "").strip(),
            "version": d.get("Version", ""),
            "build_number": str(d.get("BuildNumber", "")),
            "architecture": d.get("OSArchitecture", ""),
            "install_date": None,
        }
    except Exception:
        return {"caption": platform.platform(), "version": platform.release(),
                "build_number": "", "architecture": platform.machine(), "install_date": None}


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


def collect_snapshot() -> dict:
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
    }


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


def checkin_once(cfg: dict) -> bool:
    snap = collect_snapshot()
    log.info("Collected snapshot: OS=%s, CPU=%s, software=%d",
             snap["os"].get("caption"), snap["cpu"].get("name"), len(snap["software"]))
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
