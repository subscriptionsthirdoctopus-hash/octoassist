#!/usr/bin/env bash
# Run the server smoke tests on the droplet against a throwaway COPY of the
# production database, using the image that is currently deployed.
#
#   ssh octoassist 'bash -s' < deploy/droplet/smoke-test.sh
#
# Never points at production: it dumps octoassist into octoassist_smoke,
# runs pytest there (the app's startup migrations run against the copy),
# then drops the copy. Takes about a minute.
set -euo pipefail
SMOKE_DB=octoassist_smoke
PG=octoassist-postgres
NET=octoassist_octoassist-network
TESTS=/opt/octoassist/server/tests
PW=$(grep -E '^POSTGRES_PASSWORD=' /opt/octoassist/deploy/docker/.env | cut -d= -f2-)

echo "== copying octoassist -> $SMOKE_DB"
docker exec $PG psql -U octoassist -d postgres -qc "DROP DATABASE IF EXISTS $SMOKE_DB" >/dev/null
docker exec $PG psql -U octoassist -d postgres -qc "CREATE DATABASE $SMOKE_DB" >/dev/null
docker exec $PG sh -c "pg_dump -U octoassist -d octoassist | psql -q -U octoassist -d $SMOKE_DB" >/dev/null 2>&1
echo "   $(docker exec $PG psql -U octoassist -d $SMOKE_DB -tAc 'select count(*) from users') users in the copy"

echo "== running pytest in a throwaway container from octoassist-server:latest"
set +e
docker run --rm --network $NET \
  -e OCTOASSIST_DATABASE_URL="postgresql+psycopg://octoassist:${PW}@${PG}:5432/${SMOKE_DB}" \
  -e OCTOASSIST_TENANT_NAME="Third Octopus" \
  -v "$TESTS:/srv/octoassist/tests:ro" \
  -w /srv/octoassist --user root \
  octoassist-server:latest \
  sh -c "pip install -q pytest >/dev/null 2>&1 && python -m pytest tests -q --no-header -p no:cacheprovider 2>&1 | tail -25"
rc=$?
set -e

echo "== dropping $SMOKE_DB"
docker exec $PG psql -U octoassist -d postgres -qc "DROP DATABASE IF EXISTS $SMOKE_DB" >/dev/null
exit $rc
