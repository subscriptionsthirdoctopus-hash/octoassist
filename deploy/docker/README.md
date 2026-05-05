# OctoAssist — Docker Deployment

Used on the **hrms-erp** droplet to co-host OctoAssist alongside HRMS without touching HRMS. Both apps run as separate Docker Compose stacks.

## Layout on the droplet

```
/opt/hrms-erp/         ← existing HRMS stack (untouched)
  └── docker-compose.yml etc.

/opt/octoassist/       ← this repo, rsynced from dev machine
  ├── server/          ← FastAPI app + Dockerfile
  ├── deploy/docker/
  │   ├── docker-compose.yml
  │   ├── install.sh
  │   └── .env         ← generated on first install (mode 600)
  └── ...

Docker:
  containers: octoassist-app, octoassist-postgres
  network:    octoassist_octoassist-network
  volumes:    octoassist_postgres_data
  ports:      127.0.0.1:8088 → app:8080   (loopback only — not public)
```

## Bring up

```bash
cd /opt/octoassist/deploy/docker
sudo bash install.sh
```

First run generates random admin and DB passwords into `.env` and prints them. Subsequent runs reuse the same `.env`.

## Operate

```bash
cd /opt/octoassist/deploy/docker

docker compose ps                     # status
docker compose logs -f app            # follow app logs
docker compose restart app            # restart just the app
docker compose pull && docker compose up -d   # update (after rebuild)
docker compose build && docker compose up -d  # rebuild after code change
docker compose down                   # stop both, keep DB volume
docker compose down -v                # stop both, DESTROY DB volume
```

## Access from your laptop (no public exposure yet)

```bash
# On your Mac
ssh -i ~/.ssh/octoassist_deploy -L 8088:127.0.0.1:8088 root@68.183.246.16
# leave the SSH session open
```

Open `http://localhost:8088/` in a browser. Log in with the admin/password printed by `install.sh`.

## Going public later (when you want the subdomain)

When you're ready to expose this at `https://octoassist.thirdoctopus.com`, the path is:

1. Add DNS A record for `octoassist.thirdoctopus.com → 68.183.246.16`.
2. Edit the **HRMS** nginx container's config (in `/opt/hrms-erp/nginx/`) to add a new server block:
   ```nginx
   server {
       listen 443 ssl http2;
       server_name octoassist.thirdoctopus.com;
       # ... ssl_certificate paths from certbot ...
       location / {
           proxy_pass http://host.docker.internal:8088;  # or use host gateway IP
           # proxy headers...
       }
   }
   ```
3. Reload that nginx container.
4. Issue cert via certbot from the host (HRMS already uses certbot, same flow).

This is one PR's worth of work but isn't done in this build per "forget the subdomain for now."

## Resource usage

Steady-state on the droplet:
- `octoassist-app`     ~80–120 MB RSS
- `octoassist-postgres` ~80–100 MB RSS

Total: ~200 MB. Bill stays at $24/mo.

## Tearing down

```bash
cd /opt/octoassist/deploy/docker
docker compose down -v          # stop and drop DB volume
cd /opt && rm -rf octoassist    # drop code (only after down)
```

HRMS is unaffected at every step.
