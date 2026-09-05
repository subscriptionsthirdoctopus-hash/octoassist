#!/bin/bash
# Stop hrms-nginx so certbot --standalone can bind port 80.
set -e
docker stop hrms-nginx
