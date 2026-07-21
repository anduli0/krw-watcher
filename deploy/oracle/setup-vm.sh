#!/usr/bin/env bash
# One-time setup on a fresh Oracle Cloud Ubuntu VM (run as the default `ubuntu` user).
#   bash deploy/oracle/setup-vm.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Docker…"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo "==> Opening port 8010 in the VM firewall (Oracle images ship restrictive iptables)…"
sudo iptables -I INPUT -p tcp --dport 8010 -j ACCEPT
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save

echo "==> Preparing SQLite data dir (container runs as uid 10001)…"
mkdir -p data
sudo chown -R 10001:10001 data

echo
echo "Done. Next steps:"
echo "  1) Create the .env at the repo root (see docs/DEPLOY_ORACLE.md §5)."
echo "  2) Log out & back in (docker group), then:  cd deploy/oracle && docker compose up -d --build"
echo "  3) Verify:  curl http://localhost:8010/health"
