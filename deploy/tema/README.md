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

## What is applied (as of 14 Aug 2026)

| Script | Fixes | Touches | Restart |
|---|---|---|---|
| `Apply-HeaderFix.ps1` + `tema-header-fix.css` | Table header / row overlap | appends to `app/static/styles.css` | no |
| `Patch-Item6.ps1` | Patch Compliance drilldown HTTP 500 | replaces one `elif` branch in `app/web/views_reports.py` | **yes** |

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

## These are temporary — read before v6

TEMA are deploying a "v6" build. **A v6 rollout overwrites `styles.css` and every
`.py` file, silently reverting both patches**, and the issues return on a client
who has been told they are fixed. Before v6 ships, confirm it carries these
fixes — ideally by building v6 from `main`, which already has them. Once v6 is
out with the fixes in place, delete these patches rather than reapplying them.

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
