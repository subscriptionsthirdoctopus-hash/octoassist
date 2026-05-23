# OctoAssist Agent — GPO Deployment Runbook (No EV Cert Path)

**Audience:** TEMA India IT / domain admin team (or equivalent at any client).
**Goal:** Deploy the OctoAssist Asset Agent MSI to all in-scope Windows endpoints **without an EV code-signing certificate**, while staying out of Microsoft Defender's quarantine.

This runbook is the work-around for the fact that Third Octopus is shipping the agent with a **self-signed certificate**, not a publicly-trusted EV cert. Inside an Active Directory environment this is functionally equivalent: once the self-signed publisher cert is pushed into the domain's Trusted Publishers store via GPO, every domain-joined endpoint treats binaries signed by that cert as trusted.

> **Estimated time:** 30–60 minutes for a domain admin who has done a software-deployment GPO before.

---

## Prerequisites

- Active Directory domain (TEMA's existing domain).
- Domain administrator credentials.
- A network share readable by `Domain Computers` (e.g., `\\dc01\Software\OctoAssist\`).
- Files supplied by Third Octopus:
  - `OctoAssistAgent.msi` — the signed installer
  - `ThirdOctopus-CodeSigning.cer` — the public certificate to trust
  - The **enrolment key** (a long random string, displayed in the OctoAssist admin UI under **Enrolment**)
  - The **server URL** (e.g., `https://octoassist.tema.local`)

---

## Step 1 — Stage the files on the network share

1. Create `\\dc01\Software\OctoAssist\` (or equivalent).
2. Set NTFS permissions: `Domain Computers` → Read & Execute.
3. Copy the three files there:
   - `OctoAssistAgent.msi`
   - `ThirdOctopus-CodeSigning.cer`
   - `enrolment-info.txt` (you create this — contains the SERVER_URL and ENROLMENT_KEY for reference)

---

## Step 2 — Trust the publisher domain-wide (GPO #1)

This is what stops Microsoft Defender (and most other AVs) from treating the unsigned-from-the-OS-perspective binary as suspicious.

1. Open **Group Policy Management** (`gpmc.msc`) on a DC.
2. Right-click the OU containing your endpoints → **Create a GPO in this domain, and Link it here…**
3. Name it: `OctoAssist - Trust Publisher`
4. Edit the GPO:
   - **Computer Configuration → Policies → Windows Settings → Security Settings → Public Key Policies → Trusted Publishers**
   - Right-click → **Import…** → browse to `\\dc01\Software\OctoAssist\ThirdOctopus-CodeSigning.cer`.
5. Repeat the import under:
   - **Computer Configuration → Policies → Windows Settings → Security Settings → Public Key Policies → Trusted Root Certification Authorities** *(only if Windows complains about root trust during pilot — usually not needed for code signing)*
6. Close the editor.

> **Verify on a test machine:** run `gpupdate /force` and then `Get-ChildItem Cert:\LocalMachine\TrustedPublisher | Where-Object Subject -like "*Third Octopus*"`. The cert should appear.

---

## Step 3 — Microsoft Defender exclusions (GPO #2)

Belt-and-braces: even with publisher trust, some AV configurations still throw heuristic alerts on PyInstaller-style or self-extracting binaries during install. We add explicit path/process exclusions for OctoAssist.

1. Create another GPO: `OctoAssist - Defender Exclusions`.
2. Edit:
   - **Computer Configuration → Policies → Administrative Templates → Windows Components → Microsoft Defender Antivirus → Exclusions**
3. Configure:
   - **Path Exclusions** → Enabled → Add:
     - `C:\Program Files\Third Octopus\OctoAssist Agent\` → value `0`
     - `C:\ProgramData\OctoAssist\` → value `0`
   - **Process Exclusions** → Enabled → Add:
     - `OctoAssistAgent.exe` → value `0`
4. Link the GPO to the same OU.

> If the org runs **a different AV** (Kaspersky, Bitdefender, Sophos, CrowdStrike, etc.) — apply the equivalent exclusions in *that* AV's central console. The two paths and the process name are the same.

---

## Step 4 — Software install GPO (GPO #3)

1. Create another GPO: `OctoAssist - Agent Install`.
2. Edit:
   - **Computer Configuration → Policies → Software Settings → Software installation**
3. Right-click → **New → Package…**
4. Browse to `\\dc01\Software\OctoAssist\OctoAssistAgent.msi` (must be a UNC path, not a local drive).
5. Choose **Assigned**.
6. After it's added, right-click the package → **Properties → Modifications**. We need to pass `SERVER_URL` and `ENROLMENT_KEY` as MSI properties.
   - Because GPO software-install does not natively support per-package property arguments, the simplest way is one of:
     - **Option A (recommended):** Create a one-line MST transform with [Orca](https://learn.microsoft.com/en-us/windows/win32/msi/orca-exe) that hard-codes SERVER_URL and ENROLMENT_KEY for this tenant, and attach the MST here.
     - **Option B (fallback):** Skip Software Installation GPO and use a **scheduled task** GPO that runs once at startup with: `msiexec /i \\dc01\Software\OctoAssist\OctoAssistAgent.msi SERVER_URL=https://octoassist.tema.local ENROLMENT_KEY=XXXX /qn`

7. Link the GPO to the OU.

---

## Step 5 — Pilot on 3–5 endpoints first

1. Move 3–5 test machines into the OU (or create a sub-OU for pilot).
2. On each: `gpupdate /force` then **reboot** (Software Installation requires reboot to apply at machine scope).
3. After reboot, verify:
   - `sc query OctoAssistAgent` → should show **RUNNING**.
   - `C:\ProgramData\OctoAssist\agent.json` exists and contains your SERVER_URL.
   - The OctoAssist admin web UI shows the new endpoint within 5 minutes.
   - **Microsoft Defender → Protection history** has no quarantine event for `OctoAssistAgent.exe`.

If all four are green, expand to the full OU.

---

## Step 6 — Roll out

1. Move the rest of the endpoints into the target OU.
2. Force a refresh during the next maintenance window or wait for the natural 90-minute GPO refresh cycle.
3. Watch the admin UI — every endpoint that receives the policies and reboots will show up automatically.

---

## Step 7 — Uninstalling later (rollback path)

If you ever need to remove the agent:

1. In the `OctoAssist - Agent Install` GPO, right-click the package → **All Tasks → Remove**.
2. Choose **Immediately uninstall the software from users and computers**.
3. On next refresh + reboot, the MSI uninstall sequence runs, the `OctoAssistAgent` service is removed, and `C:\Program Files\Third Octopus\` is cleaned up.
4. The Trusted Publisher cert and Defender exclusions can be left in place or removed by unlinking GPOs #1 and #2.

---

## Why this is acceptable practice

Every commercial endpoint agent (CrowdStrike Falcon, SentinelOne, Microsoft Defender for Endpoint, ManageEngine, Lansweeper, etc.) requires AV exclusions during deployment. The Trusted Publishers GPO step replaces what an EV code-signing certificate would buy us in the wider internet. Inside the TEMA AD perimeter the trust boundary is the domain — once the domain trusts the publisher, the endpoints inherit that trust automatically.

When budget allows, Third Octopus will procure a real EV code-signing certificate (DigiCert / Sectigo, ~$300–600/year) and re-sign the agent. Endpoints will continue to trust it without any action because the new chain will already be valid; the GPO #1 cert can then be retired.
