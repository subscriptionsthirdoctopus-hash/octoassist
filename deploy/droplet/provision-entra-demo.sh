#!/usr/bin/env bash
# Creates an app registration in the Third Octopus Entra tenant for the
# octoassist.thirdoctopus.com demo and repoints identity_providers row 2 at it.
# The client secret is piped az -> ssh -> psql and is never printed.
set -euo pipefail

TENANT="7ede5c19-a4f8-4620-9c7c-279a2f109c20"
IDP_ID=2
HOST="https://octoassist.thirdoctopus.com"
REDIRECT="$HOST/auth/oidc/$IDP_ID/callback"
NAME="OctoAssist Demo (octoassist.thirdoctopus.com)"
ALLOWED="thirdoctopus.com"
GRAPH="00000003-0000-0000-c000-000000000000"
USER_READ="e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope"

echo "== signed in as: $(az account show --query user.name -o tsv) tenant=$(az account show --query tenantId -o tsv)"
[ "$(az account show --query tenantId -o tsv)" = "$TENANT" ] || { echo "wrong tenant"; exit 1; }

# Reuse an existing registration of the same name if a previous run made one.
APP_ID=$(az ad app list --display-name "$NAME" --query '[0].appId' -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$NAME" \
      --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "$REDIRECT" \
      --query appId -o tsv)
  echo "== created app registration appId=$APP_ID"
else
  echo "== reusing app registration appId=$APP_ID"
  az ad app update --id "$APP_ID" --web-redirect-uris "$REDIRECT"
fi

# Service principal so the app is sign-in-able in this tenant.
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" --query id -o tsv >/dev/null
echo "== service principal present"

# Delegated User.Read (covers openid/profile/email sign-in) + admin consent so
# nobody sees a consent prompt. Consent may fail if the account is not a
# privileged admin; that only means users get a one-time consent screen.
az ad app permission add --id "$APP_ID" --api "$GRAPH" --api-permissions "$USER_READ" >/dev/null 2>&1 || true
sleep 5
if az ad app permission admin-consent --id "$APP_ID" >/dev/null 2>&1; then
  echo "== admin consent granted"
else
  echo "== admin consent NOT granted (users will see a one-time consent prompt)"
fi

# New secret, 2 years. Captured into a variable only.
SECRET=$(az ad app credential reset --id "$APP_ID" --display-name "octoassist-demo-$(date +%Y%m%d)" --years 2 --query password -o tsv)
[ -n "$SECRET" ] || { echo "no secret returned"; exit 1; }
case "$SECRET" in *'$q$'*) echo "secret contains dollar-quote tag; abort"; exit 1;; esac
echo "== secret issued (length ${#SECRET}), expires $(date -v+2y +%Y-%m-%d)"

# Repoint the IdP row. Dollar-quoted so no escaping issues; sent over stdin.
SQL="UPDATE identity_providers
     SET config = config || jsonb_build_object(
           'entra_tenant_id', \$q\$$TENANT\$q\$,
           'client_id',       \$q\$$APP_ID\$q\$,
           'client_secret',   \$q\$$SECRET\$q\$,
           'allowed_email_domains', \$q\$$ALLOWED\$q\$),
         last_test_at = NULL, last_test_ok = NULL, last_test_message = NULL,
         updated_at = now()
     WHERE id = $IDP_ID;
     SELECT id, config->>'entra_tenant_id' AS tenant, config->>'client_id' AS client_id,
            length(config->>'client_secret') AS secret_len, config->>'allowed_email_domains' AS domains
     FROM identity_providers WHERE id = $IDP_ID;"
printf '%s\n' "$SQL" | ssh -o ConnectTimeout=10 -o BatchMode=yes octoassist \
  "docker exec -i octoassist-postgres psql -U octoassist -d octoassist -v ON_ERROR_STOP=1"
unset SECRET SQL
echo "== IdP row $IDP_ID repointed"
