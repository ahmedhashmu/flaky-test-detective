"""The CI Trust Score.

The score's only justification is that it can be taken apart. So the load-bearing test
here is not any particular number -- it is that the component penalties sum exactly to
`deducted`, and the headline score is that deduction subtracted from 100 and rounded.
Nothing else sits in between. A score with an unexplained remainder would be the same
unexaminable verdict this tool exists to replace.
"""

from __future__ import annotations

import pytest

from flaky_detective.analysis import analyze
from flaky_detective.analysis.health import (
    MAX_BREAK_PENALTY,
    MAX_COVERAGE_PENALTY,
    MAX_FLAKE_PENALTY,
    MAX_QUARANTINE_PENALTY,
    median_run_duration,
    trust_score,
)
from flaky_detective.config import Config

from conftest import sequence


def report_for(*specs: tuple[str, str, list[str] | None]):
    outcomes = []
    for test_id, pattern, commits in specs:
        outcomes += sequence(test_id, pattern, commits=commits)
    return analyze(outcomes, Config())


class TestExplainability:
    def test_components_sum_to_the_deduction(self) -> None:
        """The property the whole score rests on."""
        report = report_for(
            ("t.py::flaky", ".F" * 8, ["c1"] * 16),
            ("t.py::broken", "F" * 16, ["c1"] * 16),
            ("t.py::stable", "." * 16, ["c1"] * 16),
        )
        score = trust_score(report, quarantine_days_outstanding=20)

        deducted = sum(component.penalty for component in score.components)
        assert score.deducted == pytest.approx(deducted)
        assert score.score == round(100 - deducted)

    def test_deducted_is_exact_while_the_score_is_rounded(self) -> None:
        """The displayed score is whole; the audit trail is not.

        Adding up the penalties on screen can land up to half a point away from
        `100 - score`. `deducted` is what makes that gap checkable as rounding rather
        than leaving it looking like an undisclosed adjustment.
        """
        report = report_for(
            ("t.py::flaky", ".F" * 9, ["c1"] * 18),
            ("t.py::flaky2", ".FF" * 6, ["c1"] * 18),
        )
        score = trust_score(report)

        assert not float(score.deducted).is_integer(), "pick inputs with a fractional penalty"
        assert score.deducted == pytest.approx(sum(c.penalty for c in score.components))
        assert abs(score.deducted - (100 - score.score)) <= 0.5

    def test_every_component_explains_itself(self) -> None:
        report = report_for(("t.py::flaky", ".F" * 8, ["c1"] * 16))
        for component in trust_score(report).components:
            assert component.name
            assert len(component.detail) > 15, component.name
            assert component.weight > 0

    def test_healthy_components_deduct_nothing(self) -> None:
        report = report_for(("t.py::stable", "." * 20, ["c1"] * 20))
        score = trust_score(report)
        assert score.score == 100
        assert all(component.is_healthy for component in score.components)
        assert score.penalties == ()

    def test_score_is_bounded(self) -> None:
        """Every penalty maxed out must still land inside 0-100."""
        report = report_for(
            *[(f"t.py::flaky{i}", ".F" * 10, None) for i in range(40)],
            *[(f"t.py::broken{i}", "F" * 20, None) for i in range(10)],
        )
        score = trust_score(report, quarantine_days_outstanding=9999)
        assert 0 <= score.score <= 100


class TestBands:
    @pytest.mark.parametrize(
        ("score_value", "expected"),
        [
            (100, "healthy"),
            (90, "healthy"),
            (89, "fair"),
            (75, "fair"),
            (74, "poor"),
            (50, "poor"),
            (49, "critical"),
            (0, "critical"),
        ],
    )
    def test_band_thresholds(self, score_value: int, expected: str) -> None:
        from flaky_detective.models import TrustScore

        assert TrustScore(score=score_value, components=()).band == expected


