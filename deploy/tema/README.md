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
| `Apply-AssetReconcile.ps1` + `asset-reconcile-append.py` (21 Aug, **staged, NOT applied**) | `/assets` only: byte-order sort, unlocated assets invisible to every location filter, location filter ignoring AD aliases; adds `/assets/export.csv`. **Does not fix Dipesh's ticket** — that is the `am_assets` Masters module | 4 one-line edits + a 3-line sort + an appended block in `app/web/views.py`; 2 inserts in `assets_list.html` | **yes** |

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

## Asset Register reconciliation (21 Aug) — staged, NOT applied

Prepared for Dipesh Panchal's report (110 assets at Achhad in OctoAssist vs 113
in his spreadsheet). `-Capture` was run against production on 21 Aug and the
kit was then rewritten against the captured sources. **It has not been applied**
— production still has no `OCTO-ASSETREC` marker.

### What the capture proved

**The ticket is about a different screen than this patch fixes.** Dipesh's
"Assets Management" tab is the Masters module, backed by its own **`am_assets`**
table with a normalised `location_id` FK — not the free-text `Agent.location` /
`EntraDevice.location` the Asset Register reads. This patch fixes `/assets`.

**Where his 110 comes from.** `am_assets` for Achhad is exactly
106 `in_use` + 3 `stock` + 1 `retired` = **110**.

**Where the gap is.** 13 `am_assets` rows have `location_id IS NULL`, so they
appear in the total but under no location. Resolving each row's site from its
linked agent/device puts exactly **one** at Achhad —
`PG047VPT` / `AU-TIPL-LAP-027` — so that is one of his three. The other two need
his 113-row sheet to name; they are most likely rows never entered into
`am_assets` at all.

**A hypothesis the data killed.** The `Acchad`/`Achhad` misspelling does *not*
occur in asset locations — every stored value is already a clean AD office
(`exact = alias-aware = 226`). That typo is confined to the routing rules.

### What this build already does better than the repo

`services/offices.py` resolves the Location dropdown from Active Directory
(`ad_offices`), de-duplicates case-insensitively and applies `OFFICE_ALIASES`
(including `acchad -> Achhad`). The generic dropdown-folding edit was therefore
dropped. But `canonical_office()` is **never called by the row filter**, which
still compares the raw stored string — so the patch routes both sides of the
location comparison through this build's own service rather than inventing a
second rule.

### Edits, rewritten against the captured sources

| Edit | Note |
|---|---|
| resolve the AD office list once per request | inserted after `needle = …`; `ad_offices()` is a query and the register renders hundreds of rows |
| location filter matches on canonical AD office | 2 sites |
| department filter honours trim and case | 2 sites |
| department dropdown folds spelling variants | location dropdown deliberately left alone |
| numeric sort of `rows`, `discovered_rows`, `manual_rows` | this build has **three** lists |
| append helpers + `GET /assets/export.csv` | exports all three lists, with `location_raw` **and** `location_office` |

Two traps found while rewriting, both now avoided:

- The original edit stripped `.order_by(Agent.hostname)`, which occurs **twice**
  — the second is in `assets_dashboard()` and would have been left unsorted.
  The ORDER BY is now left in place entirely; sorting again in Python is
  redundant, not wrong.
- `discovered_rows` is appended via `(manual_rows if is_manual else
  discovered_rows).append({`, so a guard looking for `discovered_rows.append(`
  silently skipped the sort.

### Run order

    .\Apply-AssetReconcile.ps1 -Capture
    .\Apply-AssetReconcile.ps1 -Check
    .\Apply-AssetReconcile.ps1
    Restart-Service OctoAssistServer
    .\Apply-AssetReconcile.ps1 -Rollback    # if needed

Verified: all 8 edits match the captured `views.py`; the patched file compiles;
the helpers were unit-tested against this build's real `offices.py`
(`Acchad -> Achhad` folds, `Panoli`/`Dahej` do not leak, unknown offices are
left alone, blanks reachable only via the sentinel); `-Rollback` restores
byte-identical files; the script is pure ASCII and writes UTF-8 without a BOM.

**Before applying, decide whether it is worth it** — it improves `/assets` but
does **not** address Dipesh's ticket. The ticket needs the Masters module: a
"no location" view there, plus assigning `location_id` to those 13 rows.

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
