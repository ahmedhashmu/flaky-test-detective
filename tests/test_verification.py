"""Tests for fix verification.

The interesting cases are all the ways a clean streak is *not* proof:

- too short for the old failure rate;
- clean because the situation that used to fail never occurred;
- clean here while the same change broke something else;
- clean because the test stopped running at all.

A tool that says "fixed" on any of those puts a flake back into the trusted set, and the
next time it fails everyone reads the failure as new breakage.
"""

from __future__ import annotations

import json

import pytest

from flaky_detective.analysis import analyze
from flaky_detective.analysis import compare as compare_reports
from flaky_detective.analysis.ordering import build_ordering_index
from flaky_detective.analysis.verification import (
    MIN_AFTER_RUNS,
    MIN_EXPOSURES,
    count_exposures,
    verify_fix,
)
from flaky_detective.config import Config
from flaky_detective.models import FixOutcome, FixVerification
from flaky_detective.models import TestAnalysis as Analysis
from flaky_detective.report import verification as verification_report

from conftest import outcome, sequence


def analysis_for(pattern: str, *, test_id: str = "t.py::a", commits: bool = True) -> Analysis:
    shas = ["c1"] * len(pattern) if commits else None
    report = analyze(sequence(test_id, pattern, commits=shas), Config())
    return next(t for t in report.tests if t.test_id == test_id)


class TestFixed:
    def test_a_long_clean_streak_after_real_flakiness_is_a_fix(self) -> None:
        before = analysis_for(".F" * 20)
        after = analysis_for("." * 40)
        result = verify_fix(before, after)

        assert result.outcome is FixOutcome.FIXED
        assert result.is_fixed
        assert result.clean_runs == 40
        assert result.probability < 0.05

    def test_the_explanation_cites_the_old_proof(self) -> None:
        result = verify_fix(analysis_for(".F" * 20), analysis_for("." * 40))
        assert "same commit" in result.explanation
        assert "proven, not guessed" in result.explanation

    def test_rate_reduction_and_avoided_failures_are_reported(self) -> None:
        before = analysis_for(".F" * 20)
        after = analysis_for("." * 40)
        result = verify_fix(before, after)

        assert result.rate_reduction == pytest.approx(0.5)
        # At the old 50% rate, 40 runs would have produced about 20 failures.
        assert result.failures_avoided == 20

    def test_runs_needed_scales_with_the_old_rate(self) -> None:
        """A rare flake needs a much longer streak than a frequent one."""
        frequent = verify_fix(analysis_for("F." * 20), analysis_for("." * 60))
        rare = verify_fix(analysis_for("F" + "." * 79), analysis_for("." * 300))
        assert frequent.runs_needed < 20 < rare.runs_needed


class TestNotEnough:
    def test_a_short_streak_is_inconclusive_not_fixed(self) -> None:
        before = analysis_for(".F" * 20)
        after = analysis_for("." * 3)
        result = verify_fix(before, after)

        assert result.outcome is FixOutcome.INCONCLUSIVE
        assert not result.is_fixed
        assert str(MIN_AFTER_RUNS) in result.explanation

    def test_a_streak_shorter_than_required_says_how_many_are_needed(self) -> None:
        before = analysis_for("F" + "." * 79)  # a rare flake: 1 in 80
        after = analysis_for("." * 20)
        result = verify_fix(before, after)

        assert result.outcome is FixOutcome.INCONCLUSIVE
        assert result.runs_needed > 20
        assert str(result.runs_needed) in result.explanation

    def test_still_failing_is_not_fixed(self) -> None:
        before = analysis_for(".F" * 20)
        after = analysis_for("...F...F" * 5)
        result = verify_fix(before, after)

        assert result.outcome is FixOutcome.NOT_FIXED
        assert "Still failing" in result.explanation

    def test_partial_improvement_is_acknowledged_without_being_a_fix(self) -> None:
        before = analysis_for("F." * 20)
        after = analysis_for("." * 36 + "F...")
        result = verify_fix(before, after)

        assert result.outcome is FixOutcome.NOT_FIXED
        assert "may mean the fix helped" in result.explanation

    def test_nothing_to_verify_when_the_before_window_is_clean(self) -> None:
        result = verify_fix(analysis_for("." * 40), analysis_for("." * 40))
        assert result.outcome is FixOutcome.INCONCLUSIVE
        assert "nothing to verify" in result.explanation


