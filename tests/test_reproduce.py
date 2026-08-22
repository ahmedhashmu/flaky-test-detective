"""Tests for the reproducer: delta debugging, and the search built on it.

Every test here uses a fake oracle. That is the point of the module's shape: the search
makes hundreds of decisions and each real decision costs a suite run, so a test suite that
exercised it for real would take hours and still only cover one project's behaviour. With
an injected oracle the known-answer cases are exact and the whole file runs in
milliseconds.

The end-to-end check that the printed command actually reproduces a real failure lives in
CI against `examples/flaky_demo`, where there is a genuine order-dependent flake to find.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest

from flaky_detective.models import ReproduceOutcome, Reproduction
from flaky_detective.report import reproduction as reproduction_report
from flaky_detective.reproduce import (
    ReproduceError,
    TrialBatch,
    check_command,
    ddmin,
    estimate_cost,
    reproduce,
)

VICTIM = "tests/test_a.py::test_victim"
PYTEST = ["pytest"]


@dataclass
class FakeRunner:
    """A test command that never runs anything.

    `rule(sequence, trials) -> failures` decides the outcome, so a case can state its
    ground truth exactly instead of hoping a real suite produces it.
    """

    rule: Callable[[tuple[str, ...], int], int]
    calls: list[tuple[tuple[str, ...], int]] = field(default_factory=list)

    def __call__(self, sequence: Sequence[str], trials: int) -> TrialBatch:
        seq = tuple(sequence)
        self.calls.append((seq, trials))
        return TrialBatch(failures=self.rule(seq, trials), trials=trials)

    @property
    def sequences(self) -> list[tuple[str, ...]]:
        return [seq for seq, _ in self.calls]


def needs(*culprits: str) -> Callable[[tuple[str, ...], int], int]:
    """Victim fails every trial exactly when all `culprits` are present."""

    def rule(sequence: tuple[str, ...], trials: int) -> int:
        return trials if all(c in sequence for c in culprits) else 0

    return rule


def candidates(count: int, culprit_at: int | None = None, culprit: str = "culprit") -> list[str]:
    names = [f"t{index:02d}" for index in range(count)]
    if culprit_at is not None:
        names[culprit_at] = culprit
    return names


class TestDdmin:
    def test_finds_a_single_culprit(self) -> None:
        items = candidates(16, culprit_at=9)
        result = ddmin(items, lambda seq: "culprit" in seq)
        assert result.subset == ("culprit",)
        assert not result.exhausted

    def test_finds_a_two_element_conjunction(self) -> None:
        """The case correlation cannot express: neither test alone breaks the victim."""
        items = candidates(24)
        items[3], items[17] = "left", "right"

        result = ddmin(items, lambda seq: "left" in seq and "right" in seq)

        assert set(result.subset) == {"left", "right"}
        assert result.subset == ("left", "right"), "execution order must be preserved"

    def test_reduces_to_one_when_anything_reproduces(self) -> None:
        result = ddmin(candidates(8), lambda seq: bool(seq))
        assert len(result.subset) == 1

    def test_returns_the_full_set_when_nothing_smaller_works(self) -> None:
        """All eight needed together. There is nothing to remove, and saying so is right."""
        items = candidates(8)
        result = ddmin(items, lambda seq: len(seq) == len(items))
        assert result.subset == tuple(items)

    def test_preserves_input_order(self) -> None:
        items = ["z", "culprit", "a", "m", "b", "culprit2", "c", "d"]
        result = ddmin(items, lambda seq: "culprit" in seq and "culprit2" in seq)
        assert list(result.subset) == [i for i in items if i in set(result.subset)]

    def test_counts_its_oracle_calls(self) -> None:
        calls = 0

        def oracle(sequence: Sequence[str]) -> bool:
            nonlocal calls
            calls += 1
            return "culprit" in sequence

        result = ddmin(candidates(16, culprit_at=5), oracle)
        assert result.calls == calls
        assert result.calls < 16, "delta debugging should beat trying each one in turn"

    def test_stops_at_the_budget(self) -> None:
        result = ddmin(candidates(64, culprit_at=63), lambda seq: "culprit" in seq, budget=3)
        assert result.exhausted
        assert result.calls == 3
        assert len(result.subset) > 1

    def test_is_deterministic(self) -> None:
        items = candidates(32, culprit_at=20)
        first = ddmin(items, lambda seq: "culprit" in seq)
        second = ddmin(items, lambda seq: "culprit" in seq)
        assert first == second

    def test_handles_a_single_candidate(self) -> None:
        result = ddmin(["only"], lambda seq: True)
        assert result.subset == ("only",)
        assert result.calls == 0, "nothing to try when there is nothing to remove"

    def test_handles_no_candidates(self) -> None:
        result = ddmin([], lambda seq: True)
        assert result.subset == ()


class TestReproduceOrderDependence:
    def test_isolates_the_polluter(self) -> None:
        suspects = candidates(20, culprit_at=11, culprit="polluter")
        runner = FakeRunner(needs("polluter"))

        result = reproduce(VICTIM, PYTEST, suspects, trials=20, search_trials=3, runner=runner)

        assert result.outcome is ReproduceOutcome.REPRODUCED
        assert result.reproduced
        assert result.sequence == ("polluter",)
        assert result.failures == 20
        assert result.trials == 20
        assert result.control_failures == 0
        assert result.candidates_started == 20

    def test_command_puts_the_victim_last(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM, PYTEST, ["a", "polluter", "b"], trials=6, search_trials=2, runner=runner
        )

        assert result.command.endswith(VICTIM)
        assert "polluter" in result.command
        assert result.command.index("polluter") < result.command.index(VICTIM)

    def test_command_disables_order_randomization(self) -> None:
        """Without this the printed command shuffles the sequence it is meant to pin."""
        runner = FakeRunner(needs("polluter"))
        result = reproduce(VICTIM, PYTEST, ["polluter"], trials=4, search_trials=2, runner=runner)
        assert "-p no:randomly" in result.command

    def test_finds_a_pair_no_correlation_could_name(self) -> None:
        suspects = candidates(16)
        suspects[2], suspects[13] = "left", "right"
        runner = FakeRunner(needs("left", "right"))

        result = reproduce(VICTIM, PYTEST, suspects, trials=10, search_trials=2, runner=runner)

        assert result.outcome is ReproduceOutcome.REPRODUCED
        assert set(result.sequence) == {"left", "right"}

    def test_measures_the_control_before_searching(self) -> None:
        runner = FakeRunner(needs("polluter"))
        reproduce(VICTIM, PYTEST, ["polluter"], trials=7, search_trials=2, runner=runner)
        assert runner.calls[0] == ((), 7), "the victim must be run alone first"

    def test_accounts_for_every_suite_run(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            PYTEST,
            candidates(8, culprit_at=3, culprit="polluter"),
            trials=10,
            search_trials=2,
            runner=runner,
        )
        assert result.suite_runs == sum(trials for _, trials in runner.calls)

    def test_reports_the_reduction(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            PYTEST,
            candidates(12, culprit_at=6, culprit="polluter"),
            trials=6,
            search_trials=2,
            runner=runner,
        )
        assert result.reduction == "12 candidates reduced to 1"

    def test_confirms_at_full_trials_not_search_trials(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM, PYTEST, ["a", "polluter"], trials=25, search_trials=2, runner=runner
        )
        assert result.trials == 25
        assert runner.calls[-1][1] == 25


class TestReproduceNonAnswers:
    def test_a_test_that_fails_alone_is_not_blamed_on_a_prefix(self) -> None:
        """The failure mode worth guarding: any prefix "reproduces" a self-failing test."""
        runner = FakeRunner(lambda seq, trials: round(0.5 * trials))

        result = reproduce(
            VICTIM, PYTEST, candidates(12), trials=20, search_trials=4, runner=runner
        )

        assert result.outcome is ReproduceOutcome.FAILS_ALONE
        assert result.sequence == ()
        assert result.command == ""
        assert "on its own" in result.explanation

    def test_a_stable_test_reports_nothing_found(self) -> None:
        runner = FakeRunner(lambda seq, trials: 0)
        result = reproduce(
            VICTIM, PYTEST, candidates(10), trials=12, search_trials=3, runner=runner
        )

        assert result.outcome is ReproduceOutcome.NOT_REPRODUCED
        assert result.sequence == ()
        assert "not the order of these tests" in result.explanation

    def test_stops_after_the_control_and_one_check(self) -> None:
        """A negative answer must be cheap, or nobody will ask the question."""
        runner = FakeRunner(lambda seq, trials: 0)
        reproduce(VICTIM, PYTEST, candidates(40), trials=10, search_trials=3, runner=runner)
        assert len(runner.calls) == 2

    def test_no_recorded_predecessors(self) -> None:
        runner = FakeRunner(lambda seq, trials: 0)
        result = reproduce(VICTIM, PYTEST, [], trials=8, runner=runner)

        assert result.outcome is ReproduceOutcome.NOT_REPRODUCED
        assert "no ordering to isolate" in result.explanation

    def test_no_predecessors_but_fails_alone(self) -> None:
        runner = FakeRunner(lambda seq, trials: trials)
        result = reproduce(VICTIM, PYTEST, [], trials=8, runner=runner)
        assert result.outcome is ReproduceOutcome.FAILS_ALONE

    def test_excludes_the_victim_from_its_own_candidates(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            PYTEST,
            [VICTIM, "polluter", VICTIM],
            trials=6,
            search_trials=2,
            runner=runner,
        )
        assert VICTIM not in result.sequence
        assert result.candidates_started == 1

    def test_deduplicates_candidates(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM, PYTEST, ["a", "a", "polluter", "a"], trials=6, search_trials=2, runner=runner
        )
        assert result.candidates_started == 2

    def test_a_lucky_reduction_is_not_published(self) -> None:
        """Accepted at 2 search trials, gone at 20. Reporting it would waste an afternoon."""

        def rule(sequence: tuple[str, ...], trials: int) -> int:
            if "polluter" not in sequence:
                return 0
            return 1 if trials <= 2 else 0

        result = reproduce(
            VICTIM,
            PYTEST,
            ["a", "polluter"],
            trials=20,
            search_trials=2,
            runner=FakeRunner(rule),
        )

        assert result.outcome is ReproduceOutcome.NOT_REPRODUCED
        assert "was luck" in result.explanation
        assert result.sequence, "the sequence is still reported so the claim can be checked"

    def test_budget_exhaustion_reports_a_working_but_unminimized_sequence(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            PYTEST,
            candidates(64, culprit_at=63, culprit="polluter"),
            trials=6,
            search_trials=1,
            budget=2,
            runner=runner,
        )

        assert result.outcome is ReproduceOutcome.BUDGET_EXHAUSTED
        assert len(result.sequence) > 1
        assert "may not be minimal" in result.explanation
        assert result.command, "a non-minimal reproducer is still a reproducer"


class TestControlRateGate:
    def test_a_clear_increase_over_a_nonzero_control_is_accepted(self) -> None:
        def rule(sequence: tuple[str, ...], trials: int) -> int:
            if not sequence:
                return round(0.1 * trials)
            return trials if "polluter" in sequence else round(0.1 * trials)

        result = reproduce(
            VICTIM,
            PYTEST,
            ["a", "polluter", "b"],
            trials=20,
            search_trials=3,
            runner=FakeRunner(rule),
        )

        assert result.outcome is ReproduceOutcome.REPRODUCED
        assert result.sequence == ("polluter",)
        assert result.control_failures == 2

    def test_a_marginal_increase_over_a_nonzero_control_is_refused(self) -> None:
        """2 of 3 against a 50% control is not evidence, and must not become a command."""

        def rule(sequence: tuple[str, ...], trials: int) -> int:
            if not sequence:
                return round(0.5 * trials)
            return round(0.67 * trials)

        result = reproduce(
            VICTIM, PYTEST, ["a", "b"], trials=20, search_trials=3, runner=FakeRunner(rule)
        )

        assert result.outcome is ReproduceOutcome.FAILS_ALONE

    def test_any_failure_counts_when_the_control_is_clean(self) -> None:
        def rule(sequence: tuple[str, ...], trials: int) -> int:
            return 1 if "polluter" in sequence else 0

        result = reproduce(
            VICTIM,
            PYTEST,
            ["a", "polluter"],
            trials=20,
            search_trials=3,
            runner=FakeRunner(rule),
        )

        assert result.outcome is ReproduceOutcome.REPRODUCED
        assert result.failures == 1


class TestCommandPreparation:
    def test_rejects_a_non_pytest_runner(self) -> None:
        with pytest.raises(ReproduceError, match="pytest-only"):
            reproduce(VICTIM, ["go", "test", "./..."], ["a"], runner=FakeRunner(needs("a")))

    def test_rejects_an_empty_command(self) -> None:
        with pytest.raises(ReproduceError, match="No command given"):
            reproduce(VICTIM, [], ["a"], runner=FakeRunner(needs("a")))

    def test_strips_test_selection_and_says_so(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            ["pytest", "tests/", "tests/test_a.py"],
            ["polluter"],
            trials=4,
            search_trials=2,
            runner=runner,
        )

        assert "tests/" in result.explanation
        assert "tests/" not in result.command.split()[:2]
        assert result.outcome is ReproduceOutcome.REPRODUCED

    def test_keeps_flags(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            ["pytest", "-x", "--tb=short", "tests/"],
            ["polluter"],
            trials=4,
            search_trials=2,
            runner=runner,
        )

        assert "-x" in result.command
        assert "--tb=short" in result.command

    def test_accepts_python_m_pytest(self) -> None:
        runner = FakeRunner(needs("polluter"))
        result = reproduce(
            VICTIM,
            ["python", "-m", "pytest"],
            ["polluter"],
            trials=4,
            search_trials=2,
            runner=runner,
        )
        assert result.command.startswith("python -m pytest")

    def test_victim_never_ran_is_a_usage_error(self) -> None:
        """A renamed test must produce an explanation, never a confident 'stable'."""

        def missing(sequence: Sequence[str], trials: int) -> TrialBatch:
            return TrialBatch(failures=0, trials=trials, missing=trials)

        with pytest.raises(ReproduceError, match="did not run in any"):
            reproduce(VICTIM, PYTEST, ["a"], trials=5, runner=missing)

    def test_check_command_reports_a_missing_binary(self) -> None:
        problem = check_command(["pytest-that-does-not-exist"])
        assert problem is not None
        assert "not found on PATH" in problem

    def test_check_command_rejects_a_non_pytest_runner(self) -> None:
        with pytest.raises(ReproduceError):
            check_command(["cargo", "test"])


class TestCostEstimate:
    def test_grows_with_candidates(self) -> None:
        small = estimate_cost(4, trials=20, search_trials=3)
        large = estimate_cost(64, trials=20, search_trials=3)
        assert large > small

    def test_includes_the_control_and_the_confirmation(self) -> None:
        assert estimate_cost(0, trials=20, search_trials=3) == 20

    def test_is_in_the_right_ballpark(self) -> None:
        """The real 15-candidate search on examples/flaky_demo used 45 suite runs."""
        assert 30 <= estimate_cost(15, trials=12, search_trials=3) <= 80


class TestReproductionModel:
    def test_rates(self) -> None:
        result = Reproduction(
            test_id=VICTIM,
            outcome=ReproduceOutcome.REPRODUCED,
            failures=18,
            trials=20,
            control_failures=1,
            control_trials=20,
        )
        assert result.failure_rate == 0.9
        assert result.control_rate == 0.05
        assert result.reproduced

    def test_rates_with_no_trials(self) -> None:
        result = Reproduction(test_id=VICTIM, outcome=ReproduceOutcome.UNSUPPORTED)
        assert result.failure_rate == 0.0
        assert result.control_rate == 0.0
        assert result.reduction == ""

    def test_reduction_needs_both_numbers(self) -> None:
        result = Reproduction(
            test_id=VICTIM,
            outcome=ReproduceOutcome.REPRODUCED,
            sequence=("a",),
            candidates_started=0,
        )
        assert result.reduction == ""


def _reproduced() -> Reproduction:
    return Reproduction(
        test_id=VICTIM,
        outcome=ReproduceOutcome.REPRODUCED,
        sequence=("tests/test_a.py::test_polluter",),
        failures=19,
        trials=20,
        control_trials=20,
        candidates_started=31,
        oracle_calls=9,
        suite_runs=86,
        command="pytest -p no:randomly tests/test_a.py::test_polluter " + VICTIM,
        explanation="31 candidates reduced to 1 in 9 suite experiments.",
    )


class TestReproductionReport:
    @pytest.mark.parametrize("outcome", list(ReproduceOutcome))
    def test_console_renders_every_outcome(self, outcome: ReproduceOutcome) -> None:
        from rich.console import Console

        console = Console(file=None, record=True, width=100)
        reproduction_report.render_console(
            Reproduction(test_id=VICTIM, outcome=outcome, control_trials=5, explanation="x"),
            console,
        )
        assert VICTIM in console.export_text()

    def test_console_shows_the_command(self) -> None:
        from rich.console import Console

        console = Console(record=True, width=200)
        reproduction_report.render_console(_reproduced(), console)
        text = console.export_text()
        assert "Run this" in text
        assert "test_polluter" in text
        assert "19/20" in text

    def test_markdown_puts_the_command_in_a_fence(self) -> None:
        rendered = reproduction_report.render_markdown(_reproduced())
        assert "```sh" in rendered
        assert "test_polluter" in rendered
        assert "| In this order | 19/20 failed (95%) |" in rendered

    def test_markdown_numbers_the_sequence_with_the_victim_last(self) -> None:
        rendered = reproduction_report.render_markdown(_reproduced())
        assert "1. `tests/test_a.py::test_polluter`" in rendered
        assert f"2. `{VICTIM}`" in rendered

    def test_json_carries_the_evidence(self) -> None:
        import json

        payload = json.loads(reproduction_report.render_json(_reproduced()))
        assert payload["outcome"] == "reproduced"
        assert payload["reproduced"] is True
        assert payload["failure_rate"] == 0.95
        assert payload["control_rate"] == 0.0
        assert payload["sequence"] == ["tests/test_a.py::test_polluter"]
        assert payload["suite_runs"] == 86

    def test_render_rejects_an_unknown_format(self) -> None:
        with pytest.raises(ValueError, match="Unknown format"):
            reproduction_report.render(_reproduced(), "yaml")

    def test_markdown_omits_the_fence_when_there_is_no_command(self) -> None:
        rendered = reproduction_report.render_markdown(
            Reproduction(
                test_id=VICTIM,
                outcome=ReproduceOutcome.FAILS_ALONE,
                failures=8,
                trials=20,
                control_failures=8,
                control_trials=20,
                explanation="It fails on its own.",
            )
        )
        assert "```" not in rendered
        assert "Fails on its own" in rendered
