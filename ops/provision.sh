#!/bin/bash
# One-time setup of a fresh Ubuntu 24.04 droplet for trailgeek.
# Idempotent: safe to re-run. Run as root on the droplet:
#   ssh root@<ip> 'bash -s' < ops/provision.sh
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl git unattended-upgrades fail2ban

# Docker Engine + compose plugin from Docker's own apt repo.
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Container logs are capped so a chatty worker cannot fill the disk.
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
JSON
systemctl restart docker

# 2 GB swap: rasterio jobs spike memory on a 4 GB box.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=10 >/dev/null
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf

# SSH: keys only. A fresh public IP gets dozens of scanner connections at
# once, which exhausts sshd's default pre-auth limit (MaxStartups 10:30:100)
# and randomly drops real logins; raise it and let fail2ban ban the scanners.
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
cat > /etc/ssh/sshd_config.d/10-trailgeek.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
MaxStartups 100:30:200
LoginGraceTime 20
CONF
cat > /etc/fail2ban/jail.d/sshd.local <<'CONF'
[sshd]
enabled = true
maxretry = 3
findtime = 10m
bantime = 1d
CONF
systemctl enable --now fail2ban
systemctl restart fail2ban
systemctl reload ssh || systemctl reload sshd

dpkg-reconfigure -f noninteractive unattended-upgrades
timedatectl set-timezone America/Anchorage

mkdir -p /opt/trailgeek
echo "provisioned: $(docker --version); $(docker compose version)"
