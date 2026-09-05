#!/bin/bash
set -e

# Copy octoassist certs if they exist
if [ -d "/etc/letsencrypt/live/octoassist.thirdoctopus.com" ]; then
    mkdir -p /opt/hrms-erp/nginx/ssl/octoassist
    cp -L /etc/letsencrypt/live/octoassist.thirdoctopus.com/fullchain.pem /opt/hrms-erp/nginx/ssl/octoassist/fullchain.pem
    cp -L /etc/letsencrypt/live/octoassist.thirdoctopus.com/privkey.pem /opt/hrms-erp/nginx/ssl/octoassist/privkey.pem
    echo "Copied octoassist.thirdoctopus.com certs"
fi

# Copy thirdoctopus certs if they exist
if [ -d "/etc/letsencrypt/live/thirdoctopus.com" ]; then
    mkdir -p /opt/hrms-erp/nginx/ssl/thirdoctopus
    cp -L /etc/letsencrypt/live/thirdoctopus.com/fullchain.pem /opt/hrms-erp/nginx/ssl/thirdoctopus/fullchain.pem
    cp -L /etc/letsencrypt/live/thirdoctopus.com/privkey.pem /opt/hrms-erp/nginx/ssl/thirdoctopus/privkey.pem
    echo "Copied thirdoctopus.com certs"
fi

# Copy octovault certs if they exist
if [ -d "/etc/letsencrypt/live/octovault.thirdoctopus.com" ]; then
    mkdir -p /opt/hrms-erp/nginx/ssl/octovault
    cp -L /etc/letsencrypt/live/octovault.thirdoctopus.com/fullchain.pem /opt/hrms-erp/nginx/ssl/octovault/fullchain.pem
    cp -L /etc/letsencrypt/live/octovault.thirdoctopus.com/privkey.pem /opt/hrms-erp/nginx/ssl/octovault/privkey.pem
    echo "Copied octovault.thirdoctopus.com certs"
fi

# Copy license certs if they exist
if [ -d "/etc/letsencrypt/live/license.thirdoctopus.com" ]; then
    mkdir -p /opt/hrms-erp/nginx/ssl/license
    cp -L /etc/letsencrypt/live/license.thirdoctopus.com/fullchain.pem /opt/hrms-erp/nginx/ssl/license/fullchain.pem
    cp -L /etc/letsencrypt/live/license.thirdoctopus.com/privkey.pem /opt/hrms-erp/nginx/ssl/license/privkey.pem
    echo "Copied license.thirdoctopus.com certs"
fi
