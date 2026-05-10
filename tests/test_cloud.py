import json

import httpx
import pytest

from govee.cloud import CloudClient, CloudAuthError, CloudRateLimited


def make_client(handler, api_key="test-key", sku="H6004",
                device_id="DE:AD:BE:EF:CA:FE:00:01"):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, timeout=5.0)
    return CloudClient(api_key=api_key, sku=sku, device_id=device_id, http=http)


def test_set_rgb_sends_correct_request():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"code": 200, "msg": "success"})

    c = make_client(handler)
    c.set_rgb(0xFF0000)

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/router/api/v1/device/control"
    assert req.headers["Govee-API-Key"] == "test-key"
    body = json.loads(req.content)
    assert body["payload"]["sku"] == "H6004"
    assert body["payload"]["device"] == "DE:AD:BE:EF:CA:FE:00:01"
    assert body["payload"]["capability"]["instance"] == "colorRgb"
    assert body["payload"]["capability"]["value"] == 0xFF0000
    assert "requestId" in body


def test_retries_once_on_5xx():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": 200})

    c = make_client(handler)
    c.set_rgb(0x00FF00)
    assert calls["n"] == 2


def test_does_not_retry_on_400():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"message": "bad"})

    c = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        c.set_rgb(0x0000FF)
    assert calls["n"] == 1


def test_401_raises_auth_error():
    def handler(request):
        return httpx.Response(401, json={"message": "bad key"})

    c = make_client(handler)
    with pytest.raises(CloudAuthError):
        c.set_rgb(0x0000FF)


def test_429_raises_rate_limited():
    def handler(request):
        return httpx.Response(429, json={"message": "slow down"})

    c = make_client(handler)
    with pytest.raises(CloudRateLimited):
        c.set_rgb(0x0000FF)


def test_list_devices_returns_payload():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/router/api/v1/user/devices"
        return httpx.Response(200, json={
            "code": 200,
            "data": [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}],
        })

    c = make_client(handler)
    devices = c.list_devices()
    assert devices == [{"sku": "H6004", "device": "DE:AD:BE:EF:CA:FE:00:01"}]
