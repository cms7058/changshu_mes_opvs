#!/usr/bin/env bash
# Pull latest code from git, reinstall deps if requirements changed, restart service.
# Usage: sudo bash /opt/mes-agent/deploy/update.sh
set -euo pipefail

APP_USER=mesagent
APP_DIR=/opt/mes-agent
GIT_BRANCH=${GIT_BRANCH:-main}

if [ ! -d "$APP_DIR/.git" ]; then
  echo "[update] $APP_DIR is not a git checkout. Use setup.sh with GIT_REPO instead."
  exit 1
fi

# All git commands must run as the repo owner to avoid "dubious ownership"
GIT="sudo -u $APP_USER git -C $APP_DIR"

# Belt-and-suspenders: mark this dir as safe globally
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "[update] Fetching latest from origin/$GIT_BRANCH …"
OLD_HASH=$($GIT rev-parse HEAD)
# Try shallow fetch first (much smaller, more reliable on flaky networks)
if ! $GIT fetch --depth 1 origin "$GIT_BRANCH" 2>/dev/null; then
  echo "[update] shallow fetch failed, falling back to full fetch"
  $GIT fetch --all
fi
$GIT reset --hard "origin/$GIT_BRANCH"
NEW_HASH=$($GIT rev-parse HEAD)

if [ "$OLD_HASH" = "$NEW_HASH" ]; then
  echo "[update] Already at $NEW_HASH — no changes."
  exit 0
fi

# Re-install deps if requirements.txt changed
if $GIT diff --name-only "$OLD_HASH" "$NEW_HASH" | grep -q '^requirements.txt$'; then
  echo "[update] requirements.txt changed — re-installing deps"
  sudo -u "$APP_USER" bash -lc "
    cd $APP_DIR && source .venv/bin/activate && pip install -r requirements.txt
  "
fi

echo "[update] Restarting service …"
systemctl restart mes-agent
sleep 2
systemctl --no-pager status mes-agent | head -15

echo "[update] DONE.  $OLD_HASH → $NEW_HASH"