class TestFailingConditionsMustBeExercised:
    def test_a_clean_streak_without_the_polluter_proves_nothing(self) -> None:
        """The check that makes the rest worth anything.

        If the test only failed after a polluter, and the polluter barely ran ahead of it,
        the clean streak is not evidence about the fix -- the failing situation was hardly
        attempted. Reporting a fix here would be a confident wrong answer.
        """
        before = _order_dependent_before()
        assert before.order is not None and before.order.likely_polluter

        after = analysis_for("." * 50)
        result = verify_fix(before, after, polluter_exposures=1)

        assert result.outcome is FixOutcome.INCONCLUSIVE
        assert not result.exposures_sufficient
        assert "barely attempted" in result.explanation
        assert str(MIN_EXPOSURES) in result.explanation

    def test_a_clean_streak_with_the_polluter_exercised_is_a_fix(self) -> None:
        before = _order_dependent_before()
        after = analysis_for("." * 50)
        result = verify_fix(before, after, polluter_exposures=MIN_EXPOSURES + 5)

        assert result.outcome is FixOutcome.FIXED
        assert result.exposures_sufficient
        assert "failing sequence was exercised" in result.explanation

    def test_exposures_are_not_required_when_order_was_never_the_cause(self) -> None:
        before = analysis_for(".F" * 20)
        assert before.order is None
        result = verify_fix(before, analysis_for("." * 40), polluter_exposures=None)

        assert result.outcome is FixOutcome.FIXED
        assert result.exposures_sufficient
        assert result.polluter is None


class TestCollateralDamage:
    def test_a_fix_that_breaks_something_else_is_not_a_fix(self) -> None:
        before_report = analyze(
            sequence("t.py::target", ".F" * 20, commits=["c1"] * 40)
            + sequence("t.py::other", "." * 40, commits=["c1"] * 40),
            Config(),
        )
        after_report = analyze(
            sequence("t.py::target", "." * 40, commits=["c1"] * 40)
            + sequence("t.py::other", ".F" * 20, commits=["c1"] * 40),
            Config(),
        )
        collateral = compare_reports(before_report, after_report)

        before = next(t for t in before_report.tests if t.test_id == "t.py::target")
        after = next(t for t in after_report.tests if t.test_id == "t.py::target")
        result = verify_fix(before, after, collateral=collateral)

        assert result.outcome is FixOutcome.INCONCLUSIVE
        assert "t.py::other" in result.collateral
        assert "moves the problem" in result.explanation

    def test_the_test_under_verification_is_not_its_own_collateral(self) -> None:
        before_report = analyze(sequence("t.py::a", ".F" * 20, commits=["c1"] * 40), Config())
        after_report = analyze(sequence("t.py::a", "." * 40, commits=["c1"] * 40), Config())
        collateral = compare_reports(before_report, after_report)

        before = next(t for t in before_report.tests if t.test_id == "t.py::a")
        after = next(t for t in after_report.tests if t.test_id == "t.py::a")
        result = verify_fix(before, after, collateral=collateral)

        assert result.collateral == ()
        assert result.outcome is FixOutcome.FIXED


class TestExposureCounting:
    def test_counts_only_runs_where_the_polluter_ran_first(self) -> None:
        from flaky_detective.models import Status

        outcomes = []
        for run_index in range(6):
            run = f"r{run_index}"
            first = "t.py::polluter" if run_index % 2 == 0 else "t.py::other"
            outcomes.append(outcome(first, Status.PASSED, run=run, commit="c1", position=0))
            outcomes.append(
                outcome("t.py::victim", Status.PASSED, run=run, commit="c1", position=1)
            )

        ordering = build_ordering_index(outcomes)
        assert count_exposures("t.py::victim", "t.py::polluter", outcomes, ordering) == 3

    def test_returns_zero_when_the_polluter_never_preceded_it(self) -> None:
        outcomes = sequence("t.py::victim", "...", commits=["c1"] * 3)
        ordering = build_ordering_index(outcomes)
        assert count_exposures("t.py::victim", "t.py::nobody", outcomes, ordering) == 0


