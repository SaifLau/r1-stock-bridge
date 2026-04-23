#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
SERVICE_SRC="${ROOT_DIR}/deploy/r1-stock-bridge.service"
SERVICE_DST="/etc/systemd/system/r1-stock-bridge.service"

mkdir -p "${ROOT_DIR}/logs" "${ROOT_DIR}/cache" "${ROOT_DIR}/data" "${ROOT_DIR}/config"

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3 venv module is unavailable. Install python3-venv first." >&2
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
"${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"

install -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
systemctl daemon-reload
systemctl enable r1-stock-bridge.service
systemctl restart r1-stock-bridge.service
systemctl --no-pager --full status r1-stock-bridge.service
