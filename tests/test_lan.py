import json

from govee.lan import LanClient


class FakeUDPSocket:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


def test_set_rgb_sends_lan_packet():
    fake = FakeUDPSocket()
    c = LanClient(device_ip="10.0.0.42", socket_factory=lambda: fake)
    c.set_rgb(0xFF0000)

    assert len(fake.sent) == 1
    data, addr = fake.sent[0]
    assert addr == ("10.0.0.42", 4003)
    payload = json.loads(data)
    assert payload["msg"]["cmd"] == "colorwc"
    assert payload["msg"]["data"]["color"] == {"r": 255, "g": 0, "b": 0}
    assert payload["msg"]["data"]["colorTemInKelvin"] == 0


def test_set_rgb_close_socket():
    fake = FakeUDPSocket()
    c = LanClient(device_ip="10.0.0.42", socket_factory=lambda: fake)
    c.set_rgb(0x00FFFF)
    assert fake.closed is True
