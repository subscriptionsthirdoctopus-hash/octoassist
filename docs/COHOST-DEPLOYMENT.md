# Co-host Deployment on `hrms-erp` Droplet

OctoAssist is co-hosted with HRMS on the same DigitalOcean droplet to avoid the cost of a second VM. This doc is the runbook for that specific deployment.

## Target environment

| Item | Value |
|---|---|
| Droplet | `hrms-erp` (BLR1) |
| OS | Ubuntu 24.04 LTS |
| Public IPv4 | `68.183.86.66` |
| Reserved IPv4 | `68.183.246.16` (DNS points here) |
| Existing app | `https://hrms.thirdoctopus.com` (must remain untouched) |
| New subdomain | `https://octoassist.thirdoctopus.com` |
| OctoAssist internal port | `8088` (nginx → 127.0.0.1:8088) |

## What the co-host installer guarantees

✅ Adds a *new* nginx site for `octoassist.thirdoctopus.com`.
✅ Installs Postgres only if missing; otherwise reuses existing.
✅ Binds OctoAssist to `127.0.0.1:8088` — never to a public port.
✅ Issues a Let's Encrypt cert via certbot for the new subdomain.
❌ Does **not** touch the default nginx site or any existing site config.
❌ Does **not** modify UFW rules.
❌ Does **not** restart anything HRMS depends on (only `nginx reload`, which is graceful).

## Pre-deploy checklist

Before running the installer:

- [ ] **DNS A record** for `octoassist.thirdoctopus.com` → `68.183.246.16` is in place and resolves. Test from anywhere: `dig +short octoassist.thirdoctopus.com` should return `68.183.246.16`.
- [ ] **SSH access** to the droplet as a user that can `sudo`. (`ssh root@68.183.246.16` or `ssh <user>@68.183.246.16` then `sudo -i`.)
- [ ] **Code is on the droplet** at e.g. `/root/OctoAssist/` (via `git clone`, `rsync`, or `scp`).
- [ ] **Nothing is bound to TCP/8088** on the droplet: `ss -tlnp | grep :8088` returns nothing.

## Run

```bash
# On the droplet, as root, from the repo root:
cd /root/OctoAssist
sudo bash deploy/linux/install-cohost.sh
```

The script prints the **admin password** and **tenant enrolment key** at the end. Save them; they're also in `/opt/octoassist/.env` (mode 600, root-readable).

To override defaults:

```bash
sudo APP_PORT=8090 \
     DOMAIN=octoassist.thirdoctopus.com \
     LE_EMAIL=info@thirdoctopus.com \
     TENANT_NAME="TEMA India Pvt. Ltd." \
     bash deploy/linux/install-cohost.sh
```

## Verify

```bash
# Service running?
systemctl status octoassist

# App responding internally?
curl -s http://127.0.0.1:8088/health
# → {"status":"ok"}

# nginx routing?
curl -sI http://octoassist.thirdoctopus.com/health
# → 200 OK after certbot redirect chain

# HTTPS up?
curl -sI https://octoassist.thirdoctopus.com/health
# → 200 OK

# HRMS still alive (the critical regression test)?
curl -sI https://hrms.thirdoctopus.com/
# → 200 OK   (whatever HRMS normally returns)
```

## Troubleshooting

**`Port 8088 is already in use`** → another service is on that port. Re-run with `APP_PORT=8090` (or any free port).

**`certbot SKIPPED — DNS does not point at this droplet`** → either the A record isn't in yet, or it's still propagating (TTL up to 1 hour). Add the record, wait, then re-run only the certbot step:
```bash
sudo certbot --nginx -d octoassist.thirdoctopus.com --non-interactive --agree-tos -m info@thirdoctopus.com --redirect
```

**Service won't start** → `journalctl -u octoassist -n 100`. Most common causes: bad DB password in `.env`, port still in use, app code missing.

**HRMS is broken after install** → unlikely (the script doesn't touch HRMS), but if it happens: `nginx -T | less` to inspect full config, and `systemctl restart nginx`. If genuinely broken, `cd /etc/nginx/sites-enabled && rm octoassist && systemctl reload nginx` removes the OctoAssist site cleanly.

## Resource budget

The droplet is 2 vCPU / 4 GB. HRMS reportedly uses ~57% of RAM. OctoAssist server (FastAPI + uvicorn + Postgres) typically runs:

- Python process: 80–150 MB RSS
- Postgres: ~150–200 MB RSS (idle), grows with data
- nginx adds < 10 MB

Total OctoAssist footprint at idle: ~250–400 MB. Should fit in the remaining ~1.5–1.7 GB without swap pressure.

If `free -h` after deployment shows `available` dropping under 500 MB, that's the signal to either resize the droplet (next tier is 2 vCPU / 8 GB at $48/mo) or move OctoAssist to a separate VM.

## Rolling back

```bash
sudo systemctl stop octoassist
sudo systemctl disable octoassist
sudo rm /etc/systemd/system/octoassist.service
sudo systemctl daemon-reload

sudo rm /etc/nginx/sites-enabled/octoassist /etc/nginx/sites-available/octoassist
sudo nginx -t && sudo systemctl reload nginx

sudo rm -rf /opt/octoassist
sudo userdel octoassist
# Drop the DB (optional — destructive)
sudo -u postgres psql -c "DROP DATABASE octoassist;"
sudo -u postgres psql -c "DROP ROLE octoassist;"
```

HRMS is untouched throughout.
