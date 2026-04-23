#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB_BIN="${R1LAB_ADB_BIN:-/tmp/platform-tools/adb}"
R1_IP="${R1LAB_R1_IP:-}"
if [[ -z "${R1_IP}" ]]; then
  echo "R1LAB_R1_IP is required for ADB helper scripts" >&2
  exit 1
fi
SERIAL="${R1LAB_R1_SERIAL:-${R1_IP}:5555}"
PORT="${R1LAB_PORT:-18888}"
MAC_IP="${R1LAB_MAC_IP:-$(ipconfig getifaddr en0 2>/dev/null || true)}"

if [[ -z "${MAC_IP}" ]]; then
  MAC_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi

if [[ -z "${MAC_IP}" ]]; then
  echo "failed to detect Mac LAN IP; set R1LAB_MAC_IP manually" >&2
  exit 1
fi

if [[ ! -x "${ADB_BIN}" ]]; then
  ADB_BIN="$(command -v adb || true)"
fi

if [[ -z "${ADB_BIN}" ]]; then
  echo "adb not found; set R1LAB_ADB_BIN or install adb" >&2
  exit 1
fi

echo "setting R1 global http_proxy => ${MAC_IP}:${PORT}"
"${ADB_BIN}" connect "${SERIAL}" >/dev/null 2>&1 || true
"${ADB_BIN}" -s "${SERIAL}" shell settings put global http_proxy "${MAC_IP}:${PORT}"
"${ADB_BIN}" -s "${SERIAL}" shell settings get global http_proxy
