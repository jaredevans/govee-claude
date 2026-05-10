from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

API_BASE = "https://openapi.api.govee.com"


class CloudAuthError(RuntimeError):
    """Govee API rejected the API key (401/403)."""


class CloudRateLimited(RuntimeError):
    """Govee API returned 429."""


class CloudClient:
    def __init__(
        self,
        *,
        api_key: str,
        sku: str,
        device_id: str,
        http: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.sku = sku
        self.device_id = device_id
        self.http = http or httpx.Client(base_url=API_BASE, timeout=15.0)
        # When user passes a transport-only client, base_url may be empty —
        # we use absolute URLs in requests so it works either way.

    def _headers(self) -> dict[str, str]:
        return {
            "Govee-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _post_with_retry(self, url: str, body: dict[str, Any]) -> httpx.Response:
        for attempt in (1, 2):
            try:
                resp = self.http.post(url, headers=self._headers(), json=body)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                continue
            if resp.status_code in (401, 403):
                raise CloudAuthError(f"Govee API auth failed: {resp.status_code} {resp.text}")
            if resp.status_code == 429:
                raise CloudRateLimited("Govee API rate limited (429)")
            if 500 <= resp.status_code < 600 and attempt == 1:
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    def set_rgb(self, rgb: int) -> None:
        body = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": self.sku,
                "device": self.device_id,
                "capability": {
                    "type": "devices.capabilities.color_setting",
                    "instance": "colorRgb",
                    "value": rgb,
                },
            },
        }
        self._post_with_retry(f"{API_BASE}/router/api/v1/device/control", body)

    def list_devices(self) -> list[dict[str, Any]]:
        for attempt in (1, 2):
            try:
                resp = self.http.get(
                    f"{API_BASE}/router/api/v1/user/devices",
                    headers=self._headers(),
                )
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                continue
            if resp.status_code in (401, 403):
                raise CloudAuthError(f"Govee API auth failed: {resp.status_code}")
            if 500 <= resp.status_code < 600 and attempt == 1:
                time.sleep(0.5)
                continue
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("data", []))
        raise RuntimeError("unreachable")
