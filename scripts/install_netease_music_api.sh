#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${R1LAB_MUSIC_INSTALL_ROOT:-/opt/netease_cloud_music_api}"
PORT="${R1LAB_MUSIC_PORT:-3900}"
PACKAGE_VERSION="${R1LAB_MUSIC_PACKAGE_VERSION:-4.31.0}"
NODE_BIN="${R1LAB_NODE_BIN:-$(command -v node || true)}"
NPM_BIN="${R1LAB_NPM_BIN:-$(command -v npm || true)}"
SERVICE_NAME="netease-cloud-music-api.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

if [[ -z "${NODE_BIN}" || -z "${NPM_BIN}" ]]; then
  echo "node/npm not found. Install Node.js 18+ first." >&2
  exit 1
fi

mkdir -p "${INSTALL_ROOT}"
cd "${INSTALL_ROOT}"

if [[ ! -f package.json ]]; then
  "${NPM_BIN}" init -y >/dev/null 2>&1
fi

"${NPM_BIN}" install --omit=dev "NeteaseCloudMusicApi@${PACKAGE_VERSION}"

cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=NetEase Cloud Music API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_ROOT}
Environment=PORT=${PORT}
ExecStart=${NODE_BIN} ${INSTALL_ROOT}/node_modules/NeteaseCloudMusicApi/app.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
