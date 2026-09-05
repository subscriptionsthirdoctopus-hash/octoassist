#!/usr/bin/env bash
# SSH hardening for the Third Octopus droplet (68.183.86.66, Ubuntu 24.04).
#
# Why: on 5 Sep 2026 the box logged 106,864 failed SSH logins in a day with no
# fail2ban and no firewall, while sshd still accepted passwords and root login.
# The brute-force traffic kept MaxStartups (10:30:100) saturated — 5,144
# "exceeded" drops that day — which is what was resetting legitimate deploys.
# Every real login in the preceding week was root via public key, so turning
# password auth off does not lock anyone out.
#
# Run from your Mac:   ssh octoassist 'bash -s' < deploy/droplet/harden-ssh.sh
# Keep the session you ran it from OPEN and test a fresh `ssh octoassist` from
# a second terminal before closing it.
set -euo pipefail

echo "== 1/4 sshd hardening drop-in (00- sorts first, so it wins over cloud-init's 50-)"
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
# Keys only. Every legitimate login here is publickey (audit 5 Sep 2026).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
# Give brute-forcers less to hold: 30s instead of 120s per unauthenticated
# connection, 3 tries instead of 6, and a deeper pre-auth queue so a burst of
# bots cannot crowd out a real connection.
LoginGraceTime 30
MaxAuthTries 3
MaxStartups 30:50:300
# Drop dead sessions instead of holding slots for them.
ClientAliveInterval 30
ClientAliveCountMax 4
EOF
sshd -t && echo "   sshd config OK"
systemctl reload ssh
sshd -T | grep -E '^(passwordauthentication|permitrootlogin|maxstartups|logingracetime) '

echo "== 2/4 fail2ban with an sshd jail"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban >/dev/null
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
backend  = systemd
bantime  = 1h
findtime = 10m
maxretry = 4
# Repeat offenders get progressively longer bans, up to a week.
bantime.increment = true
bantime.factor    = 2
bantime.maxtime   = 1w
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
mode    = aggressive
EOF
systemctl enable --now fail2ban >/dev/null
sleep 3
fail2ban-client status sshd | sed 's/^/   /'

echo "== 3/4 host firewall (docker-published ports are NOT governed by ufw; see note)"
# ufw only covers what the host itself listens on. Docker inserts its own
# iptables chain ahead of ufw, so 80/443/8085-8092 stay reachable regardless —
# this is about sshd and anything else bound on the host.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw limit 22/tcp comment 'ssh (rate-limited)' >/dev/null
ufw allow 80/tcp  comment 'http'  >/dev/null
ufw allow 443/tcp comment 'https' >/dev/null
# OctoCred is a host service (systemd, :8005) that hrms-nginx reaches through
# the bridge gateway 172.18.0.1. That container->host hop crosses ufw's INPUT
# chain, so "deny incoming" silently broke octocred.thirdoctopus.com on the
# first run (5 Sep 2026). Only this one upstream targets the host.
ufw allow from 172.18.0.0/16 to any port 8005 proto tcp comment 'octocred: nginx -> host' >/dev/null
ufw --force enable >/dev/null
ufw status numbered | sed 's/^/   /'

echo "== 4/4 done. Now, from ANOTHER terminal, confirm: ssh octoassist 'echo still in'"
