#!/usr/bin/env bash
# Applies the OctoAssist table-header overlap fix to the TEMA India server.
#
# This is a static-asset change only: it appends a CSS block to styles.css.
# No database change, no code change, no service restart, no downtime. It is
# guarded by a marker so running it twice does nothing the second time, and it
# writes a timestamped backup so it can be undone in one command.
#
# Run ON the server (or via ssh):
#     bash apply-header-fix.sh                 # apply
#     bash apply-header-fix.sh --check         # report state, change nothing
#     bash apply-header-fix.sh --rollback      # restore the newest backup
#
# The patch file tema-header-fix.css must sit next to this script.

set -euo pipefail

MARKER="OctoAssist — table header / row overlap fix"
PATCH="$(dirname "$0")/tema-header-fix.css"

# Common install roots; override with OCTO_CSS=/path/to/styles.css
CANDIDATES=(
  "/opt/octoassist/server/app/static/styles.css"
  "/srv/octoassist/server/app/static/styles.css"
  "/var/www/octoassist/server/app/static/styles.css"
  "/home/octoassist/server/app/static/styles.css"
  "/app/server/app/static/styles.css"
)

find_css() {
  if [[ -n "${OCTO_CSS:-}" ]]; then echo "$OCTO_CSS"; return; fi
  for c in "${CANDIDATES[@]}"; do
    [[ -f "$c" ]] && { echo "$c"; return; }
  done
  # Last resort: search, but stay out of /proc and friends.
  find /opt /srv /var/www /home /app -maxdepth 8 -name styles.css -path '*static*' 2>/dev/null | head -1
}

CSS="$(find_css)"
if [[ -z "$CSS" || ! -f "$CSS" ]]; then
  echo "ERROR: could not locate styles.css."
  echo "       Re-run with the path, e.g.  OCTO_CSS=/opt/octoassist/.../styles.css bash $0"
  exit 1
fi

echo "stylesheet : $CSS"
echo "size       : $(wc -c < "$CSS") bytes"

if grep -qF "$MARKER" "$CSS"; then
  APPLIED=yes
else
  APPLIED=no
fi
echo "fix applied: $APPLIED"

case "${1:-}" in
  --check)
    exit 0
    ;;
  --rollback)
    BACKUP="$(ls -1t "${CSS}".bak.* 2>/dev/null | head -1 || true)"
    if [[ -z "$BACKUP" ]]; then echo "ERROR: no backup found next to $CSS"; exit 1; fi
    cp -p "$BACKUP" "$CSS"
    echo "rolled back from $BACKUP"
    exit 0
    ;;
esac

if [[ "$APPLIED" == "yes" ]]; then
  echo "Nothing to do — the fix is already present."
  exit 0
fi

if [[ ! -f "$PATCH" ]]; then
  echo "ERROR: patch file not found at $PATCH"
  exit 1
fi

BACKUP="${CSS}.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$CSS" "$BACKUP"
echo "backup     : $BACKUP"

printf '\n\n' >> "$CSS"
cat "$PATCH" >> "$CSS"

echo "appended   : $(wc -c < "$PATCH") bytes"
echo "new size   : $(wc -c < "$CSS") bytes"
echo
echo "Done. No restart is needed — styles.css is served from disk."
echo "In the browser, hard-refresh (Ctrl/Cmd-Shift-R) to get past the cache."
echo "To undo:  bash $0 --rollback"
