#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


DEFAULT_R1_IP = os.getenv("R1LAB_R1_IP", "").strip()
DEFAULT_ADB = os.getenv("R1LAB_ADB_BIN", "/tmp/platform-tools/adb")


def adb_bin() -> str:
    configured = DEFAULT_ADB
    if configured and os.path.exists(configured):
        return configured
    for candidate in [
        shutil.which("adb"),
        "/Applications/BlueStacks.app/Contents/MacOS/hd-adb",
    ]:
        if candidate and os.path.exists(candidate):
            return candidate
    raise SystemExit("adb binary not found. Set R1LAB_ADB_BIN or install adb.")


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [adb_bin(), *args]
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def reconnect(serial: str) -> None:
    run(["disconnect", serial], check=False)
    completed = run(["connect", serial], check=False)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)


def cmd_probe(serial: str) -> int:
    reconnect(serial)
    devices = run(["devices", "-l"], check=False)
    print(devices.stdout.strip())
    model = run(["-s", serial, "shell", "getprop", "ro.product.model"], check=False)
    version = run(["-s", serial, "shell", "getprop", "ro.build.version.release"], check=False)
    print(f"model={model.stdout.strip() or '<unknown>'}")
    print(f"android={version.stdout.strip() or '<unknown>'}")
    if devices.returncode != 0:
        return devices.returncode
    return 0


def cmd_shell(serial: str, shell_command: str) -> int:
    reconnect(serial)
    completed = run(["-s", serial, "shell", shell_command], check=False)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal ADB helper for Phicomm R1")
    default_serial = f"{DEFAULT_R1_IP}:5555" if DEFAULT_R1_IP else ""
    parser.add_argument("--serial", default=default_serial)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe")
    shell_parser = sub.add_parser("shell")
    shell_parser.add_argument("shell_command")

    args = parser.parse_args()
    if not args.serial:
        parser.error("set --serial or export R1LAB_R1_IP before using ADB helpers")

    if args.command == "probe":
        return cmd_probe(args.serial)
    if args.command == "shell":
        return cmd_shell(args.serial, args.shell_command)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
