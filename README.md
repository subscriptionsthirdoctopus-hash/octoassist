# OctoAssist — Phase 1 (Asset Discovery)

ITIL-aligned ITSM platform by **Third Octopus**. This Phase 1 build delivers the **Asset Agent + Server** loop end-to-end. Patch Management, Incident, Change, KB, etc. are pinned for later phases.

```
[ Windows endpoints ]               [ Windows Server at client ]
                                    ┌───────────────────────────┐
 ┌──────────────────────┐    HTTPS  │ IIS :80/:443              │
 │ OctoAssistAgent.exe  │ ────────► │  └─► uvicorn :8080        │
 │ (Windows Service,    │           │       FastAPI app         │
 │  C# .NET 8 single-   │           │       Postgres 16         │
 │  file MSI deployed)  │           │                           │
 └──────────────────────┘           │  /          → admin UI    │
                                    │  /asset/:id → detail page │
                                    │  /api/v1/*  → JSON API    │
                                    └───────────────────────────┘

  Server platform: Windows Server 2019/2022/2025 (primary)
                   Ubuntu 22.04 LTS (alternative — see deploy/linux/)
```

## Repository layout

```
OctoAssist/
├── server/                    Python 3.11 / FastAPI / Postgres
│   ├── app/
│   │   ├── api/               /api/v1/agent/{register,checkin}, /api/v1/assets
│   │   ├── web/               Server-rendered admin UI
│   │   ├── templates/         Jinja2
│   │   ├── static/            CSS
│   │   ├── auth.py            Bearer (agent), HTTP-Basic (admin)
│   │   ├── config.py          12-factor env settings
│   │   ├── database.py        SQLAlchemy
│   │   ├── main.py            FastAPI bootstrap + DB init
│   │   ├── models.py          Tenant, Agent, AssetSnapshot
│   │   └── schemas.py         Pydantic
│   ├── pyproject.toml
│   └── .env.example
│
├── agent/                     C# .NET 8 Windows Service
│   ├── Models/AssetSnapshot.cs
│   ├── AgentConfig.cs         Reads/writes C:\ProgramData\OctoAssist\agent.json
│   ├── ApiClient.cs           HTTP register + checkin
│   ├── AssetCollector.cs      WMI + registry inventory
│   ├── Bootstrap.cs           --bootstrap mode (called by MSI)
│   ├── Program.cs             Host + DI
│   ├── Worker.cs              BackgroundService check-in loop
│   └── OctoAssistAgent.csproj
│
├── installer/                 WiX 4 MSI
│   ├── OctoAssistAgent.wixproj
│   ├── Package.wxs
│   └── build-msi.ps1
│
├── deploy/
│   ├── windows/               PRIMARY — server install on Windows Server
│   │   ├── Install-OctoAssist-Server.ps1
│   │   ├── Uninstall-OctoAssist-Server.ps1
│   │   ├── Preflight-Check.ps1     prereqs validator (Python/Postgres/IIS/URL Rewrite/ARR)
│   │   ├── Install.cmd             double-click entry point (auto-elevates)
│   │   ├── web.config              IIS reverse proxy config
│   │   └── README.md               Windows-specific quickstart
│   ├── linux/                 ALTERNATIVE — server install on Ubuntu 22.04
│   │   ├── install-server.sh
│   │   ├── octoassist.service
│   │   └── nginx-octoassist.conf
│   ├── generate-cert.ps1      Self-signed code-signing cert (for the agent MSI)
│   ├── sign-msi.ps1           Sign the agent MSI with the cert
│   └── gpo-runbook.md         How TEMA IT pushes the agent + cert + AV exclusions
│
└── docs/
    ├── ARCHITECTURE.md
    └── DEPLOYMENT.md          End-to-end deployment narrative
```

## Quickstart

### 1. Server (Windows Server VM at the client) — primary path

```powershell
# As Administrator, from the repo root:
cd deploy\windows
.\Preflight-Check.ps1                # tells you if Python/Postgres/IIS/URL Rewrite/ARR are missing
.\Install-OctoAssist-Server.ps1      # or double-click Install.cmd
```

Prints the admin password and tenant **enrolment key** at the end. Both are stored in `C:\ProgramData\OctoAssist\Server\.env` (Administrators + SYSTEM only).

See [deploy/windows/README.md](deploy/windows/README.md) for full details, HTTPS setup, and troubleshooting.

### 1-alt. Server on Ubuntu 22.04 (alternative — clean droplet)

```bash
sudo bash deploy/linux/install-server.sh
```

Same outcome, different OS. Prints credentials at the end; stored in `/opt/octoassist/.env`.

### 1-cohost. Co-host on the existing `hrms-erp` droplet (Ubuntu 24.04)

For the actual production deploy on the existing Third Octopus DigitalOcean droplet alongside HRMS:

```bash
sudo bash deploy/linux/install-cohost.sh
```

Adds an *additive* nginx site for `octoassist.thirdoctopus.com` on internal port 8088, leaves HRMS untouched, issues Let's Encrypt cert. Full runbook: [docs/COHOST-DEPLOYMENT.md](docs/COHOST-DEPLOYMENT.md).

### 2. Agent MSI (build on a Windows host)

```powershell
# install prereqs once
winget install Microsoft.DotNet.SDK.8
dotnet tool install --global wix

# build the cert (once, keep the .pfx safe)
PowerShell -ExecutionPolicy Bypass -File .\deploy\generate-cert.ps1

# build and sign the MSI
PowerShell -ExecutionPolicy Bypass -File .\installer\build-msi.ps1
PowerShell -ExecutionPolicy Bypass -File .\deploy\sign-msi.ps1 -MsiPath .\dist\OctoAssistAgent.msi
```

### 3. Distribute to endpoints (TEMA IT does this — see `deploy/gpo-runbook.md`)

```cmd
msiexec /i OctoAssistAgent.msi ^
  SERVER_URL=https://octoassist.tema.local ^
  ENROLMENT_KEY=<paste from step 1> ^
  /qn /l*v %TEMP%\OctoAssistAgent.log
```

Then watch the admin UI — endpoints appear within minutes of first check-in.

## Security model (Phase 1, MVP)

| Surface | Auth |
|---|---|
| Admin web UI (`/`, `/asset/:id`, `/enrolment`) | HTTP Basic, single admin user from env |
| Agent register (`POST /api/v1/agent/register`) | Tenant-scoped enrolment key |
| Agent checkin (`POST /api/v1/agent/checkin`) | Long-lived per-agent bearer token |

This is intentionally minimal for the 2-day MVP. Phase 2 hardening: M365/Entra SSO for the admin UI, agent token rotation, mTLS option.

## What's NOT in this Phase 1

The TEMA proposal lists 11 modules. This build delivers **Asset Agent + Server inventory** only. Out of scope here, scoped for later phases:

- Patch Management (deployment, scheduling, compliance dashboard)
- Incident / Problem / Change / Service Request modules
- Knowledge Base
- SLA matrix engine
- Self-Service Portal for end-users
- M365 / Entra ID SSO
- Audit & Compliance Logging beyond app logs
- 50+ pre-built reports

## License / Confidentiality

Internal Third Octopus codebase. Do not redistribute.
