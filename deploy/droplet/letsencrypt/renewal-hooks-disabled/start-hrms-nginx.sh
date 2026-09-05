#!/bin/bash
# Always restart hrms-nginx after certbot, even if renewal failed.
docker start hrms-nginx || true
