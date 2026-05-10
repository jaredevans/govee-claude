import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import setup as setup_mod


def test_lan_discovery_returns_ip_when_device_responds(monkeypatch):
    fake_response = {
        "msg": {
            "cmd": "scan",
            "data": {
                "ip": "10.0.0.42",
                "device": "DE:AD:BE:EF:CA:FE:00:01",
                "sku": "H6004",
            },
        }
    }
    monkeypatch.setattr(setup_mod, "_collect_lan_responses",
                        lambda local_ip, timeout: [fake_response])
    ip = setup_mod.lan_discover(target_sku="H6004", target_device="DE:AD:BE:EF:CA:FE:00:01",
                                local_ip="10.0.0.58", timeout=0.1)
    assert ip == "10.0.0.42"


def test_lan_discovery_returns_none_when_no_match(monkeypatch):
    monkeypatch.setattr(setup_mod, "_collect_lan_responses",
                        lambda local_ip, timeout: [])
    assert setup_mod.lan_discover("H6004", "DE:AD", "10.0.0.58", timeout=0.1) is None


def test_validate_cloud_passes_when_device_present():
    def handler(request):
        return httpx.Response(200, json={
            "code": 200,
            "data": [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}],
        })
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, timeout=5.0)
    assert setup_mod.validate_cloud("k", "H6004", "DE:AD:BE:EF:CA:FE:00:01", http=http) is True


def test_validate_cloud_returns_false_when_missing():
    def handler(request):
        return httpx.Response(200, json={"code": 200, "data": []})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert setup_mod.validate_cloud("k", "H6004", "DE:AD", http=http) is False


def test_validate_cloud_raises_on_auth_failure():
    def handler(request):
        return httpx.Response(401, json={"message": "bad"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(setup_mod.SetupError):
        setup_mod.validate_cloud("k", "H6004", "DE:AD", http=http)


def test_write_config_lan(tmp_path):
    setup_mod.write_config(tmp_path / "cfg.json", mode="lan",
                           device_ip="10.0.0.42",
                           device_id="DE:AD:BE:EF:CA:FE:00:01",
                           sku="H6004",
                           api_key_path="/tmp/k.txt")
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    assert cfg["mode"] == "lan"
    assert cfg["device_ip"] == "10.0.0.42"
    assert cfg["flash_period_seconds"] == 1.0


def test_write_config_cloud(tmp_path):
    setup_mod.write_config(tmp_path / "cfg.json", mode="cloud",
                           device_ip=None,
                           device_id="DE:AD",
                           sku="H6004",
                           api_key_path="/tmp/k.txt")
    cfg = json.loads((tmp_path / "cfg.json").read_text())
    assert cfg["mode"] == "cloud"
    assert cfg["flash_period_seconds"] == 6.0
