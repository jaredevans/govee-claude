from __future__ import annotations

import threading


class FakeBulbClient:
    """Records every set_rgb call. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[int] = []
        self.fail_next: int = 0

    def set_rgb(self, rgb: int) -> None:
        with self._lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise RuntimeError("simulated failure")
            self.calls.append(rgb)

    def snapshot(self) -> list[int]:
        with self._lock:
            return list(self.calls)
