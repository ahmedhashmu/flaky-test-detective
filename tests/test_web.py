"""The dashboard API and server.

Two things get the most attention here. First, that the dashboard cannot disagree with
the CLI: both read the same analysis, and a UI that quietly showed a different verdict
would be worse than no UI. Second, the security posture -- this serves local test data
over HTTP, so containment and headers are asserted rather than assumed.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from flaky_detective import web
from flaky_detective.analysis import analyze
from flaky_detective.config import Config
from flaky_detective.models import Status
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.storage import Storage
from flaky_detective.web import api


def populate(db: Path) -> None:
    """A history containing one flake, one break, one stable test and a polluter."""
    with Storage(db) as store:
        for index in range(12):
            flaky_failed = index % 2 == 0
            store.add_run(
                Run(
                    run_uid=f"run-{index}",
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                    outcomes=(
                        Outcome(
                            test_id="t.py::test_polluter",
                            name="test_polluter",
                            status=Status.PASSED,
                            position=0 if flaky_failed else 9,
                        ),
                        Outcome(
                            test_id="t.py::test_flaky",
                            name="test_flaky",
                            status=Status.FAILED if flaky_failed else Status.PASSED,
                            message="TimeoutError: timed out after 30s" if flaky_failed else None,
                            signature="TimeoutError: timed out after <DURATION>"
                            if flaky_failed
                            else None,
                            position=1 if flaky_failed else 2,
                        ),
                        Outcome(
                            test_id="t.py::test_broken",
                            name="test_broken",
                            status=Status.FAILED,
                            message="ImportError: nope",
                            signature="ImportError: nope",
                            position=3,
                        ),
                        Outcome(
                            test_id="t.py::test_stable",
                            name="test_stable",
                            status=Status.PASSED,
                            position=4,
                        ),
                    ),
                    commit_sha="c1" if index < 6 else "c2",
                    branch="main",
                    runner="pytest",
                    duration=12.0,
                )
            )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    db = tmp_path / "history.db"
    populate(db)
    return Config(db_path=db, quarantine_path=tmp_path / "q.json")


@pytest.fixture
def client(config: Config) -> Iterator[str]:
    """A live server on an ephemeral port, torn down afterwards."""
    server = web.serve(config, host="127.0.0.1", port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=15) as response:  # noqa: S310
        return json.loads(response.read())


def get_raw(base: str, path: str):
    return urllib.request.urlopen(f"{base}{path}", timeout=15)  # noqa: S310


class TestOverviewPayload:
    def test_agrees_with_the_cli_analysis(self, config: Config) -> None:
        """The dashboard must never show a verdict the terminal would not."""
        with Storage(config.db_path) as store:
            payload = api.overview_payload(store, config)
            expected = analyze(store.outcomes(), config)

        assert len(payload["tests"]) == len(expected.tests)
        for row, test in zip(payload["tests"], expected.tests, strict=True):
            assert row["test_id"] == test.test_id
            assert row["verdict"] == str(test.verdict)
            assert row["score"] == test.score

    def test_rows_carry_the_counts_behind_the_score(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            payload = api.overview_payload(store, config)
        row = payload["tests"][0]
        for key in ("runs", "passes", "failures", "flips", "divergent_commits", "confidence"):
            assert key in row

    def test_trust_components_sum_to_the_deduction(self, config: Config) -> None:
        """The payload has to carry the arithmetic, not just its result.

        `deducted` is what lets a reader add up the penalties on screen and land on
        the score exactly. Without it the display is half a point of rounding away
        from checkable, which for this particular number is the whole argument.
        """
        with Storage(config.db_path) as store:
            trust = api.overview_payload(store, config)["trust"]

        deducted = sum(component["penalty"] for component in trust["components"])
        assert trust["deducted"] == pytest.approx(deducted, abs=0.05)
        assert trust["score"] == max(0, min(100, round(100 - trust["deducted"])))

    def test_wasted_time_carries_its_assumption(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            wasted = api.overview_payload(store, config)["trust"]["wasted_ci"]
        assert wasted["is_estimate"] is True
        assert "assumption" in wasted
        assert len(wasted["assumption"]) > 40

    def test_verdict_tone_covers_every_verdict(self) -> None:
        from flaky_detective.models import Verdict

        for verdict in Verdict:
            assert verdict in api.VERDICT_TONE

    def test_short_history_is_flagged(self, tmp_path: Path) -> None:
        """The UI must repeat the caveats the CLI prints, not quietly drop them."""
        db = tmp_path / "small.db"
        with Storage(db) as store:
            store.add_run(
                Run(
                    run_uid="only",
                    started_at="2026-08-01T00:00:00+00:00",
                    outcomes=(Outcome(test_id="t::a", name="a", status=Status.PASSED, position=0),),
                    commit_sha="c1",
                )
            )
        config = Config(db_path=db, quarantine_path=tmp_path / "q.json")
        with Storage(db) as store:
            payload = api.overview_payload(store, config)
        assert any(c["title"] == "Short history" for c in payload["caveats"])

    def test_missing_commit_data_is_flagged(self, tmp_path: Path) -> None:
        db = tmp_path / "nocommit.db"
        with Storage(db) as store:
            for index in range(12):
                store.add_run(
                    Run(
                        run_uid=f"r{index}",
                        started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                        outcomes=(
                            Outcome(
                                test_id="t::a",
                                name="a",
                                status=Status.FAILED if index % 2 else Status.PASSED,
                                position=0,
                            ),
                        ),
                    )
                )
        config = Config(db_path=db, quarantine_path=tmp_path / "q.json")
        with Storage(db) as store:
            payload = api.overview_payload(store, config)
        assert any(c["title"] == "No commit data" for c in payload["caveats"])


class TestDetailPayload:
    def test_separates_proof_from_inference(self, config: Config) -> None:
        """The central idea of the investigation page.

        A measured fact and a pattern match must not look alike, or the weaker one
        borrows the authority of the stronger.
        """
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_flaky")

        assert detail is not None
        labels = [item["label"] for item in detail["evidence"]["proven"]]
        assert "Same-commit divergence" in labels
        assert detail["evidence"]["inferred"]

    def test_includes_a_timeline_of_every_run(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_flaky")
        assert detail is not None
        assert len(detail["timeline"]) == 12
        assert {point["status"] for point in detail["timeline"]} == {"passed", "failed"}

    def test_includes_blame(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_flaky")
        assert detail is not None
        assert detail["blame"]["attribution"]
        assert detail["blame"]["explanation"]

    def test_neighbours_show_both_sides(self, config: Config) -> None:
        """So a polluter claim can be checked rather than believed."""
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_flaky")
        assert detail is not None
        for row in detail["neighbours"]:
            assert "before_failure" in row
            assert "before_pass" in row

    def test_actions_are_real_commands(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_flaky")
        assert detail is not None
        assert detail["actions"]
        for action in detail["actions"]:
            assert action["command"].startswith("flaky ")

    def test_break_is_told_not_to_re_run(self, config: Config) -> None:
        with Storage(config.db_path) as store:
            detail = api.test_detail_payload(store, config, "t.py::test_broken")
        assert detail is not None
        assert any("human" in action["label"] for action in detail["actions"])

    def test_unknown_test_returns_none(self, config: Config) -> None:
        """None rather than an empty record, so the caller can answer 404."""
        with Storage(config.db_path) as store:
            assert api.test_detail_payload(store, config, "t::nope") is None


class TestHttp:
    def test_health_endpoint(self, client: str) -> None:
        assert get_json(client, "/api/health")["status"] == "ok"

    def test_overview_endpoint(self, client: str) -> None:
        payload = get_json(client, "/api/overview")
        assert payload["api_version"] == api.API_VERSION
        assert payload["tests"]

    def test_detail_endpoint(self, client: str) -> None:
        quoted = urllib.parse.quote("t.py::test_flaky", safe="")
        payload = get_json(client, f"/api/tests/{quoted}")
        assert payload["test"]["test_id"] == "t.py::test_flaky"

    def test_unknown_test_is_404(self, client: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as caught:
            get_json(client, "/api/tests/nope")
        assert caught.value.code == 404

    def test_unknown_endpoint_is_404(self, client: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as caught:
            get_json(client, "/api/nonsense")
        assert caught.value.code == 404

    def test_serves_the_spa(self, client: str) -> None:
        with get_raw(client, "/") as response:
            assert response.status == 200
            assert b'<div id="root">' in response.read()

    def test_client_routes_fall_through_to_index(self, client: str) -> None:
        """A hard refresh on /tests/... must not 404."""
        with get_raw(client, "/tests/anything") as response:
            assert response.status == 200
            assert b'<div id="root">' in response.read()


class TestSecurity:
    def test_sets_a_content_security_policy(self, client: str) -> None:
        """Failure messages are untrusted text rendered in a browser."""
        with get_raw(client, "/") as response:
            csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp

    def test_sets_nosniff(self, client: str) -> None:
        with get_raw(client, "/") as response:
            assert response.headers["X-Content-Type-Options"] == "nosniff"

    @pytest.mark.parametrize(
        "attempt",
        [
            "/../pyproject.toml",
            "/../../etc/passwd",
            "/assets/../../../../etc/passwd",
            "/%2e%2e%2f%2e%2e%2fpyproject.toml",
            # Windows forms. `\` is a path separator there, and a drive-absolute
            # right-hand side makes pathlib's `/` DISCARD the left operand entirely,
            # so `STATIC_ROOT / "C:/Windows/win.ini"` is just the Windows path. The
            # containment check is the only thing standing between that and an
            # arbitrary file read, which makes it worth asserting on every platform
            # rather than only where it bites.
            "/..\\pyproject.toml",
            "/assets\\..\\..\\pyproject.toml",
            "/C:/Windows/win.ini",
            "/C:\\Windows\\win.ini",
        ],
    )
    def test_cannot_escape_the_asset_directory(self, client: str, attempt: str) -> None:
        """Traversal must fall through to index.html, never read outside static/."""
        try:
            with get_raw(client, attempt) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            assert exc.code in (400, 404)
            return

        assert b"[project]" not in body
        assert b"root:" not in body

    def test_defaults_to_loopback(self) -> None:
        """Binding anywhere else must be an explicit choice; there is no auth."""
        assert web.LOOPBACK == "127.0.0.1"

    def test_api_responses_are_not_cached(self, client: str) -> None:
        with get_raw(client, "/api/overview") as response:
            assert "no-store" in response.headers["Cache-Control"]


class TestServerLifecycle:
    def test_reports_a_port_clash_clearly(self, config: Config) -> None:
        first = web.serve(config, port=0, quiet=True)
        port = first.server_address[1]
        try:
            with pytest.raises(web.DashboardError, match="--port"):
                web.serve(config, port=port, quiet=True)
        finally:
            first.server_close()

    def test_is_built_reports_asset_presence(self) -> None:
        """The assets are committed so `flaky serve` works without a Node toolchain."""
        assert web.is_built() is (web.STATIC_ROOT / "index.html").is_file()

    def test_static_assets_are_shipped(self) -> None:
        assert web.is_built(), "Built dashboard missing. Run `npm ci && npm run build` in web/."


class TestBundleIntegrity:
    """The compiled bundle is committed, so it can go stale without anything failing.

    A stale or half-built bundle produces a blank page rather than an error, which is
    among the worst failure modes to debug. These checks make it a test failure instead.
    """

    def test_index_references_an_existing_chunk(self) -> None:
        """A dangling script src renders a blank page with no console error worth reading."""
        import re

        html = (web.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        referenced = re.findall(r'src="([^"]+\.js)"', html)
        assert referenced, "index.html has no module script"

        for src in referenced:
            asset = web.STATIC_ROOT / src.lstrip("./")
            assert asset.is_file(), f"index.html points at a missing chunk: {src}"

    def test_bundle_contains_the_ui(self) -> None:
        """Guards against a bundle built from an older source tree."""
        js = "".join(
            path.read_text(encoding="utf-8") for path in (web.STATIC_ROOT / "assets").glob("*.js")
        )
        assert js, "no JS chunks found"

        for marker in (
            "CI Trust Score",
            "Ranked tests",
            "Proven by the detector",
            "Inferred, weaker",
            "api/overview",
        ):
            assert marker in js, f"bundle is missing {marker!r} -- rebuild with npm run build"

    def test_bundle_makes_no_external_requests(self) -> None:
        """The dashboard must work offline, and the CSP forbids third-party origins."""
        js = "".join(
            path.read_text(encoding="utf-8") for path in (web.STATIC_ROOT / "assets").glob("*.js")
        )
        for origin in ("//fonts.googleapis.com", "//cdn.jsdelivr.net", "//unpkg.com"):
            assert origin not in js, f"bundle reaches out to {origin}"

    def test_served_chunk_has_a_javascript_content_type(self, client: str) -> None:
        """A wrong content type plus nosniff means the browser refuses to run it."""
        import re

        html = (web.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        src = re.findall(r'src="([^"]+\.js)"', html)[0].lstrip("./")
        with get_raw(client, f"/{src}") as response:
            assert response.status == 200
            assert "javascript" in response.headers["Content-Type"]
