"""End-to-end pipeline against the real demo suite.

This is the test that proves the tool works on genuine nondeterminism rather than
on constructed data: it runs `examples/flaky_demo` for real, several times, and
checks what comes out.

**These assertions are deliberately one-sided.** The demo suite is genuinely
random, so "test_worker_finishes_within_deadline is flaky" is not guaranteed in any
particular sample. Asserting it would make this file flaky, which would be an
embarrassing thing to ship in a flaky-test detector.

So what is asserted here is only what must hold in every sample:

- the four stable controls always pass, so they must always score 0.00
- `test_known_broken` always fails, so it must never be called flaky
- the worst flake must outrank every control
- ingesting the same report twice must change nothing

The probabilistic behaviours (specific causes, order dependence) are covered
deterministically in test_ordering.py and test_classify.py instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flaky_detective import runner
from flaky_detective.analysis import analyze, triage
from flaky_detective.config import Config
from flaky_detective.ingest import ingest_paths
from flaky_detective.models import Verdict
from flaky_detective.storage import Storage

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "examples" / "flaky_demo"

ITERATIONS = 12

STABLE_CONTROLS = {
    "test_stable_sums_prices",
    "test_stable_applies_discount",
    "test_stable_handles_empty_basket",
    "test_stable_rejects_negative_total",
}

pytestmark = pytest.mark.integration


def demo_command() -> list[str]:
    return [sys.executable, "-m", "pytest", str(DEMO), "-p", "no:cacheprovider", "-q"]


@pytest.fixture(scope="module")
def hunted(tmp_path_factory: pytest.TempPathFactory):
    """Run the demo suite for real and analyze the result.

    Module-scoped: the hunt takes a few seconds and every test here can share it.
    """
    workdir = tmp_path_factory.mktemp("hunt")
    settings = Config(db_path=workdir / "history.db")

    plan = runner.plan_hunt(
        demo_command(),
        iterations=ITERATIONS,
        shuffle=True,
        cwd=REPO_ROOT,
        base_seed=4242,
        workdir=workdir,
    )

    with Storage(settings.db_path) as store:
        summary = runner.run_hunt(plan, store, settings)
        outcomes = store.outcomes()

    return plan, summary, analyze(outcomes, settings)


def short(test_id: str) -> str:
    return test_id.rsplit("::", 1)[-1]


def _hostile_sequence():
    """A flaky history whose failure message contains markup."""
    from flaky_detective.models import Status, TestOutcome

    payload = "<script>alert(1)</script>"
    return [
        TestOutcome(
            test_id="t.py::test_hostile",
            name="test_hostile",
            status=Status.FAILED if index % 2 else Status.PASSED,
            message=payload if index % 2 else None,
            signature=payload if index % 2 else None,
            position=0,
            run_uid=f"h{index}",
            commit_sha="c1",
            started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
        )
        for index in range(8)
    ]


class TestHuntMechanics:
    def test_the_runner_was_recognized(self, hunted) -> None:
        plan, _, _ = hunted
        assert plan.runner == "pytest"

    def test_order_randomization_is_actually_available(self, hunted) -> None:
        """pytest-randomly is a dev dependency precisely so this holds.

        If it stops holding, order-dependent flakes can never be provoked and the
        tool would be quietly less useful than it claims.
        """
        plan, _, _ = hunted
        assert plan.shuffle_effective
        assert plan.shuffle_template is not None

    def test_every_iteration_produced_a_report(self, hunted) -> None:
        _, summary, _ = hunted
        assert summary.collected == ITERATIONS
        assert summary.failed_to_collect == []

    def test_failing_tests_do_not_count_as_collection_failures(self, hunted) -> None:
        """A non-zero exit code is expected: failing tests are the point."""
        _, summary, _ = hunted
        assert all(i.exit_code != 0 for i in summary.iterations)
        assert all(i.ok for i in summary.iterations)

    def test_seeds_are_recorded_so_a_hunt_can_be_replayed(self, hunted) -> None:
        _, summary, _ = hunted
        seeds = [i.seed for i in summary.iterations]
        assert all(seeds)
        assert len(set(seeds)) == ITERATIONS


class TestDetection:
    def test_it_finds_flakes(self, hunted) -> None:
        _, _, report = hunted
        assert len(report.flaky) >= 4

    def test_stable_controls_score_zero(self, hunted) -> None:
        """The controls are what stop a tool that flags everything from looking good."""
        _, _, report = hunted
        controls = [t for t in report.tests if short(t.test_id) in STABLE_CONTROLS]
        assert len(controls) == len(STABLE_CONTROLS)
        for control in controls:
            assert control.score == 0.0, f"{control.test_id} should be stable"
            assert control.verdict is Verdict.STABLE
            assert control.failures == 0

    def test_worst_flake_outranks_every_control(self, hunted) -> None:
        _, _, report = hunted
        worst = max(t.score for t in report.tests)
        controls = max(t.score for t in report.tests if short(t.test_id) in STABLE_CONTROLS)
        assert worst > controls

    def test_consistent_failure_is_not_called_flaky(self, hunted) -> None:
        """The distinction that decides exit code 2 versus 1."""
        _, _, report = hunted
        broken = next(t for t in report.tests if short(t.test_id) == "test_known_broken")
        assert broken.verdict is not Verdict.FLAKY
        assert broken.verdict in (Verdict.BROKEN, Verdict.REGRESSION)
        assert broken.passes == 0

    def test_every_flake_has_evidence_behind_it(self, hunted) -> None:
        """No score may be unexplainable from the counts shown to the user."""
        _, _, report = hunted
        for test in report.flaky:
            assert test.runs > 0
            assert test.passes > 0
            assert test.failures > 0
            assert test.cause is not None

    def test_flakes_are_ranked_by_score(self, hunted) -> None:
        _, _, report = hunted
        scores = [t.score for t in report.tests]
        assert scores == sorted(scores, reverse=True)

    def test_the_polluter_itself_is_not_blamed(self, hunted) -> None:
        """`test_registers_session` always passes. It causes failures but has none."""
        _, _, report = hunted
        polluter = next(t for t in report.tests if short(t.test_id) == "test_registers_session")
        assert polluter.verdict is Verdict.STABLE


class TestReportRendering:
    @pytest.mark.parametrize("fmt", ["md", "json", "html"])
    def test_every_format_renders(self, hunted, fmt: str) -> None:
        from flaky_detective import report as report_module

        _, _, report = hunted
        assert report_module.render(report, fmt).strip()

    def test_json_round_trips(self, hunted) -> None:
        import json

        from flaky_detective.report import json_report

        _, _, report = hunted
        payload = json.loads(json_report.render_report(report))
        assert payload["summary"]["tests"] == len(report.tests)
        assert len(payload["tests"]) == len(report.tests)

    def test_html_makes_no_external_requests(self, hunted) -> None:
        from flaky_detective.report import html

        _, _, report = hunted
        rendered = html.render_report(report)
        for marker in ("http://", "https://", "cdn.", "<script"):
            assert marker not in rendered

    def test_html_escapes_hostile_failure_text(self) -> None:
        """Failure messages come from test output, so they are untrusted input.

        Constructed rather than taken from the hunt, because the demo suite has no
        reason to emit markup and a test that cannot fail proves nothing.
        """
        from flaky_detective.report import html

        outcomes = [
            *_hostile_sequence(),
        ]
        rendered = html.render_report(analyze(outcomes, Config()))
        assert "<script>alert(1)</script>" not in rendered
        assert "&lt;script&gt;" in rendered


class TestIdempotency:
    def test_reingesting_a_report_changes_nothing(self, tmp_path: Path) -> None:
        """CI retries re-present the same artifact; double-counting would corrupt
        every rate the tool computes."""
        import subprocess

        report_path = tmp_path / "run.xml"
        subprocess.run(
            [*demo_command(), f"--junitxml={report_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        assert report_path.is_file()

        settings = Config(db_path=tmp_path / "idem.db")
        with Storage(settings.db_path) as store:
            first = ingest_paths(store, [report_path])
            before = analyze(store.outcomes(), settings)

            second = ingest_paths(store, [report_path])
            after = analyze(store.outcomes(), settings)

        assert first.runs_added == 1
        assert second.runs_added == 0
        assert second.runs_skipped == 1
        assert before.total_runs == after.total_runs
        assert before.total_results == after.total_results
        assert [t.score for t in before.tests] == [t.score for t in after.tests]


class TestTriageAgainstHistory:
    def test_a_known_flake_is_not_treated_as_new(self, hunted, tmp_path: Path) -> None:
        import subprocess

        from flaky_detective.ingest import junit

        _, _, history = hunted
        report_path = tmp_path / "fresh.xml"
        subprocess.run(
            [*demo_command(), f"--junitxml={report_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )

        run = junit.parse_file(report_path)
        result = triage(list(run.outcomes), history, source=str(report_path))

        # test_known_broken always fails and is always in history as broken, so it
        # must land in the actionable bucket, never in known_flakes.
        actionable = {short(f.test_id) for f in result.actionable}
        assert "test_known_broken" in actionable
        assert "test_known_broken" not in {short(f.test_id) for f in result.known_flakes}

    def test_stable_controls_never_appear_as_failures(self, hunted, tmp_path: Path) -> None:
        import subprocess

        from flaky_detective.ingest import junit

        _, _, history = hunted
        report_path = tmp_path / "fresh2.xml"
        subprocess.run(
            [*demo_command(), f"--junitxml={report_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )

        run = junit.parse_file(report_path)
        result = triage(list(run.outcomes), history)
        reported = {
            short(f.test_id) for f in result.known_flakes + result.new_failures + result.regressions
        }
        assert not (reported & STABLE_CONTROLS)
