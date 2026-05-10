from daemon import Daemon
from tests.fakes import FakeBulbClient


COLORS = {
    "yellow": 0xFFFF00,
    "red":    0xFF0000,
    "white":  0xFFFFFF,
    "blue":   0x0000FF,
}


def make_daemon():
    return Daemon(client=FakeBulbClient(), colors=COLORS)


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


def test_flash_sets_solid_blue():
    d = make_daemon()
    d.handle("flash")
    assert d.mode == "flash"
    assert d.client.calls == [COLORS["blue"]]


def test_flash_then_yellow_ends_yellow():
    d = make_daemon()
    d.handle("flash")
    d.handle("yellow")
    assert d.client.calls == [COLORS["blue"], COLORS["yellow"]]
    assert d.mode == "yellow"


def test_red_then_flash_then_yellow_ends_yellow():
    d = make_daemon()
    d.handle("red")
    d.handle("flash")
    d.handle("yellow")
    assert d.client.calls == [COLORS["red"], COLORS["blue"], COLORS["yellow"]]


def test_set_rgb_failure_does_not_crash_handle():
    d = make_daemon()
    d.client.fail_next = 1
    assert d.handle("flash") == "ok"
    assert d.handle("yellow") == "ok"
    assert d.client.calls == [COLORS["yellow"]]


def test_quit_sets_mode_idle_without_emitting():
    d = make_daemon()
    d.handle("flash")
    before = list(d.client.calls)
    d.handle("quit")
    assert d.mode == "idle"
    assert d.client.calls == before
