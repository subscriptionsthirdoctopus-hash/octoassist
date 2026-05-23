# Architecture — OctoAssist Phase 1

## Components

### Server (`server/`)

- **Python 3.11**, FastAPI, SQLAlchemy 2, PostgreSQL 16.
- Single process: `uvicorn app.main:app` on `127.0.0.1:8080` behind nginx on `:80`.
- Single tenant per deployment (`tenants` table holds exactly one row, bootstrapped on first start).

### Agent (`agent/`)

- **C# .NET 8** Worker Service.
- Published as **self-contained, single-file, win-x64**. No .NET runtime needed on the endpoint.
- Runs as `LocalSystem` so it can read WMI and the HKLM uninstall keys.
- Logs to the Windows **Application** event log under source `OctoAssistAgent`.

### Installer (`installer/`)

- **WiX Toolset v4** (modern .wixproj SDK).
- MSI installs the EXE to `C:\Program Files\Third Octopus\OctoAssist Agent\`.
- Registers the Windows Service `OctoAssistAgent` (auto-start, LocalSystem, restart on failure inherited from default service control).
- A deferred custom action invokes the EXE in `--bootstrap` mode to write the initial `C:\ProgramData\OctoAssist\agent.json` from MSI properties (`SERVER_URL`, `ENROLMENT_KEY`).

## Data model

```
tenants(id, name, enrolment_key UNIQUE, created_at)
   └─ has many ─►
agents(id, tenant_id, machine_id UNIQUE, hostname, agent_token UNIQUE, registered_at, last_seen_at)
   └─ has many ─►
asset_snapshots(id, agent_id, snapshot_at, payload JSONB)
```

The full agent payload is stored as JSONB on each check-in. This keeps the schema flexible: when the agent grows new fields (e.g., disk SMART data, GPU info, BitLocker status), no migration is needed. Indexed access patterns we care about (latest per agent) are served by the `(agent_id, snapshot_at DESC)` index.

## Auth

- **Enrolment key**: tenant-wide secret, displayed once in the admin UI under `/enrolment`. Used only by the agent's `register` call.
- **Agent token**: 32-byte URL-safe random, generated server-side at registration. Long-lived. Sent as `Authorization: Bearer …` on every check-in. Stored in `C:\ProgramData\OctoAssist\agent.json` (only `LocalSystem` and Administrators can read).
- **Admin user**: HTTP Basic, single user from env (`OCTOASSIST_ADMIN_USERNAME` / `_PASSWORD`). Phase 2 will swap this for M365/Entra ID SSO via OAuth2.

## Check-in flow

```
1. Service starts at boot (LocalSystem)
2. AgentConfig.Load() reads C:\ProgramData\OctoAssist\agent.json
3. If !IsRegistered:
     POST /api/v1/agent/register  { enrolment_key, machine_id, hostname }
     -> { agent_id, agent_token }
     Save back to agent.json
4. Collect snapshot via WMI + registry
5. POST /api/v1/agent/checkin (Bearer agent_token)
     body = full AssetSnapshot JSON
6. Sleep CheckinIntervalHours (default 6h), loop to step 4
```

Failure modes:
- Network down → next interval retries; nothing breaks.
- Server returns 401 (token revoked) → agent logs error, does NOT auto-re-register (deliberate; revocation is a real signal).
- Server returns 5xx → log + continue.

## Endpoint identity (`machine_id`)

We use `Win32_ComputerSystemProduct.UUID` (the SMBIOS UUID baked into the motherboard). It survives OS reinstalls and is stable across boots. If the hardware reports the all-zeros UUID (some VMs do), we fall back to `MachineName + Platform`.

## What the agent collects

| Group | Source | Fields |
|---|---|---|
| OS | `Win32_OperatingSystem` | caption, version, build_number, architecture, install_date |
| CPU | `Win32_Processor` | name, cores, logical_processors, max_clock_mhz |
| Memory | `Win32_ComputerSystem.TotalPhysicalMemory` | total_gb |
| Disks | `Win32_LogicalDisk WHERE DriveType=3` | drive, filesystem, size_gb, free_gb |
| Network | `System.Net.NetworkInformation.NetworkInterface` | name, mac, ip (v4) |
| BIOS | `Win32_BIOS` | manufacturer, version, serial |
| System | `Win32_ComputerSystem` | manufacturer, model, domain/workgroup |
| Logged-in user | `Win32_ComputerSystem.UserName` | string |
| Software | `HKLM\…\Uninstall` (+ WOW6432Node) | name, version, publisher, install_date |

The DisplayName + DisplayVersion + Publisher tuple is what every commercial software asset tool (SCCM, Lansweeper, ManageEngine) reads. We dedupe by `(name, version)` and skip entries flagged `SystemComponent=1` (driver entries, redistributables not visible in Add/Remove Programs).
