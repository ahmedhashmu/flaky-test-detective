"""Tests for the one-command demo.

Two things have to hold, and they pull against each other.

The demo must be *convincing*: a dashboard full of `test_flaky_4_p70` teaches nobody
anything, and printing the generated failure rate in the test name shows the answer key
next to the verdict.

The demo must be *honest*: the history is generated, so the tool has to say so where it
cannot be missed. A demo mistaken for real results would cost more credibility than the
demo buys attention.

So the tests here check that the data is real engine output, that the labels do not leak,
and that the "this is generated" marker survives all the way to the dashboard payload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flaky_detective import demo
from flaky_detective.analysis import analyze
from flaky_detective.config import Config
from flaky_detective.models import DEMO_RUNNER, Status, Verdict
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.storage import Storage
from flaky_detective.web import api


@pytest.fixture
def built(tmp_path: Path) -> tuple[Storage, demo.DemoSummary]:
    store = Storage(tmp_path / "demo.db")
    summary = demo.build(store, runs=24)
    return store, summary


class TestBuilding:
    def test_writes_runs_and_results(self, built: tuple[Storage, demo.DemoSummary]) -> None:
        store, summary = built
        assert summary.runs == 24
        assert summary.results > 0
        assert store.run_count() == 24
        assert store.result_count() == summary.results

    def test_is_deterministic_from_the_seed(self, tmp_path: Path) -> None:
        """A walkthrough or a screenshot has to be reproducible."""

        def outcomes_for(name: str, seed: int) -> list[tuple[str, str]]:
            with Storage(tmp_path / name) as store:
                demo.build(store, seed=seed, runs=12)
                return [(o.test_id, str(o.status)) for o in store.outcomes()]

        assert outcomes_for("a.db", 7) == outcomes_for("b.db", 7)
        assert outcomes_for("c.db", 8) != outcomes_for("a.db", 7)

    def test_rebuilding_adds_nothing(self, built: tuple[Storage, demo.DemoSummary]) -> None:
        """Runs are content-addressed, so a second build is a no-op rather than a double."""
        store, _ = built
        again = demo.build(store, runs=24)
        assert again.runs == 0
        assert again.is_empty
        assert store.run_count() == 24

    def test_every_run_is_stamped_as_demo_data(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        assert store.stats().runners == {DEMO_RUNNER: 24}

    def test_runs_carry_durations_so_the_wasted_time_estimate_works(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        durations = [run.duration for run in store.recent_runs(limit=50)]
        assert all(d is not None and d > 0 for d in durations)

    def test_runs_carry_commits_so_divergence_is_observable(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        """Without commit SHAs the demo would show the tool's weaker signal only."""
        store, _ = built
        assert all(run.commit_sha for run in store.recent_runs(limit=50))


