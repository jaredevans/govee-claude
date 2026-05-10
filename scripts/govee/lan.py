from __future__ import annotations

import json
import socket
from typing import Callable

from .client import rgb_int_to_tuple

LAN_CMD_PORT = 4003


def _default_socket_factory() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


class LanClient:
    def __init__(
        self,
        *,
        device_ip: str,
        socket_factory: Callable[[], socket.socket] = _default_socket_factory,
    ) -> None:
        self.device_ip = device_ip
        self._socket_factory = socket_factory

    def set_rgb(self, rgb: int) -> None:
        r, g, b = rgb_int_to_tuple(rgb)
        payload = {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": r, "g": g, "b": b},
                    "colorTemInKelvin": 0,
                },
            }
        }
        sock = self._socket_factory()
        try:
            sock.sendto(json.dumps(payload).encode(), (self.device_ip, LAN_CMD_PORT))
        finally:
            sock.close()