class TestRendering:
    @pytest.fixture
    def fixed(self) -> FixVerification:
        return verify_fix(analysis_for(".F" * 20), analysis_for("." * 40))

    @pytest.fixture
    def unresolved(self) -> FixVerification:
        return verify_fix(analysis_for("F" + "." * 79), analysis_for("." * 20))

    def test_console_shows_both_bars_and_the_outcome(self, fixed: FixVerification) -> None:
        import io

        from rich.console import Console

        console = Console(file=io.StringIO(), width=120)
        verification_report.render_console(fixed, console)
        text = console.file.getvalue()  # type: ignore[union-attr]

        assert "Fixed" in text
        assert "Before" in text and "After" in text
        assert "clean runs needed" in text
        assert "\u2588" in text  # the bar was drawn

    def test_console_tells_an_unresolved_case_what_to_do_next(
        self, unresolved: FixVerification
    ) -> None:
        import io

        from rich.console import Console

        console = Console(file=io.StringIO(), width=120)
        verification_report.render_console(unresolved, console)
        text = console.file.getvalue()  # type: ignore[union-attr]

        assert "Cannot say yet" in text
        assert "flaky verify" in text

    def test_a_tiny_probability_is_not_rendered_as_zero(self) -> None:
        """0.0% reads as a rounding artefact, not as the strongest evidence available."""
        result = verify_fix(analysis_for("F." * 30), analysis_for("." * 80))
        assert result.probability < 0.0001
        assert "under 0.01%" in verification_report.render_markdown(result)
        assert "0.0%" not in result.explanation

    def test_markdown_carries_the_before_after_table(self, fixed: FixVerification) -> None:
        rendered = verification_report.render_markdown(fixed)
        assert "Fix verification" in rendered
        assert "| Before |" in rendered
        assert "| After |" in rendered
        assert "Clean runs needed" in rendered

    def test_json_labels_the_estimate_as_an_estimate(self, fixed: FixVerification) -> None:
        payload = json.loads(verification_report.render_json(fixed))
        assert payload["outcome"] == "fixed"
        assert payload["is_fixed"] is True
        assert payload["failures_avoided_is_estimate"] is True
        assert payload["runs_needed"] > 0
        assert payload["before"]["failures"] == 20
        assert payload["after"]["failures"] == 0

    def test_unknown_format_is_rejected(self, fixed: FixVerification) -> None:
        with pytest.raises(ValueError, match="Unknown format"):
            verification_report.render(fixed, "yaml")


def _order_dependent_before() -> Analysis:
    """A history where the victim fails exactly when the polluter ran immediately before.

    Built rather than hand-written so the order detector genuinely fires on it; asserting
    on a fixture the detector would reject would test nothing.
    """
    from flaky_detective.models import Status

    outcomes = []
    for run_index in range(16):
        run = f"r{run_index}"
        polluter_first = run_index % 2 == 0

        if polluter_first:
            outcomes.append(
                outcome("t.py::polluter", Status.PASSED, run=run, commit="c1", position=0)
            )
            victim_position = 1
            victim_status = Status.FAILED
        else:
            # A different, longer prefix on the clean runs, so the victim's position
            # actually varies. With a constant position the spread is zero and the order
            # detector declines -- correctly -- making the fixture untestable.
            outcomes.append(outcome("t.py::other", Status.PASSED, run=run, commit="c1", position=0))
            outcomes.append(
                outcome("t.py::filler", Status.PASSED, run=run, commit="c1", position=1)
            )
            victim_position = 2
            victim_status = Status.PASSED

        outcomes.append(
            outcome(
                "t.py::victim",
                victim_status,
                run=run,
                commit="c1",
                position=victim_position,
                message="AssertionError: registry not empty" if polluter_first else None,
            )
        )

    report = analyze(outcomes, Config())
    return next(t for t in report.tests if t.test_id == "t.py::victim")
