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

echo "[1/3] reconnect"
"$ADB_BIN" disconnect "$R1_SERIAL" >/dev/null 2>&1 || true
"$ADB_BIN" connect "$R1_SERIAL"

echo "[2/3] disable stock speaker services"
for pkg in \
  com.phicomm.speaker.player \
  com.phicomm.speaker.device \
  com.phicomm.speaker.airskill \
  com.phicomm.speaker.exceptionreporter \
  com.phicomm.speaker.ijetty \
  com.phicomm.speaker.netctl \
  com.phicomm.speaker.otaservice \
  com.phicomm.speaker.systemtool \
  com.phicomm.speaker.productiontest \
  com.phicomm.speaker.bugreport
do
  "$ADB_BIN" -s "$R1_SERIAL" shell /system/bin/pm hide "$pkg" || true
done

echo "[3/3] current device list"
"$ADB_BIN" devices -l

echo ""
echo "Stock voice services have been hidden where possible."
echo "Do not hide com.phicomm.speaker.launcher."