class TestWeighting:
    def test_a_break_costs_more_than_a_flake(self) -> None:
        """A suite with one real regression is less trustworthy than one with a known
        flake, because the flake is known."""
        flaky = trust_score(report_for(("t.py::a", ".F" * 10, ["c1"] * 20)))
        broken = trust_score(report_for(("t.py::a", "F" * 20, ["c1"] * 20)))
        assert broken.score < flaky.score

    def test_a_mild_flake_costs_less_than_a_severe_one(self) -> None:
        """Counting flakes alone would treat 1-in-20 the same as 10-in-20."""
        spread = [c for c in ("c1", "c2", "c3", "c4", "c5") for _ in range(4)]
        mild = trust_score(report_for(("t.py::a", "..................F.", spread)))
        severe = trust_score(report_for(("t.py::a", ".F" * 10, ["c1"] * 20)))
        assert mild.score > severe.score

    def test_missing_commit_data_costs_points(self) -> None:
        """Not a style complaint: the false alarm rate measurably rises without it."""
        with_commits = trust_score(report_for(("t.py::a", ".F" * 10, ["c1"] * 20)))
        without = trust_score(report_for(("t.py::a", ".F" * 10, None)))

        coverage = next(c for c in without.components if c.name == "Commit evidence")
        assert coverage.penalty == pytest.approx(MAX_COVERAGE_PENALTY)
        assert next(c for c in with_commits.components if c.name == "Commit evidence").is_healthy

    def test_quarantine_debt_costs_points(self) -> None:
        report = report_for(("t.py::a", "." * 20, ["c1"] * 20))
        clean = trust_score(report, quarantine_days_outstanding=0)
        indebted = trust_score(report, quarantine_days_outstanding=30)
        assert indebted.score < clean.score
        assert (
            "verify" in next(c for c in indebted.components if c.name == "Quarantine debt").detail
        )

    def test_weights_are_declared(self) -> None:
        """Each component states its own ceiling so a reader can see its influence."""
        report = report_for(("t.py::a", ".F" * 8, ["c1"] * 16))
        weights = {c.name: c.weight for c in trust_score(report).components}
        assert weights["Flaky tests"] == MAX_FLAKE_PENALTY
        assert weights["Unresolved breaks"] == MAX_BREAK_PENALTY
        assert weights["Commit evidence"] == MAX_COVERAGE_PENALTY
        assert weights["Quarantine debt"] == MAX_QUARANTINE_PENALTY


class TestFacts:
    def test_counts_match_the_report(self) -> None:
        report = report_for(
            ("t.py::flaky", ".F" * 8, ["c1"] * 16),
            ("t.py::broken", "F" * 16, ["c1"] * 16),
            ("t.py::stable1", "." * 16, ["c1"] * 16),
            ("t.py::stable2", "." * 16, ["c1"] * 16),
        )
        score = trust_score(report)
        assert score.total_tests == 4
        assert score.active_flakes == 1
        assert score.unresolved_breaks == 1
        assert score.stable_tests == 2
        assert score.stable_share == 0.5

    def test_fixed_tests_count_as_stable(self) -> None:
        """A test that was flaky and is now stable should not be held against the suite."""
        report = report_for(("t.py::a", ".F.F" + "." * 14, ["c1"] * 18))
        score = trust_score(report)
        assert score.stable_tests == 1


class TestWastedTime:
    def test_estimated_from_failures_and_duration(self) -> None:
        report = report_for(("t.py::a", ".F" * 10, ["c1"] * 20))
        score = trust_score(report, median_run_seconds=30.0)
        assert score.flaky_failures == 10
        assert score.wasted_ci_seconds == pytest.approx(300.0)
        assert score.wasted_ci_minutes == pytest.approx(5.0)

    def test_zero_without_durations(self) -> None:
        """No duration data means no estimate, not a guessed one."""
        report = report_for(("t.py::a", ".F" * 10, ["c1"] * 20))
        assert trust_score(report).wasted_ci_seconds == 0.0

    def test_always_labelled_an_estimate(self) -> None:
        """It is the most quotable number in the product and the least defensible."""
        report = report_for(("t.py::a", ".F" * 10, ["c1"] * 20))
        assert trust_score(report, median_run_seconds=10.0).wasted_ci_is_estimate is True

    def test_median_ignores_missing_durations(self) -> None:
        assert median_run_duration([0, 0.0, 10.0, 20.0, 30.0]) == 20.0
        assert median_run_duration([]) == 0.0
        assert median_run_duration([0, 0]) == 0.0


class TestEdgeCases:
    def test_empty_report(self) -> None:
        score = trust_score(analyze([], Config()))
        assert score.total_tests == 0
        assert score.stable_share == 0.0
        assert 0 <= score.score <= 100
