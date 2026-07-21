#!/usr/bin/env bash
# Pull the latest code from GitHub and redeploy — run on the VM (or via cron for auto-deploy).
#   bash deploy/oracle/update.sh
# Cron example (auto-deploy every 5 min when new commits land):
#   */5 * * * * cd /home/ubuntu/krw-watcher && bash deploy/oracle/update.sh >> /home/ubuntu/deploy.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/../.."

git fetch origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse @{u})
if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # already up to date
fi

echo "==> New commits found ($LOCAL -> $REMOTE), redeploying…"
git pull --ff-only
cd deploy/oracle
docker compose up -d --build
echo "==> Deployed $(git rev-parse --short HEAD) at $(date)"
