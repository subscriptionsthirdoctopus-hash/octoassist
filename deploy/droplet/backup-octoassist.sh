#!/usr/bin/env bash
# Nightly logical backup of the OctoAssist Postgres database on the droplet.
# Installed at /opt/octoassist/bin/backup-octoassist.sh, run by root's crontab
# at 21:15 UTC (02:45 IST). Keeps 14 days locally; copy off-box separately.
set -euo pipefail
DIR=/opt/octoassist/backups
KEEP_DAYS=14
mkdir -p "$DIR"
f="$DIR/octoassist-$(date +%Y%m%d-%H%M%S).sql.gz"
docker exec octoassist-postgres pg_dump -U octoassist -d octoassist | gzip -6 > "$f"
gzip -t "$f"
size=$(stat -c %s "$f")
[ "$size" -gt 100000 ] || { echo "backup suspiciously small: $size bytes" >&2; exit 1; }
find "$DIR" -name 'octoassist-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) ok $f ($size bytes); $(ls "$DIR"/octoassist-*.sql.gz | wc -l) kept"
