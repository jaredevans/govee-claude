from __future__ import annotations

from typing import Protocol


class BulbClient(Protocol):
    def set_rgb(self, rgb: int) -> None: ...


def hex_to_rgb_int(hex_str: str) -> int:
    s = hex_str.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        raise ValueError(f"invalid hex color: {hex_str!r}")
    return int(s, 16)


def rgb_int_to_tuple(rgb: int) -> tuple[int, int, int]:
    return ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
