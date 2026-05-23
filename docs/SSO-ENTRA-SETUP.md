# SSO with Microsoft Entra ID — Setup Guide

This is the one-time setup an OctoAssist administrator does in the Azure / Entra ID portal **before** filling in the OctoAssist Settings → Identity Providers form.

> **HTTPS required.** Entra ID rejects redirect URIs over plain HTTP except for `http://localhost`. OctoAssist must be served over HTTPS for sign-in to actually work. The Settings page lets you save Entra config either way, but real sign-ins only succeed on HTTPS.

---

## Step 1 — Register the application in Entra

1. Open https://entra.microsoft.com → **Identity → Applications → App registrations** → **+ New registration**.
2. Name: **OctoAssist** (or whatever you want users to see on the Microsoft consent prompt).
3. Supported account types: **Accounts in this organizational directory only** (single tenant) — unless you genuinely want multi-tenant.
4. Redirect URI:
   - Platform: **Web**
   - URL: paste the **Redirect URI** shown on the OctoAssist Settings → Identity Providers → Microsoft Entra ID page. It will look like:
     ```
     https://octoassist.thirdoctopus.com/auth/oidc/1/callback
     ```
5. Click **Register**.
6. On the resulting Overview page, copy:
   - **Application (client) ID** → goes into OctoAssist as **Application (client) ID**
   - **Directory (tenant) ID** → goes into OctoAssist as **Entra Tenant ID**

---

## Step 2 — Create a client secret

1. In the same App registration → **Certificates & secrets → Client secrets → + New client secret**.
2. Description: `OctoAssist OIDC` ; Expires: **24 months** (or shorter if you prefer).
3. Click **Add**.
4. **Copy the `Value` field immediately** — it is shown exactly once. This is the **Client secret** to paste into OctoAssist.
5. Note the expiry on a calendar — you'll need to rotate it before that date.

---

## Step 3 — API permissions (usually default is enough)

OctoAssist asks for the standard OIDC scopes: `openid`, `profile`, `email`. These are the **Microsoft Graph delegated** permissions.

In **API permissions**:
- Confirm `User.Read` is present (it is, by default, on a fresh App registration).
- No admin consent needed for these scopes — users consent on first sign-in. If your tenant requires admin consent for new apps, click **Grant admin consent for <tenant>**.

---

## Step 4 — (Optional) Token configuration to surface email

If your sign-ins land in OctoAssist with no email, the `email` claim may not be in the ID token by default. To force it:

1. **Token configuration → + Add optional claim → ID** → tick **email** → **Add**.
2. If asked, also tick **Turn on the Microsoft Graph email permission**.

This is only an issue for some tenant configurations. OctoAssist falls back to `preferred_username` if `email` is missing, which usually contains a UPN like `arun@thirdoctopus.com` and is good enough.

---

## Step 5 — Fill in OctoAssist

In OctoAssist as admin:

1. Go to **Settings → Microsoft Entra ID** (or click **+ Add Entra ID** if not yet created).
2. Paste:
   - **Entra Tenant ID** = the *Directory (tenant) ID* from Step 1.
   - **Application (client) ID** = from Step 1.
   - **Client secret** = the *Value* from Step 2.
3. **Allowed email domains** (optional): comma-separated list, e.g. `thirdoctopus.com, tema.in`. Blank = allow any domain.
4. **Auto-provision** + **Default role**: with auto-provision on, any successful Microsoft sign-in for an unknown email creates an OctoAssist user with the default role you choose. Default role of `requester` is the safe pick — admins can promote a user to `agent` or `admin` in `/users` afterwards.
5. Tick **Enable**.
6. Click **Save**, then **Test connection**. The test fetches Microsoft's OIDC discovery document — if your Tenant ID is wrong it errors here, before any user gets a confusing redirect.

---

## Step 6 — Try it

1. Open `/login` in an incognito/private window (so you don't carry the existing admin session).
2. You should see a **Sign in with Microsoft Entra ID** button above the password form.
3. Click it. Microsoft challenges you, you consent on first run, and OctoAssist signs you in.
4. Check `/users` as an admin — you should see the SSO-provisioned account with the default role.

---

## Rotating the secret

When the secret approaches expiry:

1. **Certificates & secrets → + New client secret** → copy the new Value.
2. OctoAssist **Settings → Microsoft Entra ID** → paste the new secret into **Client secret** (leave blank if you want to keep the current one) → **Save**.
3. Optionally delete the old secret in Entra after you've confirmed the new one works (keep both active for a few minutes during cutover).

## Disabling SSO temporarily

In OctoAssist **Settings → Microsoft Entra ID** → uncheck **Enable** → Save. The SSO button disappears from `/login`. Existing SSO-provisioned users can still sign in with their local password (which is random and unusable, in practice — admin must reset it via **Users**).

## On-prem Active Directory (LDAP) — note

This guide is **only for Entra ID (cloud Microsoft 365)**. Authenticating against an on-prem AD via LDAP/LDAPS requires the OctoAssist server to have network access to the domain controllers (typically over a site-to-site VPN). That path is on the roadmap but not yet built. If TEMA needs on-prem AD specifically, raise it as a phase 4+ request.
