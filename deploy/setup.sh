#!/usr/bin/env bash
# One-shot installer for Alibaba Cloud Linux 3 (also works on Ubuntu 22.04).
#
# Two ways to use:
#   A) Git-based (recommended):
#        export GIT_REPO=https://gitee.com/<your-account>/mes-agent.git
#        export GIT_BRANCH=main
#        curl -fsSL https://gitee.com/<your-account>/mes-agent/raw/main/deploy/setup.sh | sudo -E bash
#      (or sudo -E bash deploy/setup.sh after manual clone)
#
#   B) Local-source: cd into a copy of the repo and run:  sudo bash deploy/setup.sh
#
set -euo pipefail

APP_USER=mesagent
APP_DIR=/opt/mes-agent
LOG_DIR=/var/log/mes-agent
GIT_REPO=${GIT_REPO:-}             # if set, will git clone instead of rsync
GIT_BRANCH=${GIT_BRANCH:-main}

# ---------- 0. Detect OS ----------
. /etc/os-release || true
echo "[setup] Detected: ${PRETTY_NAME:-unknown}"

# ---------- 1. Install system packages ----------
if command -v dnf &>/dev/null; then
  dnf install -y python3.11 python3.11-pip python3.11-devel gcc nginx git || \
  dnf install -y python3 python3-pip python3-devel gcc nginx git
elif command -v apt-get &>/dev/null; then
  apt-get update
  apt-get install -y python3.11 python3.11-venv python3-pip python3-dev build-essential nginx git
fi

# Pick python binary
PYBIN=$(command -v python3.11 || command -v python3)
echo "[setup] Using $PYBIN"

# ---------- 2. Create user ----------
if ! id "$APP_USER" &>/dev/null; then
  useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"
  echo "[setup] Created user $APP_USER"
fi

# ---------- 3. Get source code ----------
if [ -n "$GIT_REPO" ]; then
  echo "[setup] Cloning $GIT_REPO ($GIT_BRANCH) into $APP_DIR"
  if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch --all
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$GIT_BRANCH"
  else
    rm -rf "$APP_DIR" 2>/dev/null || true
    mkdir -p "$APP_DIR"
    chown "$APP_USER":"$APP_USER" "$APP_DIR"
    sudo -u "$APP_USER" git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_REPO" "$APP_DIR"
  fi
else
  # Local-source mode: rsync from the directory containing this script
  SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  echo "[setup] Local source: $SRC_DIR"
  mkdir -p "$APP_DIR"
  rsync -a --delete --exclude='.venv' --exclude='data' --exclude='uploads' --exclude='__pycache__' --exclude='.git' \
    "$SRC_DIR"/ "$APP_DIR"/
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ---------- 4. Python venv + deps ----------
sudo -u "$APP_USER" bash -lc "
  cd $APP_DIR
  $PYBIN -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
"

# ---------- 5. .env ----------
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  # randomize JWT secret
  JWT=$(openssl rand -hex 32)
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$JWT|" "$APP_DIR/.env"
  echo "[setup] Created .env (please edit and set MINIMAX_API_KEY)"
fi

# ---------- 6. data / uploads / logs ----------
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data" "$APP_DIR/uploads"
mkdir -p "$LOG_DIR"
chown "$APP_USER":"$APP_USER" "$LOG_DIR"

# ---------- 7. systemd service ----------
cp "$APP_DIR/deploy/mes-agent.service" /etc/systemd/system/mes-agent.service
systemctl daemon-reload
systemctl enable mes-agent

# ---------- 8. Nginx ----------
if [ -d /etc/nginx/conf.d ]; then
  cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/conf.d/mes-agent.conf
  nginx -t && systemctl restart nginx
fi

# ---------- 9. firewalld (if present) ----------
if command -v firewall-cmd &>/dev/null; then
  firewall-cmd --permanent --add-service=http || true
  firewall-cmd --permanent --add-service=https || true
  firewall-cmd --reload || true
fi

echo ""
echo "==============================================================="
echo "[setup] DONE. Next steps:"
echo "  1) Edit $APP_DIR/.env  (set MINIMAX_API_KEY, change ADMIN_PASSWORD)"
echo "  2) systemctl start mes-agent"
echo "  3) systemctl status mes-agent"
echo "  4) Open  http://<your-server-ip>/  in browser"
echo "  5) Login with admin / ChangeMe!2026  (change immediately!)"
echo "==============================================================="
