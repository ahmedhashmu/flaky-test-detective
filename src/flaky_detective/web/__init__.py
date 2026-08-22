"""Local dashboard server.

`http.server` from the standard library rather than a framework, for the same reason
the rest of the tool takes no dependencies it can avoid: this is a developer tool for a
side-concern, and asking someone to install a web stack to look at their test history
is how a tool goes uninstalled.

**Security posture.** The server binds to 127.0.0.1 by default and has no
authentication, because it is a single-user local viewer -- the same trust model as
`python -m http.server` in a project directory. It exposes test names, failure messages
and commit SHAs from the local database. Binding to any other interface therefore
requires `--host` to be passed explicitly, and doing so publishes that data to the
network with no access control. There is a warning on that path.

Read-only. Nothing here mutates the database, the quarantine list or the filesystem; the
dashboard surfaces commands for the user to run rather than running them.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..config import Config
from ..quarantine import Quarantine
from ..storage import Storage, StorageError
from . import api

STATIC_ROOT = Path(__file__).parent / "static"
"""Built dashboard assets, shipped as package data.

Committed so that `flaky serve` works from a fresh install with no Node toolchain. The
source lives in `web/` at the repository root; see docs/dashboard.md.
"""

LOOPBACK = "127.0.0.1"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

CACHE_SECONDS = 2
"""Analyses are cached briefly so a page with several panels does not re-analyze the
whole database once per panel. Short enough that a fresh ingest shows up promptly."""


class DashboardError(RuntimeError):
    """The server cannot start. A usage error, reported before binding."""


class _Server(ThreadingHTTPServer):
    """Threading server that stays quiet when a client goes away.

    A browser that navigates mid-request, or a client that closes early, otherwise
    dumps a traceback to stderr. That is noise, not a fault, and printing it trains the
    reader to ignore the terminal -- which matters because real errors are reported
    there too.
    """

    daemon_threads = True

    allow_reuse_address = os.name != "nt"
    """False on Windows, and that difference is not cosmetic.

    `HTTPServer` sets this to 1 so a restart is not rejected while the previous socket sits
    in TIME_WAIT, which is the right trade on POSIX. Windows gives SO_REUSEADDR *different*
    semantics: it permits binding a port another socket is actively LISTENing on. So `flaky
    serve --port 8080` against an occupied port silently started a second server, and two
    processes then raced for the same port with requests landing on whichever won.

    Found by adding windows-latest to CI -- `test_reports_a_port_clash_clearly` reported
    DID NOT RAISE. The clash detection at `_build_server` was correct all along; the socket
    option was quietly preventing the clash from happening.
    """

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys

        kind = sys.exc_info()[0]
        if kind is not None and issubclass(
            kind, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)
        ):
            return
        super().handle_error(request, client_address)


def is_built() -> bool:
    """Whether the compiled dashboard is present."""
    return (STATIC_ROOT / "index.html").is_file()


class _Cache:
    """Tiny time-based cache around the analysis.

    Not an optimization for its own sake: without it, opening the dashboard runs the
    full analysis several times in a second, which on a large database is visible.
    """

    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self._seconds = seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, build: Callable[[], Any]) -> Any:
        import time

        now = time.monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if cached and now - cached[0] < self._seconds:
                return cached[1]

        value = build()
        with self._lock:
            self._entries[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the JSON API and the compiled single-page app."""

    server_version = "flaky-test-detective"
    sys_version = ""

    def __init__(
        self,
        *args: Any,
        config: Config,
        cache: _Cache,
        quiet: bool = False,
        **kwargs: Any,
    ) -> None:
        self._config = config
        self._cache = cache
        self._quiet = quiet
        super().__init__(*args, **kwargs)

    # -- routing --------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path.startswith("/api/"):
            self._serve_api(path)
            return

        self._serve_static(path)

    def _serve_api(self, path: str) -> None:
        try:
            if path == "/api/overview":
                self._json(self._overview())
                return

            if path == "/api/health":
                self._json({"api_version": api.API_VERSION, "status": "ok"})
                return

            if path.startswith("/api/tests/"):
                test_id = unquote(path[len("/api/tests/") :])
                payload = self._test_detail(test_id)
                if payload is None:
                    self._error(HTTPStatus.NOT_FOUND, f"No test matching {test_id!r}")
                    return
                self._json(payload)
                return

            self._error(HTTPStatus.NOT_FOUND, f"No such endpoint: {path}")
        except StorageError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except FileNotFoundError:
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"No database at {self._config.db_path}. Record some runs first.",
            )

    # -- handlers -------------------------------------------------------------

    def _overview(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            with Storage(self._config.db_path) as store:
                return api.overview_payload(store, self._config, quarantine=self._quarantine())

        return dict(self._cache.get("overview", build))

    def _test_detail(self, test_id: str) -> dict[str, Any] | None:
        def build() -> dict[str, Any] | None:
            with Storage(self._config.db_path) as store:
                return api.test_detail_payload(
                    store, self._config, test_id, quarantine=self._quarantine()
                )

        return self._cache.get(f"test:{test_id}", build)

    def _quarantine(self) -> Quarantine | None:
        try:
            return Quarantine(self._config.quarantine_path)
        except ValueError:
            # A malformed quarantine file should degrade the panel, not the dashboard.
            return None

    # -- static ---------------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        if not is_built():
            self._html_message(
                "Dashboard not built",
                "The compiled dashboard is missing from this install. Build it with "
                "<code>npm ci &amp;&amp; npm run build</code> in <code>web/</code>, or use "
                "<code>flaky report --format html</code> for a standalone report.",
            )
            return

        relative = path.lstrip("/") or "index.html"
        target = (STATIC_ROOT / relative).resolve()

        # Containment check: a crafted path must not escape the asset directory.
        if not target.is_relative_to(STATIC_ROOT.resolve()) or not target.is_file():
            # Unknown paths fall through to index.html so client-side routes work on
            # a hard refresh.
            target = STATIC_ROOT / "index.html"

        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        if target.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        else:
            # Vite fingerprints asset filenames, so they are safe to cache hard.
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    # -- helpers --------------------------------------------------------------

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message, "status": int(status)}, status)

    def _html_message(self, title: str, detail: str) -> None:
        body = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>"
            "body{font:15px/1.6 ui-sans-serif,system-ui,sans-serif;background:#0f1115;"
            "color:#e5e7eb;display:grid;place-items:center;height:100vh;margin:0}"
            "main{max-width:44rem;padding:2rem}h1{font-size:1.3rem}"
            "code{background:#1c2029;padding:.15rem .35rem;border-radius:4px}"
            "</style></head><body><main>"
            f"<h1>{title}</h1><p>{detail}</p></main></body></html>"
        ).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        """Defensive headers.

        Failure messages come from test output, so they are untrusted text being
        rendered in a browser. React escapes by default, and a restrictive CSP means a
        mistake there cannot become script execution.
        """
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def log_message(self, format: str, *args: Any) -> None:  # signature fixed upstream
        if not self._quiet:
            super().log_message(format, *args)


def serve(
    config: Config,
    *,
    host: str = LOOPBACK,
    port: int = 8420,
    quiet: bool = False,
) -> _Server:
    """Create the dashboard server. Caller runs it.

    Returned rather than run so tests can drive it on an ephemeral port and shut it
    down cleanly.
    """
    handler = partial(DashboardHandler, config=config, cache=_Cache(), quiet=quiet)
    try:
        return _Server((host, port), handler)
    except OSError as exc:
        raise DashboardError(
            f"Cannot listen on {host}:{port}: {exc}. Try --port with another number."
        ) from exc


__all__ = ["STATIC_ROOT", "DashboardError", "DashboardHandler", "api", "is_built", "serve"]
