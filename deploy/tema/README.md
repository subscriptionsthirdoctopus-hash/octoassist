# TEMA India — hand-applied production patches

Stopgap patches applied directly to TEMA's self-hosted OctoAssist while their
build is out of step with this repository. **They are not part of the product.**
Every fix here also exists, properly, in `main` — these scripts only exist
because that build cannot take our source.

## Why these exist

TEMA self-host at `octoassist.temaindia.com` (103.167.223.81), a Windows box
running from `C:\Program Files\Third Octopus\OctoAssist Server`. That build is a
different lineage from this repo: it has an `ENDPOINTS` column and an Asset
Management module we do not, defines its own `--topbar-h`, and is **not a git
checkout**. Copying our files over it would take out screens that work today, so
each patch below is scoped to the smallest safe unit and is individually
reversible.

Only 443 is open on that host — no SSH from outside. Access is RDP on
`103.167.223.81:55081`; the scripts are run in PowerShell on the server itself.

## What is applied (as of 21 Aug 2026)

| Script / change | Fixes | Touches | Restart |
|---|---|---|---|
| `Apply-HeaderFix.ps1` + `tema-header-fix.css` | Table header / row overlap | appends to `app/static/styles.css` | no |
| `Patch-Item6.ps1` | Patch Compliance drilldown HTTP 500 | replaces one `elif` branch in `app/web/views_reports.py` | **yes** |
| direct edit, 19 Aug | Drilldown count vs card count (8 vs 2) | same branch — now uses `Agent.uninstall_pending` + their `_windows_source_filter()`, the exact filters `patch_kpis()` uses | **yes** |
| direct edit, 19 Aug | Item 1 — Sophos duplicate endpoint rows | `app/services/sam.py` `product_detail()` keyed by agent; `software_detail.html` drill-down matches a versions list | **yes** |
| direct edit, 19 Aug | Item 4 — Asset Register compliance filter inert | `app/web/views.py` reads compliance from the Entra record for managed endpoints; `assets_list.html` gains a Compliant? column and a "not reported" pick | **yes** |
| direct edit, 19 Aug | Mojibake in topbar icons and sort arrows (self-inflicted, see below) | restored `base.html` / `styles.css` from pre-change backups, re-applied both changes UTF-8-safely | **yes** |
| `Apply-AssetReconcile.ps1` + `asset-reconcile-append.py` (21 Aug, **not yet applied**) | Asset count 110 vs 113 — byte-order sort, unlocated assets invisible to every location filter, untrimmed filter splitting one site in two; adds `/assets/export.csv` | 5 one-line edits + a 2-line sort + an appended block in `app/web/views.py`; 2 inserts in `assets_list.html` | **yes** |

`apply-header-fix.sh` is the Linux equivalent of the header fix, kept for the
droplet and any future Linux host.

Both scripts take a timestamped `.bak` beside the file they edit, are guarded by
a marker so a second run is a no-op, and support `-Rollback`. Backups on the
server as applied:

    app\static\styles.css.bak.20260814-140538
    app\web\views_reports.py.bak.20260814-145103

The stylesheet link in `app/templates/base.html` was also version-bumped to
`?v=202608141419` so browsers refetch the CSS instead of serving the cached copy.

## Upstream equivalents

| Patch | Commit in `main` |
|---|---|
| Header overlap | `79e3b81` |
| Patch Compliance 500 | `83e9b07` |
| Asset count discrepancy | `7c025b9` |

## Asset Register reconciliation (21 Aug) — staged, not yet applied

Raised by Dipesh Panchal: his site shows **110** assets against **113** in his
spreadsheet, with no ordering to identify the three. Three separate causes —
byte-order hostname sorting, assets with no location matching *no* location
filter while still counting in the total, and untrimmed comparison splitting
`"Achhad"` from `"Achhad "`. Adds `GET /assets/export.csv` so the two lists can
be diffed by hostname instead of by total.

