#!/usr/bin/env python3
"""govee-claude setup: discover device on LAN or fall back to cloud, write config."""
from __future__ import annotations

import sys
from pathlib import Path

# Make `from govee.cloud import ...` work when this file is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
import os
import socket
import time
from typing import Iterable

import httpx

from govee.cloud import API_BASE


SCAN_PAYLOAD = json.dumps({
    "msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}
}).encode()


class SetupError(RuntimeError):
    pass


def _primary_local_ip() -> str:
    """Pick the local IP the kernel would use to reach the public internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _broadcast_for_24(ip: str) -> str:
    parts = ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def _collect_lan_responses(local_ip: str, timeout: float) -> list[dict]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    listener.bind((local_ip, 4002))
    listener.settimeout(0.3)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sender.bind((local_ip, 0))

    targets = [
        ("239.255.255.250", 4001),
        (_broadcast_for_24(local_ip), 4001),
        ("255.255.255.255", 4001),
    ]
    for _ in range(3):
        for t in targets:
            try:
                sender.sendto(SCAN_PAYLOAD, t)
            except OSError:
                pass
        time.sleep(0.4)
    sender.close()

    end = time.time() + timeout
    out: list[dict] = []
    while time.time() < end:
        try:
            data, _ = listener.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            out.append(json.loads(data))
        except ValueError:
            pass
    listener.close()
    return out


def lan_discover(target_sku: str, target_device: str, local_ip: str | None = None,
                 timeout: float = 4.0) -> str | None:
    ip = local_ip or _primary_local_ip()
    for resp in _collect_lan_responses(ip, timeout):
        d = resp.get("msg", {}).get("data", {})
        if d.get("sku") == target_sku or d.get("device") == target_device:
            found_ip = d.get("ip")
            if found_ip:
                return found_ip
    return None


def validate_cloud(api_key: str, sku: str, device_id: str, *,
                   http: httpx.Client | None = None) -> bool:
    h = http or httpx.Client(timeout=10.0)
    try:
        resp = h.get(
            f"{API_BASE}/router/api/v1/user/devices",
            headers={"Govee-API-Key": api_key},
        )
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise SetupError(f"network error talking to Govee: {e}")
    if resp.status_code in (401, 403):
        raise SetupError(f"Govee API key rejected: {resp.status_code}")
    resp.raise_for_status()
    devices = resp.json().get("data", []) or []
    return any(d.get("sku") == sku and d.get("device") == device_id for d in devices)


def write_config(path: Path, *, mode: str, device_ip: str | None,
                 device_id: str, sku: str, api_key_path: str) -> None:
    period = 1.0 if mode == "lan" else 6.0
    cfg = {
        "mode": mode,
        "device_ip": device_ip,
        "device_id": device_id,
        "sku": sku,
        "api_key_path": api_key_path,
        "flash_period_seconds": period,
        "colors": {
            "yellow": "#FFFF00",
            "red":    "#FF0000",
            "blue":   "#0000FF",
            "aqua":   "#00FFFF",
            "white":  "#FFFFFF",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    runtime = Path(os.environ.get("GOVEE_CLAUDE_RUNTIME_DIR",
                   Path.home() / ".claude" / "govee-claude"))
    runtime.mkdir(parents=True, exist_ok=True)

    sku = os.environ.get("GOVEE_SKU", "H6004")
    device_id = os.environ.get("GOVEE_DEVICE_ID")
    api_key_path = os.environ.get("GOVEE_API_KEY_PATH")
    if not device_id or not api_key_path:
        # Try to discover via cloud list-devices.
        if not api_key_path:
            print("error: set GOVEE_API_KEY_PATH (path to file with API key)",
                  file=sys.stderr)
            return 2
        api_key = Path(api_key_path).read_text().strip()
        h = httpx.Client(timeout=10.0)
        resp = h.get(f"{API_BASE}/router/api/v1/user/devices",
                     headers={"Govee-API-Key": api_key})
        resp.raise_for_status()
        devices = resp.json().get("data", []) or []
        match = next((d for d in devices if d.get("sku") == sku), None)
        if match is None:
            print(f"error: no device with sku={sku} on this account", file=sys.stderr)
            return 3
        device_id = match["device"]

    print(f"resolving best mode for sku={sku} device={device_id} ...")
    if os.environ.get("GOVEE_ENABLE_LAN") == "1":
        lan_ip = lan_discover(sku, device_id)
        if lan_ip:
            write_config(runtime / "config.json", mode="lan", device_ip=lan_ip,
                         device_id=device_id, sku=sku, api_key_path=api_key_path)
            print(f"LAN discovered at {lan_ip}; mode=lan")
            return 0
        print("GOVEE_ENABLE_LAN=1 set but no device responded; falling back to cloud")
    else:
        print("LAN discovery skipped (set GOVEE_ENABLE_LAN=1 to opt in)")

    api_key = Path(api_key_path).read_text().strip()
    if not validate_cloud(api_key, sku, device_id):
        print(f"error: device {device_id} not found via cloud — re-check IDs",
              file=sys.stderr)
        return 4
    write_config(runtime / "config.json", mode="cloud", device_ip=None,
                 device_id=device_id, sku=sku, api_key_path=api_key_path)
    print("mode=cloud")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
