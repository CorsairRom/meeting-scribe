import socket

import pytest

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket connection to a non-loopback address fails the test (spec 13)."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(sock: socket.socket, address: object) -> None:
        # Unix sockets (the private D-Bus) are local by nature: only IP sockets are checked.
        is_ip = sock.family in {socket.AF_INET, socket.AF_INET6} and isinstance(address, tuple)
        if is_ip and address[0] not in _LOOPBACK:
            raise AssertionError(f"a test tried to reach {address[0]!r}")

    def connect(self: socket.socket, address: object) -> None:
        check(self, address)
        real_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object) -> int:
        check(self, address)
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
