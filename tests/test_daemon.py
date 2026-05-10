import time

import pytest

from daemon import Daemon
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}


def make_daemon(period=0.01):
    return Daemon(client=FakeBulbClient(), period_seconds=period, colors=COLORS)


def wait_for(predicate, timeout=1.0, interval=0.005):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_initial_mode_is_idle():
    d = make_daemon()
    assert d.mode == "idle"
    assert d.client.calls == []


def test_yellow_sets_solid_yellow():
    d = make_daemon()
    d.handle("yellow")
    assert d.mode == "yellow"
    assert d.client.calls == [COLORS["yellow"]]


def test_red_sets_solid_red():
    d = make_daemon()
    d.handle("red")
    assert d.client.calls == [COLORS["red"]]


def test_white_sets_solid_white():
    d = make_daemon()
    d.handle("white")
    assert d.client.calls == [COLORS["white"]]


def test_flash_emits_blue_and_aqua():
    d = make_daemon(period=0.01)
    try:
        d.handle("flash")
        assert wait_for(lambda: COLORS["blue"] in d.client.calls and COLORS["aqua"] in d.client.calls)
    finally:
        d.handle("quit")


def test_flash_to_yellow_stops_cleanly():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("yellow")

    # No more emissions after the final yellow.
    final_calls = d.client.snapshot()
    assert final_calls[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final_calls


def test_flash_to_red_stops_cleanly():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("red")
    final_calls = d.client.snapshot()
    assert final_calls[-1] == COLORS["red"]
    time.sleep(0.05)
    assert d.client.snapshot() == final_calls


def test_red_then_flash_then_yellow_ends_yellow():
    d = make_daemon(period=0.01)
    d.handle("red")
    d.handle("flash")
    assert wait_for(lambda: COLORS["blue"] in d.client.calls or COLORS["aqua"] in d.client.calls)
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_double_flash_is_idempotent():
    d = make_daemon(period=0.01)
    d.handle("flash")
    first_worker = d._worker  # noqa: SLF001
    d.handle("flash")
    assert d._worker is first_worker  # noqa: SLF001
    d.handle("quit")


def test_set_rgb_failure_does_not_crash_flash_loop():
    d = make_daemon(period=0.005)
    d.client.fail_next = 2
    d.handle("flash")
    assert wait_for(lambda: len(d.client.calls) >= 3)
    d.handle("quit")


def test_quit_stops_worker():
    d = make_daemon(period=0.01)
    d.handle("flash")
    assert wait_for(lambda: d._worker is not None and d._worker.is_alive())  # noqa: SLF001
    d.handle("quit")
    time.sleep(0.05)
    assert d._worker is None or not d._worker.is_alive()  # noqa: SLF001
