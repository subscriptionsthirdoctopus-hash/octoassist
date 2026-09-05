#!/bin/bash
# Same shape as the octoassist/octocred/octoflow/octoforge hooks. hrms had no
# copy step at all, so nginx kept serving the original Feb-2026 certificate
# after every renewal (found 5 Sep 2026 when the cert had been expired 4 months).
set -e
DOMAIN="hrms.thirdoctopus.com"
NGX_SSL_DIR="/opt/hrms-erp/nginx/ssl"
case "$RENEWED_DOMAINS" in
  *"$DOMAIN"*)
    cp -L "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$NGX_SSL_DIR/fullchain.pem"
    cp -L "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   "$NGX_SSL_DIR/privkey.pem"
    chmod 644 "$NGX_SSL_DIR/fullchain.pem"
    chmod 600 "$NGX_SSL_DIR/privkey.pem"
    docker exec hrms-nginx nginx -s reload || true
    logger -t hrms-cert "renewed + nginx reloaded for $DOMAIN"
    ;;
esac
