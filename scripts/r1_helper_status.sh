#!/usr/bin/env bash

set -euo pipefail

ADB_BIN="${R1LAB_ADB_BIN:-/tmp/platform-tools/adb}"
R1_IP="${R1LAB_R1_IP:-}"

if [[ -z "$R1_IP" ]]; then
  echo "R1LAB_R1_IP is required for ADB helper scripts" >&2
  exit 1
fi

R1_SERIAL="${R1LAB_R1_SERIAL:-${R1_IP}:5555}"

if [[ ! -x "$ADB_BIN" ]]; then
  echo "adb not found at $ADB_BIN" >&2
  exit 1
fi

reconnect() {
  "$ADB_BIN" disconnect "$R1_SERIAL" >/dev/null 2>&1 || true
  "$ADB_BIN" connect "$R1_SERIAL" >/dev/null 2>&1 || true
}

echo "== Device =="
reconnect
"$ADB_BIN" devices -l
echo ""
echo "== Model =="
reconnect
"$ADB_BIN" -s "$R1_SERIAL" shell getprop ro.product.model || true
echo "== Android =="
reconnect
"$ADB_BIN" -s "$R1_SERIAL" shell getprop ro.build.version.release || true
echo ""
echo "== Speaker Packages =="
reconnect
"$ADB_BIN" -s "$R1_SERIAL" shell "pm list packages | grep phicomm.speaker" || true
