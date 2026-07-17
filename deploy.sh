#!/usr/bin/env bash
set -Eeuo pipefail

# 可通过环境变量覆盖：
#   BRANCH=main DOMAIN=example.com BACKEND_PORT=5002 ./deploy.sh
#   REPO_URL=git@github.com:owner/repo.git APP_DIR=/opt/mygoal ./deploy.sh

APP_NAME="${APP_NAME:-mygoal}"
BRANCH="${BRANCH:-codex/match-analysis-integration}"
DOMAIN="${DOMAIN:-mygoal.top}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%/}"
BACKEND_PORT="${BACKEND_PORT:-5002}"
REPO_URL="${REPO_URL:-git@github.com:ahhswangkai/mygoal.git}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  printf '\n\033[1;32m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
  printf '\n\033[1;31m部署失败：%s\033[0m\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令 $1"
}

require_command git
require_command "$PYTHON_BIN"
require_command npm
require_command nginx
require_command systemctl

if [[ ! -d "$APP_DIR/.git" ]]; then
  log "克隆项目到 $APP_DIR"
  [[ -n "$REPO_URL" ]] || fail "目录不是 Git 仓库，请通过 REPO_URL 指定仓库地址"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  fail "服务器工作区存在未提交修改，请先处理后再部署"
fi

log "更新分支 $BRANCH"
git fetch origin "$BRANCH"
git switch "$BRANCH"
git pull --ff-only origin "$BRANCH"

log "安装 Python 依赖"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

log "构建 Vue 前端"
cd "$APP_DIR/frontend"
npm ci
npm run build
cd "$APP_DIR"

mkdir -p "$APP_DIR/data" "$APP_DIR/logs"

if [[ ! -f "$ENV_FILE" ]]; then
  log "生成生产环境配置 $ENV_FILE"
  SECRET_KEY="$("$VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET_KEY
MONGODB_URI=mongodb://127.0.0.1:27017/
DATA_DIR=$APP_DIR/data
USER_DATABASE_PATH=$APP_DIR/data/users.db
WECHAT_WEBHOOK_URL=
REQUEST_TIMEOUT=30
REQUEST_DELAY=2
MAX_RETRIES=3
EOF
  chmod 600 "$ENV_FILE"
else
  log "保留已有环境配置 $ENV_FILE"
fi

SYSTEMD_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_FILE="/etc/nginx/sites-available/${APP_NAME}"

log "写入 systemd 服务"
$SUDO tee "$SYSTEMD_FILE" >/dev/null <<EOF
[Unit]
Description=MyGoal football analysis service
After=network.target mongod.service
Wants=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:$BACKEND_PORT --timeout 180 --access-logfile - --error-logfile - wsgi:application
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

log "写入 Nginx 配置"
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
if $SUDO test -f "$CERT_DIR/fullchain.pem" && $SUDO test -f "$CERT_DIR/privkey.pem"; then
  log "检测到有效证书配置文件，启用 HTTPS"
  $SUDO tee "$NGINX_FILE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $DOMAIN;

    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    root $APP_DIR/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 180s;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(?:js|css|png|jpg|jpeg|gif|svg|ico|webp|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
        try_files \$uri =404;
    }
}
EOF
else
  log "未检测到 Let's Encrypt 证书，先启用 HTTP"
  $SUDO tee "$NGINX_FILE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $APP_DIR/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 180s;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(?:js|css|png|jpg|jpeg|gif|svg|ico|webp|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
        try_files \$uri =404;
    }
}
EOF
fi

$SUDO ln -sfn "$NGINX_FILE" "/etc/nginx/sites-enabled/${APP_NAME}"
$SUDO nginx -t

log "重启服务"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME" >/dev/null
$SUDO systemctl restart "$SERVICE_NAME"
$SUDO systemctl reload nginx

sleep 2
if ! $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
  $SUDO journalctl -u "$SERVICE_NAME" -n 80 --no-pager
  fail "后端服务启动失败"
fi

log "部署完成"
printf '分支：%s\n' "$BRANCH"
printf '后端：http://127.0.0.1:%s\n' "$BACKEND_PORT"
if [[ "$DOMAIN" == "_" ]]; then
  printf '网站：http://服务器IP/\n'
elif $SUDO test -f "$CERT_DIR/fullchain.pem"; then
  printf '网站：https://%s/\n' "$DOMAIN"
else
  printf '网站：http://%s/\n' "$DOMAIN"
fi
printf '日志：sudo journalctl -u %s -f\n' "$SERVICE_NAME"
