#!/usr/bin/env bash
# One-time, interactive: connect the droplet to DigitalOcean Spaces for
# off-box backups. Run it ON the droplet with a terminal, so the secret is
# typed straight into the box and never travels through a chat or a file:
#
#   ssh -t octoassist 'bash /opt/octoassist/deploy/droplet/setup-spaces.sh'
#
# Before running, mint a key pair in the DigitalOcean control panel:
#   API -> Spaces Keys -> Generate New Key  (name it e.g. "droplet-backups")
# You get an Access Key ID and a Secret; the secret is shown once.
#
# What this does: writes /root/.config/rclone/rclone.conf (mode 600), creates
# the bucket if it does not exist, uploads a probe object and reads it back,
# runs the first real sync, and adds the nightly cron line after the local
# backup. Re-running is safe; it overwrites the same config.
set -euo pipefail
command -v rclone >/dev/null || { echo "rclone is not installed (apt-get install -y rclone)"; exit 1; }

REGION_DEFAULT=$(curl -s --max-time 3 http://169.254.169.254/metadata/v1/region || echo blr1)
read -r -p "Spaces region [${REGION_DEFAULT}]: " REGION; REGION=${REGION:-$REGION_DEFAULT}
read -r -p "Bucket name [thirdoctopus-backups]: " BUCKET; BUCKET=${BUCKET:-thirdoctopus-backups}
read -r -p "Spaces Access Key ID: " KEY_ID
read -r -s -p "Spaces Secret (not echoed): " SECRET; echo
[[ -n "$KEY_ID" && -n "$SECRET" ]] || { echo "key id and secret are required"; exit 1; }

mkdir -p /root/.config/rclone
umask 077
cat > /root/.config/rclone/rclone.conf <<EOF
[spaces]
type = s3
provider = DigitalOcean
access_key_id = ${KEY_ID}
secret_access_key = ${SECRET}
endpoint = ${REGION}.digitaloceanspaces.com
acl = private
EOF
chmod 600 /root/.config/rclone/rclone.conf
unset SECRET
echo "$BUCKET" > /etc/octoassist-spaces-bucket
echo "== config written (root-only)"

echo "== bucket"
rclone mkdir "spaces:${BUCKET}" && echo "   spaces:${BUCKET} ready (${REGION})"

echo "== probe upload + read-back"
probe="probe-$(date +%s).txt"; echo "octoassist offsite probe" > "/tmp/$probe"
rclone copyto "/tmp/$probe" "spaces:${BUCKET}/octoassist/${probe}"
rclone cat "spaces:${BUCKET}/octoassist/${probe}" | grep -q "offsite probe" && echo "   read-back OK"
rclone deletefile "spaces:${BUCKET}/octoassist/${probe}"; rm -f "/tmp/$probe"

echo "== first sync of existing dumps"
install -m 750 /opt/octoassist/deploy/droplet/backup-offsite.sh /opt/octoassist/bin/backup-offsite.sh
/opt/octoassist/bin/backup-offsite.sh | sed 's/^/   /'

echo "== nightly cron (after the 21:15 UTC local backup)"
( crontab -l 2>/dev/null | grep -v backup-offsite; echo "25 21 * * * /opt/octoassist/bin/backup-offsite.sh >> /opt/octoassist/backups/offsite.log 2>&1" ) | crontab -
crontab -l | grep -E "backup-(octoassist|offsite)" | sed 's/^/   /'
echo "== done. Verify any time with: rclone lsf spaces:${BUCKET}/octoassist"