**Run `-Capture` first and send the output back before applying.** Live
`views.py` is ~38,935 bytes against 21,635 in this repo, so the anchors should
be checked against the real file. The script names and skips any anchor it
cannot find rather than forcing it, refuses to append if the expected names are
absent, compiles `views.py` afterwards and self-restores if it does not compile.

    .\Apply-AssetReconcile.ps1 -Capture
    .\Apply-AssetReconcile.ps1 -Check
    .\Apply-AssetReconcile.ps1
    Restart-Service OctoAssistServer
    .\Apply-AssetReconcile.ps1 -Rollback    # if needed

Verified end-to-end against a stand-in build reconstructed from the pre-patch
sources: all 9 edits match, patched file compiles and serves, numeric ordering
and the `-- No location set --` pick behave, the export honours live filters,
re-run is a no-op, `-Rollback` restores **byte-identical** files, and no
mojibake or BOM is introduced. Against a deliberately drifted copy the two
stale anchors were named and skipped and the result still compiled.

**Caveat.** Dipesh's screenshot is of `/settings/asset-management`, the Masters
module that does not exist in this repo. This patches the Asset Register
(`/assets`) — the same three faults on the equivalent screen. `-Capture` pulls
`views_settings.py` and the asset templates so the same fixes can be ported to
the screen he is actually looking at.

## These are temporary — read before v6

TEMA are deploying a "v6" build. **A v6 rollout overwrites `styles.css` and every
`.py` file, silently reverting both patches**, and the issues return on a client
who has been told they are fixed. Before v6 ships, confirm it carries these
fixes — ideally by building v6 from `main`, which already has them. Once v6 is
out with the fixes in place, delete these patches rather than reapplying them.

## Two cautions learned the hard way

**Never read these files with `Get-Content`.** Windows PowerShell 5.1 decodes a
BOM-less UTF-8 file using the system ANSI codepage, so emoji and box-drawing
glyphs come back as mojibake and writing them out as UTF-8 makes that
permanent. It corrupted the topbar icons and every sort arrow on 19 Aug. Use
`[IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)` and
`[IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($false)))`.

**Do not drive this server with `powershell -EncodedCommand`.** Bitdefender
Advanced Threat Control blocked it twice on 19 Aug (Heur.BZC.PYV.Boxter) —
base64-wrapped PowerShell is a malware signature. Copy a `.ps1` over with
`scp` and run it with `-File` instead; that runs clean.

## Access

The server is `SRV81`, reachable over Tailscale as **tema-octoflow**
(100.70.236.95) — the node that had been sitting offline in the tailnet was
this box all along. OpenSSH Server is enabled and the `octoassist_deploy` key
is in `C:\ProgramData\ssh\administrators_authorized_keys`. The app runs under
NSSM as `OctoAssistServer`, uvicorn on 127.0.0.1:8091 behind a proxy, logging
to `C:\ProgramData\OctoAssist\Server\logs\{stdout,stderr}.log`.

## The v6 folder is NOT a newer build

`C:\Users\Administrator\Desktop\Octoassist_v6` holds four identical nested
copies of a **May 2026** codebase — `sam.py` 30,074 bytes against 65,933 live,
`views.py` 17,973 against 38,935, `models.py` 58,432 against 86,471. Deploying
it would roll production back roughly two and a half months. No newer tree
exists anywhere on the machine. Settle where v6 actually comes from before any
rollout.

## Still outstanding on that build

- Patch Compliance card reads "2 critical patches outstanding" while the
  (now working) drilldown lists 8 across 3 assets — their `patch_kpis()` counts
  something different again. Needs their source to reconcile.
- Sophos duplicate rows on the product detail page: one host appears once per
  version string (`AU-TIPL-LAP-003` at `2026.2.1.3.0` and `2026.2.1.1`).
  Fixed upstream in `bf11402`.
- Asset Register compliance filter is inert — `?compliant=yes` and
  `?compliant=no` return byte-identical pages. Fixed upstream in `8b3d04a`.
- Sortable column headers render mojibake (`PUBLISHERÂ–¾`), a UTF-8 file being
  read as Latin-1 in their build. Pre-existing, cosmetic, present on every
  sortable table.
