import pytest
from govee.client import hex_to_rgb_int, rgb_int_to_tuple


@pytest.mark.parametrize("hex_str,expected", [
    ("#FF0000", 0xFF0000),
    ("#00FF00", 0x00FF00),
    ("#0000FF", 0x0000FF),
    ("#FFFFFF", 0xFFFFFF),
    ("#000000", 0x000000),
    ("FFFF00", 0xFFFF00),       # leading-# optional
    ("#00ffff", 0x00FFFF),      # case-insensitive
])
def test_hex_to_rgb_int(hex_str, expected):
    assert hex_to_rgb_int(hex_str) == expected


def test_hex_to_rgb_int_rejects_bad_input():
    for bad in ["", "#FFF", "#GGGGGG", "12345", "#1234567"]:
        with pytest.raises(ValueError):
            hex_to_rgb_int(bad)


def test_rgb_int_to_tuple():
    assert rgb_int_to_tuple(0xFF0000) == (255, 0, 0)
    assert rgb_int_to_tuple(0x00FF00) == (0, 255, 0)
    assert rgb_int_to_tuple(0x0000FF) == (0, 0, 255)
    assert rgb_int_to_tuple(0xFFFFFF) == (255, 255, 255)
