# Third Octopus droplet (68.183.86.66) — server-side configuration

Snapshot of the configuration that lives only on the droplet, captured
5 Sep 2026 so it is versioned somewhere. The droplet is the source of truth;
re-capture after editing there (`scp`/`tar` — see paths below).

| Repo path | Droplet path | Notes |
|---|---|---|
| `nginx/nginx.conf` | `/opt/hrms-erp/nginx/nginx.conf` | mounted ro into `hrms-nginx` |
| `nginx/conf.d/*.conf` | `/opt/hrms-erp/nginx/conf.d/` | one vhost per site; `hrms-nginx` fronts every site |
| `letsencrypt/renewal/*.conf` | `/etc/letsencrypt/renewal/` | certbot renewal configs (account id only, no keys) |
| `letsencrypt/renewal-hooks/deploy/*.sh` | `/etc/letsencrypt/renewal-hooks/deploy/` | per-site: copy renewed cert into `/opt/hrms-erp/nginx/ssl/<site>/`, `nginx -s reload` |
| `letsencrypt/renewal-hooks/post/copy-certs.sh` | `.../renewal-hooks/post/` | legacy bulk copy for octoassist/thirdoctopus/octovault/license |
| `letsencrypt/renewal-hooks-disabled/` | `/etc/letsencrypt/renewal-hooks-disabled/` | the old standalone-mode stop/start-nginx hooks — kept for history, must stay disabled |
| `nginx/conf.d/security-headers.inc` | `/opt/hrms-erp/nginx/conf.d/` | included inside every HTTPS server block (nginx drops http-level `add_header` once a vhost sets its own) |
| `backup-octoassist.sh` | `/opt/octoassist/bin/`, root cron `15 21 * * *` UTC | nightly `pg_dump` to `/opt/octoassist/backups`, 14-day rotation, log in `backup.log` |
| `52-unattended-upgrades-local` | `/etc/apt/apt.conf.d/` | unattended-upgrades auto-reboot at 21:30 UTC (03:00 IST) when a kernel/security update needs it |
| `harden-ssh.sh` | run once, 5 Sep 2026 | keys-only sshd, fail2ban, ufw (22 limited, 80, 443, 8005 from docker) |
| `provision-entra-demo.sh` | run once, 5 Sep 2026 | Entra app registration for the OctoAssist demo SSO |

Certificates and private keys are deliberately NOT captured
(`/etc/letsencrypt/live|archive`, `/opt/hrms-erp/nginx/ssl`).

## Certificate renewal (webroot, since 5 Sep 2026)

- certbot 2.9, `certbot.timer` twice daily, `authenticator = webroot`.
- Webroot is `/opt/hrms-erp/nginx/html` on the host = `/var/www/html` inside
  `hrms-nginx` (read-only mount). Every port-80 server block has
  `location /.well-known/acme-challenge/ { root /var/www/html; }` ahead of its
  HTTPS redirect, so validation works for all nine hostnames with nginx up.
- Renewal → deploy hook copies into nginx's ssl dir → `nginx -s reload`.
  Zero downtime; verified live with a forced renewal of fund.thirdoctopus.com.
- Before this it was standalone mode: pre/post hooks stopped and started
  `hrms-nginx`, so every site blipped at each timer run, and sites without a
  copy hook (hrms, fund) served stale certificates after renewal.

Docker-published ports are all bound to 127.0.0.1 (8088 OctoAssist,
8090 OctoFlow, 8092 OctoVault, 8085/8086 nginx preview listeners); only
22/80/443 are public. OctoCred is a host systemd service on :8005 reached by
nginx via 172.18.0.1 — it needs the explicit ufw allow in `harden-ssh.sh`.
