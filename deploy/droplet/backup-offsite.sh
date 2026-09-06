#!/usr/bin/env bash
# Copy the nightly OctoAssist dumps to DigitalOcean Spaces and prune old
# copies there. Runs from root's crontab right after backup-octoassist.sh.
# Credentials live only in /root/.config/rclone/rclone.conf (mode 600),
# written by setup-spaces.sh — never in this repo.
set -euo pipefail
REMOTE=${SPACES_REMOTE:-spaces}
BUCKET=${SPACES_BUCKET:-$(cat /etc/octoassist-spaces-bucket 2>/dev/null || echo thirdoctopus-backups)}
SRC=/opt/octoassist/backups
DEST="$REMOTE:$BUCKET/octoassist"
KEEP_DAYS=30

# Only the dumps, never the log; --immutable refuses to overwrite an existing
# object, so a corrupted local file can never clobber a good remote copy.
rclone copy "$SRC" "$DEST" --include 'octoassist-*.sql.gz' --immutable --quiet
rclone delete "$DEST" --min-age "${KEEP_DAYS}d" --include 'octoassist-*.sql.gz' --quiet || true
n=$(rclone lsf "$DEST" --include 'octoassist-*.sql.gz' | wc -l)
newest=$(rclone lsf "$DEST" --include 'octoassist-*.sql.gz' | sort | tail -1)
echo "$(date -u +%FT%TZ) offsite ok: $n dumps in $DEST, newest $newest"
