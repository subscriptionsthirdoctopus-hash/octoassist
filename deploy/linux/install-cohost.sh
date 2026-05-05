#!/usr/bin/env bash
# install-cohost.sh
# Co-host installer: deploys the OctoAssist server onto an Ubuntu droplet that
# is ALREADY running another app (e.g. hrms.thirdoctopus.com). Designed to
# be safe to run on the existing hrms-erp droplet without breaking HRMS.
#
# What it does:
#   1. Verifies Ubuntu 22.04 or 24.04 and existing nginx + certbot
#   2. Installs PostgreSQL (only if missing — uses existing if found)
#   3. Lays down the FastAPI app to /opt/octoassist
#   4. Creates Postgres role + DB, writes /opt/octoassist/.env
#   5. Creates a systemd service 'octoassist' bound to 127.0.0.1:<APP_PORT>
#   6. Adds an nginx site for octoassist.thirdoctopus.com → 127.0.0.1:<APP_PORT>
#   7. Issues a Let's Encrypt cert via certbot (HTTPS)
#   8. Prints the admin password + tenant enrolment key
#
# What it deliberately does NOT do:
#   - Touch the default nginx site
#   - Touch any other existing site config (HRMS is left alone)
#   - Modify UFW rules (assumes 80/443 already allowed for HRMS)
#   - Install or restart anything that HRMS depends on
#
# Run as root on the droplet.
#
#   curl -fsSL <repo-url>/raw/main/deploy/linux/install-cohost.sh | sudo bash
# or:
#   sudo bash deploy/linux/install-cohost.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (use sudo)." >&2; exit 1
fi

# --- Configurable (override via env) ---------------------------------------
APP_USER="${APP_USER:-octoassist}"
APP_DIR="${APP_DIR:-/opt/octoassist}"
APP_PORT="${APP_PORT:-8088}"           # internal port, must NOT clash with HRMS
DB_NAME="${DB_NAME:-octoassist}"
DB_USER="${DB_USER:-octoassist}"
ADMIN_USER="${ADMIN_USER:-admin}"
DOMAIN="${DOMAIN:-octoassist.thirdoctopus.com}"
LE_EMAIL="${LE_EMAIL:-info@thirdoctopus.com}"
TENANT_NAME="${TENANT_NAME:-TEMA India Pvt. Ltd.}"

# Auto-generated credentials (override only if you know what you're doing).
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18)}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 18)}"

# --- 1. Pre-flight ---------------------------------------------------------
. /etc/os-release
case "${VERSION_ID}" in
  22.04|24.04) ;;
  *) echo "Unsupported Ubuntu: ${VERSION_ID}. Targets 22.04 / 24.04." >&2; exit 1 ;;
esac
echo "==> Ubuntu ${VERSION_ID} detected"

if ! command -v nginx >/dev/null 2>&1; then
  echo "ERROR: nginx is not installed on this droplet." >&2
  echo "The co-host script assumes nginx is already running for HRMS." >&2
  echo "Use install-server.sh for a clean droplet, or install nginx first." >&2
  exit 1
fi
echo "==> nginx present: $(nginx -v 2>&1)"

if ! command -v certbot >/dev/null 2>&1; then
  echo "==> certbot missing — installing"
  apt-get update -y
  apt-get install -y --no-install-recommends certbot python3-certbot-nginx
fi

# Port collision check
if ss -tlnp 2>/dev/null | grep -q ":${APP_PORT}\b"; then
  echo "ERROR: port ${APP_PORT} is already in use on this droplet." >&2
  echo "       Set APP_PORT=<other_port> in env and rerun." >&2
  ss -tlnp | grep ":${APP_PORT}\b" >&2 || true
  exit 1
fi
echo "==> Port ${APP_PORT} is free"

# --- 2. Install missing packages -------------------------------------------
echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  build-essential libpq-dev \
  curl ca-certificates openssl \
  rsync

# Postgres: install only if missing (avoid touching an HRMS Postgres if present)
if ! command -v psql >/dev/null 2>&1; then
  echo "==> PostgreSQL missing — installing"
  apt-get install -y --no-install-recommends postgresql postgresql-contrib
  systemctl enable --now postgresql
else
  echo "==> PostgreSQL already installed: $(psql --version)"
fi

# --- 3. App user -----------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "==> Creating app user $APP_USER"
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# --- 4. Postgres role + DB -------------------------------------------------
echo "==> Configuring Postgres"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER PASSWORD '$DB_PASSWORD';" >/dev/null
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# --- 5. App code -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "==> Deploying app to $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude '__pycache__' --exclude '.venv' --exclude '.env' \
  "$REPO_ROOT/server/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Creating venv and installing app"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

