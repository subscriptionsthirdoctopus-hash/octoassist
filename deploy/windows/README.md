# OctoAssist Server — Windows Deployment

Quickstart for installing the OctoAssist server on Windows Server (2019 / 2022 / 2025).

## TL;DR

1. **Pre-flight check** — see what's missing:
   ```powershell
   # Open PowerShell as Administrator
   cd <repo>\deploy\windows
   .\Preflight-Check.ps1
   ```
   Resolve every line marked `MISSING` (the script tells you the exact install command).

2. **Install** — single command:
   ```powershell
   .\Install-OctoAssist-Server.ps1
   ```
   Or double-click `Install.cmd` (which auto-elevates).

3. The script prints the **admin password** and **tenant enrolment key** at the end. Write them down. They are also stored in `C:\ProgramData\OctoAssist\Server\.env` (Administrators + SYSTEM only).

## What gets installed

| Item | Location |
|---|---|
| App code + venv | `C:\Program Files\Third Octopus\OctoAssist Server\` |
| Runtime config + logs | `C:\ProgramData\OctoAssist\Server\` |
| Windows Service | `OctoAssistServer` (auto-start, runs as `NT AUTHORITY\NetworkService`) |
| IIS site | `OctoAssist` on port 80, reverse-proxies to `127.0.0.1:8080` |
| Firewall | inbound TCP/80 allowed |
| NSSM | `C:\Program Files\nssm\nssm.exe` (downloaded from nssm.cc) |
| Postgres role + DB | `octoassist` role + `octoassist` DB on the local Postgres |

## Operations cheat-sheet

```powershell
# Service control
sc query OctoAssistServer
Restart-Service OctoAssistServer

# Logs (NSSM-rotated)
Get-Content "C:\ProgramData\OctoAssist\Server\logs\stdout.log" -Tail 50 -Wait
Get-Content "C:\ProgramData\OctoAssist\Server\logs\stderr.log" -Tail 50 -Wait

# Health
curl http://127.0.0.1:8080/health
curl http://localhost/health  # via IIS

# Inspect config (admin only)
Get-Content "C:\ProgramData\OctoAssist\Server\.env"
```

## Adding HTTPS (recommended before pilot)

1. Get an internal CA cert for `octoassist.tema.local` (or self-signed for testing).
2. In IIS Manager, select the OctoAssist site → **Bindings** → **Add** → type `https`, port `443`, pick the cert.
3. Replace the URL Rewrite rule's `HTTPS_OR_HTTP` map output to always emit `https`, or add a separate rule that 301-redirects port 80 to 443.
4. Tell agents to use `SERVER_URL=https://octoassist.tema.local`.

## Troubleshooting

**`Install-OctoAssist-Server.ps1` errors at `Find-Psql`**
→ Postgres isn't installed at `C:\Program Files\PostgreSQL\<ver>\`. Install Postgres 16 first.

**Service `OctoAssistServer` is `STOPPED` after install**
→ Check `C:\ProgramData\OctoAssist\Server\logs\stderr.log`. Most common cause: bad DB credentials in `.env`.

**IIS returns 502 Bad Gateway**
→ ARR proxy isn't enabled at server level. Run:
```powershell
& "$env:windir\system32\inetsrv\appcmd.exe" set config /commit:apphost `
    /section:system.webServer/proxy /enabled:true
```

**`/api/v1/agent/checkin` returns 401 from agent**
→ Agent token mismatch. Confirm `agent.json` on the endpoint has a `agent_token` value, and that the corresponding row exists in the `agents` table.

## Rolling back

```powershell
.\Uninstall-OctoAssist-Server.ps1
# Add -KeepData to preserve C:\ProgramData\OctoAssist\Server\
```

The Postgres DB and role are NOT dropped — that's deliberate. Drop manually if you want a clean wipe.
