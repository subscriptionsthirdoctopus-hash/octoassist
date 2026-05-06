# OctoAssist Cross-Platform Agent

A single-file Python asset agent. Runs on Linux, macOS, and Windows. Standard library only — no `pip install` step.

## Why a Python agent

The Phase 1 C# agent (`/agent/`) is purpose-built for Windows endpoints and ships as an MSI. **This Python agent is the cross-platform alternative** for:

- Linux servers (the OctoAssist droplet itself, Ubuntu / Debian / RHEL workstations)
- macOS dev machines
- Windows hosts where the C# MSI hasn't been built yet (or where AV won't accept an unsigned MSI but will tolerate a stock `python.exe` running a `.py` file via Task Scheduler)

## Install — Linux (Ubuntu / Debian / RHEL)

```bash
# On the target host, as root:
sudo bash install-linux.sh \
    --server-url=https://octoassist.thirdoctopus.com \
    --enrolment-key=PASTE_YOUR_TENANT_ENROLMENT_KEY
```

What it does:
- Installs the script at `/usr/local/bin/octoassist-agent` (mode 0755)
- Writes config to `/etc/octoassist/agent.json` (mode 0600)
- Registers a `systemd` unit `octoassist-agent.service`, enables and starts it
- First check-in registers the host with OctoAssist and stores a long-lived bearer token

Verify:
```bash
systemctl status octoassist-agent
journalctl -u octoassist-agent -f
octoassist-agent --once     # manual one-shot for debugging
```

## Install — Windows (10 / 11 / Server 2019+)

```powershell
# Open PowerShell as Administrator, in this folder
.\install-windows.ps1 `
    -ServerUrl https://octoassist.thirdoctopus.com `
    -EnrolmentKey PASTE_YOUR_TENANT_ENROLMENT_KEY
```

Requires Python 3.10+ in PATH (`winget install Python.Python.3.12` if not).

What it does:
- Installs `octoassist_agent.py` to `C:\Program Files\OctoAssist Agent\`
- Writes config to `%PROGRAMDATA%\OctoAssist\agent.json`
- Registers a Task Scheduler task **OctoAssist Agent** that runs at boot, as `SYSTEM`, with restart-on-failure
- Starts the task immediately so first check-in happens in seconds

Verify:
```powershell
Get-ScheduledTask -TaskName "OctoAssist Agent"
Get-Content "$env:ProgramData\OctoAssist\logs\agent.log" -Tail 30 -Wait
python "C:\Program Files\OctoAssist Agent\octoassist_agent.py" --once
```

## Manual / one-shot run (for testing)

```bash
# Linux/macOS
python3 octoassist_agent.py --bootstrap \
    --server-url=URL --enrolment-key=KEY
python3 octoassist_agent.py --once

# Windows
python octoassist_agent.py --bootstrap `
    --server-url=URL --enrolment-key=KEY
python octoassist_agent.py --once
```

## What the agent collects

Standard ITIL inventory; the schema matches what the C# agent posts:

| Group | How it's gathered (Linux) | How it's gathered (Windows) |
|---|---|---|
| OS | `/etc/os-release` + `platform` | `Get-CimInstance Win32_OperatingSystem` |
| CPU | `/proc/cpuinfo` | `Get-CimInstance Win32_Processor` |
| Memory | `/proc/meminfo` | `Win32_ComputerSystem.TotalPhysicalMemory` |
| Disks | `df -P` | `Win32_LogicalDisk WHERE DriveType=3` |
| Network | `ip -json addr` | `Get-NetAdapter` + `Get-NetIPAddress` |
| BIOS | `/sys/class/dmi/id/*` | `Win32_BIOS` |
| System | `/sys/class/dmi/id/sys_vendor` etc. | `Win32_ComputerSystem` |
| Logged-in user | `who` | `Win32_ComputerSystem.UserName` |
| Software | `dpkg-query -W` or `rpm -qa` | HKLM `…\Uninstall\*` registry keys |

## Uninstall

**Linux:**
```bash
sudo systemctl stop octoassist-agent
sudo systemctl disable octoassist-agent
sudo rm /etc/systemd/system/octoassist-agent.service
sudo rm /usr/local/bin/octoassist-agent
sudo rm -rf /etc/octoassist /var/log/octoassist
sudo systemctl daemon-reload
```

**Windows:**
```powershell
Unregister-ScheduledTask -TaskName "OctoAssist Agent" -Confirm:$false
Remove-Item -Recurse "C:\Program Files\OctoAssist Agent"
Remove-Item -Recurse "$env:ProgramData\OctoAssist"
```

## Security notes

- The bearer token in `agent.json` lets the holder post check-ins as this endpoint. Treat it like a credential — it's why config files are mode 0600 (Linux) and ProgramData (Admins/SYSTEM only on Windows).
- Revoke a compromised endpoint by deleting its row in OctoAssist's `agents` table — the next check-in will get 401 and the agent stops accepting writes.
- The agent runs as `root` (Linux) / `SYSTEM` (Windows) so it can read installed-software inventory. If you want least privilege, run as a service account with read access to the relevant package DB / registry.
