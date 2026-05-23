#!/usr/bin/env bash
# install-linux.sh — install the OctoAssist agent as a systemd service.
#
# Usage:
#   sudo bash install-linux.sh \
#       --server-url=https://octoassist.thirdoctopus.com \
#       --enrolment-key=XXXXXXXX
#
# Idempotent. Writes config to /etc/octoassist/agent.json (mode 600),
# installs the agent script to /usr/local/bin/octoassist-agent, and
# starts a systemd service that runs it as a daemon.

set -euo pipefail

SERVER_URL=""
ENROLMENT_KEY=""
INTERVAL_HOURS=6

for arg in "$@"; do
  case "$arg" in
    --server-url=*)     SERVER_URL="${arg#*=}" ;;
    --enrolment-key=*)  ENROLMENT_KEY="${arg#*=}" ;;
    --interval-hours=*) INTERVAL_HOURS="${arg#*=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "$SERVER_URL" || -z "$ENROLMENT_KEY" ]]; then
  echo "Required: --server-url=URL --enrolment-key=KEY" >&2
  exit 2
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo bash install-linux.sh ...)" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Install with apt-get install -y python3" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing agent binary to /usr/local/bin/octoassist-agent"
install -m 0755 "$SCRIPT_DIR/octoassist_agent.py" /usr/local/bin/octoassist-agent

echo "==> Bootstrapping config (server_url, enrolment_key)"
/usr/local/bin/octoassist-agent --bootstrap \
  --server-url="$SERVER_URL" \
  --enrolment-key="$ENROLMENT_KEY" \
  --interval-hours="$INTERVAL_HOURS"

echo "==> Installing systemd unit"
cat > /etc/systemd/system/octoassist-agent.service <<EOF
[Unit]
Description=OctoAssist asset agent (cross-platform Python)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/octoassist-agent
Restart=always
RestartSec=15
NoNewPrivileges=true
# ProtectSystem=true makes /usr and /boot read-only but allows /etc — the
# agent needs to write its bearer token back to /etc/octoassist/agent.json.
ProtectSystem=true
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/octoassist /var/log/octoassist

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable octoassist-agent
systemctl restart octoassist-agent

sleep 2
systemctl --no-pager --lines=10 status octoassist-agent || true

cat <<EOF

==============================================================
  OctoAssist agent installed and running.

  Config:    /etc/octoassist/agent.json
  Logs:      journalctl -u octoassist-agent -f
             /var/log/octoassist/agent.log
  Status:    systemctl status octoassist-agent
  One-shot:  octoassist-agent --once
  Stop:      systemctl stop octoassist-agent

  The first check-in registers the host with OctoAssist and stores
  a long-lived bearer token. Open the OctoAssist admin UI — this
  host should appear in /assets within a minute.
==============================================================
EOF
