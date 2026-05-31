#!/bin/bash
# One-time cutover script for octoflow.thirdoctopus.com.
# Run ON THE DROPLET as root, AFTER the DNS A record has been created and
# is resolving:  octoflow.thirdoctopus.com → 68.183.86.66
#
# What it does:
#   1. Drops the new nginx config + post-renew hook into place.
#   2. Briefly stops hrms-nginx so certbot --standalone can bind :80.
#   3. Issues the Let's Encrypt cert for octoflow.thirdoctopus.com.
#   4. Copies the cert into the nginx ssl mount.
#   5. Starts hrms-nginx with the new octoflow.conf live.
#   6. Smoke-tests https://octoflow.thirdoctopus.com/health.
set -euo pipefail

DOMAIN="octoflow.thirdoctopus.com"
SSL_DIR="/opt/hrms-erp/nginx/ssl/octoflow"
CONF_DIR="/opt/hrms-erp/nginx/conf.d"
EMAIL="subscriptionsthirdoctopus@gmail.com"

echo "═══ Pre-flight ═══"
echo -n "  DNS resolves? "
HOST_IP=$(curl -sS https://api.ipify.org)
RESOLVED=$(dig +short "$DOMAIN" | tail -1)
if [[ "$RESOLVED" != "$HOST_IP" ]]; then
  echo "❌  $DOMAIN resolves to '$RESOLVED' but droplet is $HOST_IP. Fix DNS first."
  exit 1
fi
echo "✅  $DOMAIN → $HOST_IP"

echo -n "  Conf already exists? "
if [[ -f "$CONF_DIR/octoflow.conf" ]]; then
  echo "⚠️  $CONF_DIR/octoflow.conf already present — backing up to .bak.$(date +%s)"
  cp "$CONF_DIR/octoflow.conf" "$CONF_DIR/octoflow.conf.bak.$(date +%s)"
else
  echo "✅  fresh install"
fi

echo "═══ 1. Drop nginx config + post-renew hook ═══"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/../nginx/octoflow.conf"                "$CONF_DIR/octoflow.conf"
cp "$SCRIPT_DIR/octoflow-nginx.sh"                     "/etc/letsencrypt/renewal-hooks/deploy/octoflow-nginx.sh"
chmod 755                                              "/etc/letsencrypt/renewal-hooks/deploy/octoflow-nginx.sh"
mkdir -p "$SSL_DIR"
echo "  ✅  configs in place"

echo "═══ 2. Stop hrms-nginx briefly so certbot can bind :80 ═══"
docker stop hrms-nginx
echo "  ⏸  nginx stopped"

echo "═══ 3. Issue Let's Encrypt cert ═══"
certbot certonly --standalone \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos --non-interactive \
  --no-eff-email
echo "  🔐  cert issued"

echo "═══ 4. Copy cert into nginx ssl mount ═══"
cp -L "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$SSL_DIR/fullchain.pem"
cp -L "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"
chmod 600 "$SSL_DIR/privkey.pem"
echo "  📂  cert deployed to $SSL_DIR"

echo "═══ 5. Start hrms-nginx with new config ═══"
docker start hrms-nginx
sleep 3
docker exec hrms-nginx nginx -t
echo "  ▶️  nginx running"

echo "═══ 6. Smoke test ═══"
sleep 2
HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" "https://$DOMAIN/health")
echo "  https://$DOMAIN/health -> HTTP $HTTP_CODE"
if [[ "$HTTP_CODE" == "200" ]]; then
  echo ""
  echo "════════════════════════════════════════════════════════════════"
  echo "  ✅  OctoFlow is LIVE at https://$DOMAIN"
  echo "════════════════════════════════════════════════════════════════"
else
  echo "  ❌  unexpected response — check hrms-nginx logs"
  exit 1
fi
