#!/usr/bin/env bash
# install-server.sh
# One-shot Ubuntu 22.04 installer for the OctoAssist server.
# Run as root on a fresh VM owned by the client (e.g., TEMA India).
#
#   sudo bash install-server.sh
#
# What this does:
#   1. Installs system packages: postgresql-16, python3.11, nginx
#   2. Creates an 'octoassist' OS user and Postgres role + DB
#   3. Lays down the FastAPI app to /opt/octoassist
#   4. Creates a systemd service 'octoassist' and starts it
#   5. Configures nginx as a reverse proxy on port 80 (HTTPS to be added per-site)
#   6. Prints the bootstrapped tenant enrolment key

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (use sudo)." >&2
  exit 1
fi

# --- Configurable -----------------------------------------------------------
APP_USER="octoassist"
APP_DIR="/opt/octoassist"
APP_PORT="8080"
DB_NAME="octoassist"
DB_USER="octoassist"
ADMIN_USER="admin"

# Tenant defaults (override via env)
TENANT_NAME="${TENANT_NAME:-TEMA India Pvt. Ltd.}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18)}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 18)}"

# --- 1. APT packages --------------------------------------------------------
echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3.11 python3.11-venv python3-pip \
  postgresql postgresql-contrib \
  nginx \
  curl ca-certificates openssl \
  build-essential libpq-dev

# --- 2. App user ------------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "==> Creating app user $APP_USER"
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# --- 3. Postgres role and DB ------------------------------------------------
echo "==> Configuring Postgres"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# --- 4. Lay down app -------------------------------------------------------
echo "==> Deploying app to $APP_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude '__pycache__' --exclude '.env' \
  "$REPO_ROOT/server/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# --- 5. Python venv --------------------------------------------------------
echo "==> Creating Python venv and installing deps"
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
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
cp "$SCRIPT_DIR/octoassist.service" /etc/systemd/system/octoassist.service
systemctl daemon-reload
systemctl enable octoassist
systemctl restart octoassist

# --- 8. nginx --------------------------------------------------------------
echo "==> Installing nginx site"
cp "$SCRIPT_DIR/nginx-octoassist.conf" /etc/nginx/sites-available/octoassist
ln -sf /etc/nginx/sites-available/octoassist /etc/nginx/sites-enabled/octoassist
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

# --- 9. Wait for app to come up & print enrolment key ----------------------
echo "==> Waiting for app to come up"
for i in {1..20}; do
  if curl -sf "http://127.0.0.1:$APP_PORT/health" >/dev/null; then
    break
  fi
  sleep 1
done

ENROL_KEY="$(sudo -u postgres psql -tAc "SELECT enrolment_key FROM tenants ORDER BY id LIMIT 1;" "$DB_NAME" 2>/dev/null || true)"

cat <<EOF

================================================================================
  OctoAssist server is up.

  URL (internal):        http://$(hostname -I | awk '{print $1}')/
  Admin username:        $ADMIN_USER
  Admin password:        $ADMIN_PASSWORD
  Tenant:                $TENANT_NAME
  Tenant enrolment key:  ${ENROL_KEY:-<run again — DB still bootstrapping>}

  Next steps:
    1. Put a real DNS name in front (e.g. octoassist.tema.local).
    2. Add HTTPS with an internal CA cert (recommended) — replace nginx site.
    3. Build the agent MSI on a Windows host (see installer/build-msi.ps1).
    4. Distribute the MSI via GPO with SERVER_URL and the enrolment key above.

  WRITE DOWN THE ADMIN PASSWORD AND ENROLMENT KEY ABOVE — they will not be
  shown again. They are also stored in $ENV_FILE (root-readable only).
================================================================================
EOF
