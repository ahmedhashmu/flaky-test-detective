"""A real socket race producing real connection errors.

A server thread binds and starts listening after a jittery delay while a client
connects with a short timeout. Sometimes the listener is ready and sometimes it is
not, so the client genuinely gets ECONNREFUSED or a socket timeout.

Everything stays on the loopback interface: no external hosts, no credentials, no
internet access required.
"""

from __future__ import annotations

import random
import socket
import threading
import time

from _demo import DETERMINISTIC, slack


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_client_connects_once_server_is_listening() -> None:
    """Genuine startup race against a loopback listener."""
    port = _free_port()
    ready = threading.Event()

    def serve() -> None:
        # Drawn to straddle the client's patience below, so the listener is ready
        # in time roughly half the runs.
        delay = 0.0 if DETERMINISTIC else random.uniform(0.0, 0.030)
        time.sleep(delay)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind(("127.0.0.1", port))
            except OSError:
                return
            server.listen(1)
            ready.set()
            server.settimeout(1.0)
            try:
                connection, _ = server.accept()
            except (TimeoutError, OSError):
                return
            connection.close()

    listener = threading.Thread(target=serve, daemon=True)
    listener.start()

    if DETERMINISTIC:
        ready.wait(timeout=5.0)

    # Client waits ~10ms, then allows another ~5ms. The server's 0-30ms delay
    # therefore succeeds about half the time.
    time.sleep(slack(0.010, 0.0))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(slack(0.005, 5.0))
        try:
            client.connect(("127.0.0.1", port))
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            raise AssertionError(
                f"connection refused to 127.0.0.1:{port} -- server was not listening "
                f"yet ({type(exc).__name__})"
            ) from exc

    listener.join(timeout=2.0)
