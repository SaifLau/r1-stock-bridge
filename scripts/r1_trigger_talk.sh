#!/usr/bin/env bash
set -euo pipefail

ADB_BIN="${R1LAB_ADB_BIN:-/tmp/platform-tools/adb}"
R1_IP="${R1LAB_R1_IP:-}"
if [[ -z "${R1_IP}" ]]; then
  echo "R1LAB_R1_IP is required for ADB helper scripts" >&2
  exit 1
fi
SERIAL="${R1LAB_R1_SERIAL:-${R1_IP}:5555}"

if [[ ! -x "${ADB_BIN}" ]]; then
  ADB_BIN="$(command -v adb || true)"
fi

if [[ -z "${ADB_BIN}" ]]; then
  echo "adb not found; set R1LAB_ADB_BIN or install adb" >&2
  exit 1
fi

echo "triggering stock START_TALK on ${SERIAL}"
"${ADB_BIN}" connect "${SERIAL}" >/dev/null 2>&1 || true
"${ADB_BIN}" -s "${SERIAL}" shell am startservice \
  -a com.unisound.intent.action.START_TALK \
  -n com.phicomm.speaker.device/.ui.service.WindowsService