# --- 6. .env ---------------------------------------------------------------
ENV_FILE="$APP_DIR/.env"
echo "==> Writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
OCTOASSIST_DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME
OCTOASSIST_ADMIN_USERNAME=$ADMIN_USER
OCTOASSIST_ADMIN_PASSWORD=$ADMIN_PASSWORD
OCTOASSIST_BIND_HOST=127.0.0.1
OCTOASSIST_BIND_PORT=$APP_PORT
OCTOASSIST_TENANT_NAME=$TENANT_NAME
EOF
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# --- 7. systemd unit -------------------------------------------------------
echo "==> Installing systemd unit"
cat > /etc/systemd/system/octoassist.service <<EOF
[Unit]
Description=OctoAssist ITSM server (co-hosted on hrms-erp)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $APP_PORT
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable octoassist
systemctl restart octoassist

# --- 8. nginx site (additive — does NOT touch existing sites) --------------
echo "==> Adding nginx site for $DOMAIN"

# Generate the site config from the template, substituting port and domain.
NGINX_TEMPLATE="$SCRIPT_DIR/nginx-octoassist-cohost.conf"
NGINX_SITE="/etc/nginx/sites-available/octoassist"

if [ ! -f "$NGINX_TEMPLATE" ]; then
  echo "ERROR: nginx template missing: $NGINX_TEMPLATE" >&2; exit 1
fi

sed -e "s|octoassist.thirdoctopus.com|$DOMAIN|g" \
    -e "s|127.0.0.1:8088|127.0.0.1:$APP_PORT|g" \
    "$NGINX_TEMPLATE" > "$NGINX_SITE"

ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/octoassist

if ! nginx -t; then
  echo "ERROR: nginx config test failed; not reloading. Investigate above." >&2
  exit 1
fi
systemctl reload nginx

# --- 9. Wait for app, then certbot ------------------------------------------
echo "==> Waiting for app on 127.0.0.1:$APP_PORT/health"
for i in {1..30}; do
  if curl -sf "http://127.0.0.1:$APP_PORT/health" >/dev/null; then break; fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:$APP_PORT/health" >/dev/null; then
  echo "WARNING: app did not respond. Check: journalctl -u octoassist -n 50" >&2
fi

# DNS sanity check before calling certbot, so we fail fast with a clear message
RESOLVED_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
DROPLET_IP="$(curl -fsS https://api.ipify.org 2>/dev/null || true)"
if [ -n "$RESOLVED_IP" ] && [ -n "$DROPLET_IP" ] && [ "$RESOLVED_IP" != "$DROPLET_IP" ]; then
  cat >&2 <<EOF
==> SKIPPING certbot — DNS for $DOMAIN does not point at this droplet.
    $DOMAIN  →  $RESOLVED_IP
    droplet  →  $DROPLET_IP
    Add an A record for $DOMAIN → $DROPLET_IP, wait for propagation, then run:
      certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $LE_EMAIL --redirect
EOF
elif [ -z "$RESOLVED_IP" ]; then
  cat >&2 <<EOF
==> SKIPPING certbot — $DOMAIN does not resolve yet.
    Add the A record and wait for propagation, then run:
      certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m $LE_EMAIL --redirect
EOF
else
  echo "==> Issuing Let's Encrypt cert for $DOMAIN"
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect || \
    echo "WARNING: certbot did not succeed. Re-run manually after DNS propagation." >&2
fi

# --- 10. Print credentials --------------------------------------------------
ENROL_KEY="$(sudo -u postgres psql -tAc "SELECT enrolment_key FROM tenants ORDER BY id LIMIT 1;" "$DB_NAME" 2>/dev/null | tr -d '[:space:]' || true)"

cat <<EOF

================================================================================
  OctoAssist co-hosted on hrms-erp — install complete.

  Subdomain:             https://$DOMAIN
                         (HTTPS active once DNS propagates and certbot runs)
  App bind:              127.0.0.1:$APP_PORT
  Admin username:        $ADMIN_USER
  Admin password:        $ADMIN_PASSWORD
  Tenant:                $TENANT_NAME
  Tenant enrolment key:  ${ENROL_KEY:-<DB still bootstrapping — re-check>}

  Service:               systemctl status octoassist
  Logs:                  journalctl -u octoassist -f
  Config (root only):    $ENV_FILE

  HRMS is untouched — no existing nginx site was modified.

  WRITE DOWN THE ADMIN PASSWORD AND ENROLMENT KEY ABOVE — they will not be
  shown again.
================================================================================
EOF
