# Deployment Narrative — End-to-End

For TEMA India, day-of go-live. Follow top to bottom.

## 0. Inputs you need

| Item | Where it comes from |
|---|---|
| Ubuntu 22.04 VM (4 vCPU, 8 GB RAM, 100 GB disk) | TEMA infra team — internal subnet, fixed IP, DNS name like `octoassist.tema.local` |
| Domain admin access | TEMA IT |
| Network share readable by Domain Computers | TEMA IT (e.g. `\\dc01\Software\OctoAssist\`) |
| A Windows 10/11 host for the build | Your laptop or any clean VM |

---

## 1. Build the artefacts (your Windows host, ~30 min)

```powershell
# .NET 8 SDK
winget install Microsoft.DotNet.SDK.8

# WiX 4 as a global dotnet tool
dotnet tool install --global wix

# Windows 11 SDK (for signtool.exe) — required for signing
winget install Microsoft.WindowsSDK.10.0.22621
```

Then in the repo:

```powershell
cd OctoAssist

# 1a. Generate a self-signed code-signing certificate (one-time per company)
PowerShell -ExecutionPolicy Bypass -File .\deploy\generate-cert.ps1
# -> .\deploy\out\ThirdOctopus-CodeSigning.pfx   (KEEP SECRET — back it up)
# -> .\deploy\out\ThirdOctopus-CodeSigning.cer   (give to TEMA IT)

# 1b. Build the MSI
PowerShell -ExecutionPolicy Bypass -File .\installer\build-msi.ps1
# -> .\dist\OctoAssistAgent.msi

# 1c. Sign the MSI
PowerShell -ExecutionPolicy Bypass -File .\deploy\sign-msi.ps1 -MsiPath .\dist\OctoAssistAgent.msi
```

After step 1c, `OctoAssistAgent.msi` is signed by `Third Octopus`. On any machine that has the `.cer` in its Trusted Publishers store, the install will run silently with no SmartScreen prompt and no Defender quarantine.

---

## 2. Stand up the server (TEMA's Windows Server VM, ~30 min)

Copy the repo to the server (RDP + paste, or git clone, or scp via OpenSSH).

### 2a. Verify prerequisites

```powershell
# As Administrator
cd <repo>\deploy\windows
.\Preflight-Check.ps1
```

The script verifies:
- Windows Server edition
- PowerShell 5.1+
- Python 3.11+ on PATH
- PostgreSQL 14+ service running
- IIS Web Server role
- IIS URL Rewrite module
- IIS Application Request Routing module
- NSSM (auto-downloaded if missing)

For each `MISSING` item, it prints the exact install command. Resolve everything before continuing.

### 2b. Run the installer

```powershell
.\Install-OctoAssist-Server.ps1
```

The script (idempotent — safe to re-run):
1. Copies the FastAPI app to `C:\Program Files\Third Octopus\OctoAssist Server\`
2. Creates a Python venv and pip-installs the app
3. Creates the Postgres role + database (asks for the postgres superuser password once)
4. Writes `.env` to `C:\ProgramData\OctoAssist\Server\` with random admin and DB passwords
5. Downloads NSSM from nssm.cc (~300 KB) if missing
6. Registers the **OctoAssistServer** Windows Service via NSSM, runs as `NT AUTHORITY\NetworkService`
7. Configures the IIS **OctoAssist** site on port 80 with URL Rewrite reverse-proxy → `127.0.0.1:8080`
8. Opens the firewall on TCP/80
9. Prints **the admin password** and **the tenant enrolment key** at the end

**Save those two values.** They are also in `C:\ProgramData\OctoAssist\Server\.env` (Administrators + SYSTEM only).

Verify:

```powershell
Invoke-WebRequest http://localhost/health -UseBasicParsing
# StatusCode 200, Content '{"status":"ok"}'

Invoke-WebRequest http://localhost/ -Credential (Get-Credential admin) -UseBasicParsing
# 200 with the empty asset register HTML
```

Open `http://octoassist.tema.local/enrolment` in a browser, authenticate with `admin` + the password from above, and copy the enrolment key shown.

### 2-alt. Ubuntu deployment

If TEMA prefers Ubuntu 22.04 over Windows Server, use `deploy/linux/install-server.sh` instead. Same outcome, same admin URL, same enrolment-key flow — just systemd/nginx in place of NSSM/IIS.

---

## 3. Hand over to TEMA IT (~30–60 min, them not you)

Send TEMA IT three things:

1. The signed **`OctoAssistAgent.msi`** (from step 1)
2. The **`ThirdOctopus-CodeSigning.cer`** public certificate (from step 1)
3. The runbook **`deploy/gpo-runbook.md`** with two values filled in:
   - `SERVER_URL`: `https://octoassist.tema.local` (or whatever DNS name they gave you in step 0)
   - `ENROLMENT_KEY`: the value from `/enrolment`

They follow the runbook to:
- Push the cert into Trusted Publishers via GPO #1
- Push Defender exclusions via GPO #2
- Push the MSI install via GPO #3 (with an MST or a startup-scheduled-task)

They pilot on 3–5 endpoints, then expand.

---

## 4. Verify & monitor

Within minutes of the pilot endpoints rebooting, you should see:

- The endpoint hostname appears at `http://octoassist.tema.local/` (asset register).
- Clicking through shows OS, CPU, RAM, disks, network, software inventory.
- `last_seen_at` updates every 6 hours.
- No quarantine event on the endpoint's Microsoft Defender history.

If an endpoint does NOT show up:

```powershell
# On the endpoint
sc query OctoAssistAgent          # is the service running?
type C:\ProgramData\OctoAssist\agent.json   # does config exist?
Get-EventLog Application -Source OctoAssistAgent -Newest 20  # what does the agent say?
```

Most-common failure modes:
- **Service not installed at all** → MSI didn't run; check `%TEMP%\OctoAssistAgent.log` and the GPO Software Installation event log.
- **Service installed but agent.json missing** → Bootstrap CA failed; re-run the install with `/l*v` and read the log around `WriteAgentConfig`.
- **Service running but server URL unreachable** → DNS or firewall; `Test-NetConnection octoassist.tema.local -Port 80` from the endpoint.
- **Service running, server reachable, no rows in DB** → enrolment key wrong; check `/api/v1/agent/register` returns 200 not 403.

---

## 5. What "go-live" means in scope

Per the proposal, week 5 = go-live + UAT sign-off. For Phase 1 (this build), going live means:

- ✅ Server up and healthy at TEMA
- ✅ Agent on every in-scope endpoint
- ✅ Asset register populated and refreshing
- ❌ NOT YET: Patch Management, Incident/Change/Problem/SR, KB, SLA, M365 SSO

The remaining modules are subsequent phase deliveries. Be explicit with TEMA about that gap before sign-off, otherwise you'll walk into a UAT failure.
