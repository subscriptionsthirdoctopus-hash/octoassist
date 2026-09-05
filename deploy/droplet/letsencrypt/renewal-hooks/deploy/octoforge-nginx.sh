#!/bin/bash
# Runs after every successful Let's Encrypt renewal that includes
# erp1.thirdoctopus.com. Re-copies the new cert into the
# hrms-nginx-readable path and reloads nginx (zero downtime).
set -e
DOMAIN="erp1.thirdoctopus.com"
NGX_SSL_DIR="/opt/hrms-erp/nginx/ssl/octoforge"

case "$RENEWED_DOMAINS" in
  *"$DOMAIN"*)
    cp -L "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$NGX_SSL_DIR/fullchain.pem"
    cp -L "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   "$NGX_SSL_DIR/privkey.pem"
    chmod 644 "$NGX_SSL_DIR/fullchain.pem"
    chmod 600 "$NGX_SSL_DIR/privkey.pem"
    docker exec hrms-nginx nginx -s reload
    logger -t octoforge-cert "renewed + nginx reloaded for $DOMAIN"
    ;;
esac
