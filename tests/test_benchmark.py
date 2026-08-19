"""The accuracy harness.

A benchmark nobody checks is a benchmark that quietly starts measuring the wrong
thing. That already happened once: the first version of the generator gave every
order-dependent group the same positions and shared filler ids, so several tests
occupied one position per run, predecessor computation became arbitrary, and the
reported polluter precision was 0.000 while the detector was working perfectly. The
harness was measuring its own bug.

So these tests check the generator as carefully as the scorer.
"""

from __future__ import annotations

import pytest

from flaky_detective.benchmark import run_benchmark, sweep
from flaky_detective.benchmark.generate import FLAKE_RATES, Truth, generate_population
from flaky_detective.benchmark.score import score_predictions
from flaky_detective.models import Verdict


class TestGeneratorDeterminism:
    def test_same_seed_gives_identical_data(self) -> None:
        """An accuracy figure that cannot be re-derived is an anecdote."""
        first = generate_population(seed=42, runs=10)
        second = generate_population(seed=42, runs=10)
        assert [o.status for o in first.outcomes] == [o.status for o in second.outcomes]
        assert first.truths.keys() == second.truths.keys()

    def test_different_seeds_give_different_data(self) -> None:
        first = generate_population(seed=1, runs=20)
        second = generate_population(seed=2, runs=20)
        assert [o.status for o in first.outcomes] != [o.status for o in second.outcomes]

    def test_benchmark_is_reproducible(self) -> None:
        a = run_benchmark(seed=7, runs=20)
        b = run_benchmark(seed=7, runs=20)
        assert a.accuracy == b.accuracy
        assert a.false_alarm_rate == b.false_alarm_rate
        assert [(s.label, s.precision, s.recall) for s in a.labels] == [
            (s.label, s.precision, s.recall) for s in b.labels
        ]


class TestGeneratorHonesty:
    """The population has to contain the hard cases, or the numbers mean nothing."""

    def test_covers_every_label(self) -> None:
        population = generate_population(seed=1, runs=30)
        produced = {truth.truth for truth in population.truths.values()}
        assert produced == set(Truth)

    def test_spans_the_full_range_of_flake_rates(self) -> None:
        """Including 0.05, which is near-undetectable, and 0.9, which looks broken."""
        population = generate_population(seed=1, runs=30)
        rates = {
            truth.failure_rate for truth in population.truths.values() if truth.truth is Truth.FLAKY
        }
        assert rates == set(FLAKE_RATES)

    def test_positions_are_unique_within_a_run(self) -> None:
        """The bug that made polluter precision read 0.000.

        Predecessor computation sorts by position, so duplicate positions make
        "ran immediately before" arbitrary.
        """
        population = generate_population(seed=3, runs=6)
        by_run: dict[str, list[int]] = {}
        for outcome in population.outcomes:
            if outcome.position is None or outcome.run_uid is None:
                continue
            # Only the order-dependence groups model execution order; everything else
            # sits at position 0 by design and is never order-analyzed.
            if "test_order" not in outcome.test_id:
                continue
            by_run.setdefault(outcome.run_uid, []).append(outcome.position)

        for run_uid, positions in by_run.items():
            assert len(positions) == len(set(positions)), f"duplicate positions in {run_uid}"

    def test_order_dependent_victims_fail_only_after_their_polluter(self) -> None:
        population = generate_population(seed=5, runs=20)
        victims = [
            test_id
            for test_id, truth in population.truths.items()
            if truth.truth is Truth.ORDER_DEPENDENT
        ]
        assert victims

        for victim in victims:
            polluter = population.truths[victim].polluter
            assert polluter is not None
            for outcome in population.outcomes:
                if outcome.test_id != victim or not outcome.status.is_failure:
                    continue
                # In the failing layout the polluter sits immediately before.
                same_run = [
                    o
                    for o in population.outcomes
                    if o.run_uid == outcome.run_uid and o.position is not None
                ]
                earlier = [o for o in same_run if o.position < (outcome.position or 0)]
                assert any(o.test_id == polluter for o in earlier)

    def test_undetectable_flakes_are_marked_not_dropped(self) -> None:
        """A flake that never failed left no evidence; counting it as a miss would
        measure the tool against information it never had."""
        population = generate_population(seed=1234, runs=5)
        flaky = [t for t in population.truths.values() if t.truth is Truth.FLAKY]
        assert any(not t.detectable for t in flaky), "expected some at 5 runs"
        assert all(t.detectable for t in population.detectable.values())

    def test_regressions_include_a_noisy_variant(self) -> None:
        """The genuinely ambiguous case: flaky history, then a real break."""
        population = generate_population(seed=1, runs=30, regression=8)
        regressions = [
            test_id
            for test_id, truth in population.truths.items()
            if truth.truth is Truth.REGRESSION
        ]
        patterns = set()
        for test_id in regressions:
            sequence = [o for o in population.outcomes if o.test_id == test_id]
            early_failures = sum(
                1 for o in sequence[: int(len(sequence) * 0.7)] if o.status.is_failure
            )
            patterns.add(early_failures > 0)
        assert patterns == {True, False}, "expected both clean and noisy regressions"


