#!/bin/bash
# Same shape as the other per-site hooks; fund had no copy step, so a renewed
# certificate would never have reached nginx (added 5 Sep 2026).
set -e
DOMAIN="fund.thirdoctopus.com"
NGX_SSL_DIR="/opt/hrms-erp/nginx/ssl/fund"
case "$RENEWED_DOMAINS" in
  *"$DOMAIN"*)
    mkdir -p "$NGX_SSL_DIR"
    cp -L "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$NGX_SSL_DIR/fullchain.pem"
    cp -L "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   "$NGX_SSL_DIR/privkey.pem"
    chmod 644 "$NGX_SSL_DIR/fullchain.pem"
    chmod 600 "$NGX_SSL_DIR/privkey.pem"
    docker exec hrms-nginx nginx -s reload || true
    logger -t fund-cert "renewed + nginx reloaded for $DOMAIN"
    ;;
esac
