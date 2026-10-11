import socket

import pytest


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_the_guard_refuses_a_non_loopback_ip_address(method: str) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(AssertionError, match="tried to reach"),
    ):
        getattr(sock, method)(("192.0.2.1", 9))


def test_the_guard_leaves_unix_sockets_alone(tmp_path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        assert sock.connect_ex(str(tmp_path / "none")) != 0  # refused by the OS, not the guard