class TestTheDetectorReachesRealVerdicts:
    def test_every_category_the_tool_can_report_is_present(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        """The point of the demo: nothing on the dashboard should be empty.

        And these verdicts are not asserted into the data -- they come from the shipped
        analysis over recorded outcomes, so a broken detector fails this test.
        """
        store, _ = built
        report = analyze(store.outcomes(), Config())

        assert len(report.flaky) >= 4
        assert len(report.regressions) >= 1
        assert len(report.broken) >= 1
        assert len(report.fixed) >= 1
        assert any(t.verdict is Verdict.STABLE for t in report.tests)

    def test_an_order_dependent_flake_names_its_polluter(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        report = analyze(store.outcomes(), Config())

        ordered = [t for t in report.flaky if t.order and t.order.likely_polluter]
        assert ordered, "the demo must contain a diagnosable order-dependent flake"
        victim = ordered[0]
        assert victim.order is not None
        assert victim.order.likely_polluter != victim.test_id

    def test_flakes_have_same_commit_divergence_to_show(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        report = analyze(store.outcomes(), Config())
        assert any(t.divergent_commits > 0 for t in report.flaky)

    def test_the_broken_test_is_never_called_flaky(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        """The demo must not showcase the tool's worst failure mode."""
        store, _ = built
        report = analyze(store.outcomes(), Config())
        for test in report.broken:
            assert test.passes == 0
            assert test.verdict is not Verdict.FLAKY


class TestNamesDoNotLeakTheAnswerKey:
    def test_no_test_id_states_its_own_label(self, built: tuple[Storage, demo.DemoSummary]) -> None:
        """`test_flaky_4_p70` would print the ground truth beside the verdict."""
        store, _ = built
        ids = {o.test_id for o in store.outcomes()}

        for leak in (
            "test_flaky_",
            "test_victim_",
            "test_polluter_",
            "test_broken_",
            "test_regression_",
            "test_stable_",
            "test_fixed_",
        ):
            assert not any(leak in test_id for test_id in ids), leak

    def test_ids_read_like_a_real_suite(self, built: tuple[Storage, demo.DemoSummary]) -> None:
        store, _ = built
        ids = {o.test_id for o in store.outcomes()}
        assert "tests/test_registry.py::test_expects_clean_registry" in ids
        assert "tests/test_checkout.py::test_total_includes_tax" in ids

    def test_relabelling_keeps_ids_unique(self, tmp_path: Path) -> None:
        """Two tests sharing an id would silently merge in analysis.

        Checked with more tests than there are names in the pools, which is the case that
        would collide.
        """
        with Storage(tmp_path / "big.db") as store:
            demo.build(
                store,
                runs=8,
                population={
                    "flaky": 20,
                    "stable": 30,
                    "broken": 3,
                    "regression": 3,
                    "fixed": 3,
                    "order_dependent": 5,
                },
            )
            report = analyze(store.outcomes(), Config())

        ids = [t.test_id for t in report.tests]
        assert len(ids) == len(set(ids))

    def test_the_victim_and_polluter_stay_paired_through_relabelling(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        report = analyze(store.outcomes(), Config())
        victims = [t for t in report.flaky if t.order and t.order.likely_polluter]
        assert victims

        known = {t.test_id for t in report.tests}
        for victim in victims:
            assert victim.order is not None
            # The named polluter has to be a test that actually exists, or the
            # investigation page would point at nothing.
            assert victim.order.likely_polluter in known


class TestRefusingToClobberRealHistory:
    def test_a_fresh_database_is_safe(self, tmp_path: Path) -> None:
        with Storage(tmp_path / "fresh.db") as store:
            assert not demo.contains_real_history(store)

    def test_a_database_of_demo_runs_is_still_safe(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        assert not demo.contains_real_history(store)

    def test_a_database_with_real_runs_is_refused(self, tmp_path: Path) -> None:
        """`flaky demo --db .flaky.db` must not bury a team's accumulated history."""
        with Storage(tmp_path / "real.db") as store:
            store.add_run(
                Run(
                    run_uid="real-1",
                    started_at="2026-08-01T00:00:00+00:00",
                    runner="pytest",
                    outcomes=(Outcome(test_id="t.py::a", name="a", status=Status.PASSED),),
                )
            )
            assert demo.contains_real_history(store)


class TestTheDashboardSaysItIsGenerated:
    def test_the_payload_is_marked_and_carries_a_caveat(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        payload = api.overview_payload(store, Config())

        assert payload["is_demo"] is True
        titles = [c["title"] for c in payload["caveats"]]
        assert "Demo data" in titles

    def test_the_caveat_is_first_so_it_cannot_be_missed(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        store, _ = built
        payload = api.overview_payload(store, Config())
        assert payload["caveats"][0]["title"] == "Demo data"

    def test_the_caveat_says_the_verdicts_are_real(
        self, built: tuple[Storage, demo.DemoSummary]
    ) -> None:
        """The distinction that keeps the demo honest without undselling the tool."""
        store, _ = built
        payload = api.overview_payload(store, Config())
        detail = next(c["detail"] for c in payload["caveats"] if c["title"] == "Demo data")
        assert "generated" in detail
        assert "same analysis the CLI runs" in detail

    def test_a_real_database_gets_no_demo_caveat(self, tmp_path: Path) -> None:
        with Storage(tmp_path / "real.db") as store:
            store.add_run(
                Run(
                    run_uid="real-1",
                    started_at="2026-08-01T00:00:00+00:00",
                    runner="pytest",
                    outcomes=(Outcome(test_id="t.py::a", name="a", status=Status.PASSED),),
                )
            )
            payload = api.overview_payload(store, Config())

        assert payload["is_demo"] is False
        assert "Demo data" not in [c["title"] for c in payload["caveats"]]
