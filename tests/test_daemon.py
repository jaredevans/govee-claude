import time

import pytest

import daemon as daemon_mod
from daemon import Daemon
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
    "aqua":   0x00FFFF,
}


@pytest.fixture(autouse=True)
def _fast_flash(monkeypatch):
    """Shrink the flash half-period so tests don't wait whole seconds."""
    monkeypatch.setattr(daemon_mod, "FLASH_HALF_PERIOD", 0.01)


def make_daemon():
    return Daemon(client=FakeBulbClient(), colors=COLORS)


def _wait_for(predicate, timeout=1.0, interval=0.005):
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


def test_flash_alternates_blue_and_aqua():
    d = make_daemon()
    try:
        d.handle("flash")
        assert d.mode == "flash"
        assert _wait_for(
            lambda: COLORS["blue"] in d.client.calls and COLORS["aqua"] in d.client.calls
        )
    finally:
        d.handle("quit")


def test_flash_then_yellow_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final
    assert d.mode == "yellow"


def test_flash_then_red_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("red")
    final = d.client.snapshot()
    assert final[-1] == COLORS["red"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_flash_then_white_stops_alternation_cleanly():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(lambda: len(d.client.calls) >= 4)
    d.handle("white")
    final = d.client.snapshot()
    assert final[-1] == COLORS["white"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_red_then_flash_then_yellow_ends_yellow():
    d = make_daemon()
    d.handle("red")
    d.handle("flash")
    assert _wait_for(
        lambda: COLORS["blue"] in d.client.calls or COLORS["aqua"] in d.client.calls
    )
    d.handle("yellow")
    final = d.client.snapshot()
    assert final[-1] == COLORS["yellow"]
    time.sleep(0.05)
    assert d.client.snapshot() == final


def test_double_flash_is_idempotent():
    d = make_daemon()
    try:
        d.handle("flash")
        first_worker = d._worker  # noqa: SLF001
        d.handle("flash")
        assert d._worker is first_worker  # noqa: SLF001
        assert d._worker.is_alive()  # noqa: SLF001
    finally:
        d.handle("quit")


def test_quit_stops_worker():
    d = make_daemon()
    d.handle("flash")
    assert _wait_for(
        lambda: d._worker is not None and d._worker.is_alive()  # noqa: SLF001
    )
    d.handle("quit")
    assert d.mode == "idle"
    assert _wait_for(
        lambda: d._worker is None or not d._worker.is_alive()  # noqa: SLF001
    )


def test_set_rgb_failure_does_not_crash_flash_loop():
    d = make_daemon()
    d.client.fail_next = 2
    try:
        d.handle("flash")
        assert _wait_for(lambda: len(d.client.calls) >= 1, timeout=2.0)
    finally:
        d.handle("quit")