class TestMeasuredQuality:
    """Assertions on the tool's actual accuracy.

    Bounds are deliberately looser than the numbers currently achieved. The point is
    to catch a real degradation, not to freeze today's figures and fail the build every
    time a threshold moves by a percent.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def result():
        return run_benchmark(seed=1234, runs=30)

    def test_never_calls_a_real_break_flaky(self, result) -> None:
        """The property the whole product rests on.

        Reporting a regression as flaky teaches the user to re-run instead of
        investigate, which is the habit the tool exists to break.
        """
        assert result.false_alarm_rate == 0.0, (
            f"{result.false_alarms} of {result.breaks_total} breaks were called flaky"
        )

    def test_finds_most_flakes(self, result) -> None:
        flaky = result.label("flaky")
        assert flaky is not None
        assert flaky.recall >= 0.7
        assert flaky.precision >= 0.9

    def test_identifies_stable_tests_perfectly(self, result) -> None:
        stable = result.label("stable")
        assert stable is not None
        assert stable.recall == 1.0

    def test_identifies_broken_tests(self, result) -> None:
        broken = result.label("broken")
        assert broken is not None
        assert broken.recall >= 0.9

    def test_rarely_mistakes_a_flake_for_a_break(self, result) -> None:
        assert result.missed_break_rate <= 0.15

    def test_names_the_right_polluter(self, result) -> None:
        assert result.polluter_recall >= 0.9
        assert result.polluter_precision >= 0.9

    @pytest.mark.parametrize("seed", [1, 7, 42, 99, 2026])
    def test_headline_holds_across_seeds(self, seed: int) -> None:
        """One favourable seed proves nothing."""
        result = run_benchmark(seed=seed, runs=30)
        assert result.false_alarm_rate == 0.0
        assert result.accuracy >= 0.85


class TestEvidenceDependence:
    """Accuracy should improve with evidence, and the design claims about which
    signal matters should be visible in the numbers."""

    def test_more_runs_do_not_make_it_worse(self) -> None:
        results = sweep(seed=1234, over="runs", values=(10, 30))
        assert results[-1].accuracy >= results[0].accuracy - 0.05

    def test_short_history_is_less_trustworthy(self) -> None:
        """Documented honestly rather than hidden: five runs is not enough."""
        short, long_ = sweep(seed=1234, over="runs", values=(5, 30))
        assert short.false_alarm_rate >= long_.false_alarm_rate

    def test_commit_data_is_the_load_bearing_signal(self) -> None:
        """The central design claim, checked against a measurement."""
        without, with_ = sweep(seed=1234, over="coverage", values=(0.0, 1.0))
        assert with_.false_alarm_rate < without.false_alarm_rate

    def test_unknown_sweep_axis_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="Cannot sweep"):
            sweep(over="phase-of-the-moon")


class TestScoring:
    def test_perfect_predictions(self) -> None:
        from flaky_detective.benchmark.generate import GroundTruth

        truths = {
            "a": GroundTruth("a", Truth.FLAKY),
            "b": GroundTruth("b", Truth.STABLE),
        }
        result = score_predictions(truths, {"a": Verdict.FLAKY, "b": Verdict.STABLE})
        assert result.accuracy == 1.0
        assert result.false_alarm_rate == 0.0

    def test_false_alarm_is_counted(self) -> None:
        from flaky_detective.benchmark.generate import GroundTruth

        truths = {"a": GroundTruth("a", Truth.REGRESSION)}
        result = score_predictions(truths, {"a": Verdict.FLAKY})
        assert result.false_alarms == 1
        assert result.false_alarm_rate == 1.0

    def test_missed_break_is_counted(self) -> None:
        from flaky_detective.benchmark.generate import GroundTruth

        truths = {"a": GroundTruth("a", Truth.FLAKY)}
        result = score_predictions(truths, {"a": Verdict.REGRESSION})
        assert result.missed_breaks == 1
        assert result.missed_break_rate == 1.0

    def test_undetectable_tests_are_excluded(self) -> None:
        from flaky_detective.benchmark.generate import GroundTruth

        truths = {"a": GroundTruth("a", Truth.FLAKY, detectable=False)}
        result = score_predictions(truths, {"a": Verdict.STABLE})
        assert result.total == 0
        assert result.undetectable == 1

    def test_a_missing_prediction_is_a_harness_bug_not_a_score(self) -> None:
        """Silently scoring it would let the harness drift from the tool."""
        from flaky_detective.benchmark.generate import GroundTruth

        with pytest.raises(ValueError, match="generated but not analyzed"):
            score_predictions({"a": GroundTruth("a", Truth.FLAKY)}, {})

    def test_f1_handles_the_zero_case(self) -> None:
        from flaky_detective.benchmark.score import LabelScore

        assert LabelScore("x", support=0, predicted=0, true_positives=0).f1 == 0.0
