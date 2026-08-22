"""Tests for the shared binomial reasoning.

Three verdicts in this tool rest on these functions -- whether a branch introduced a
flake, whether a fix worked, and how many runs are needed to say so -- so they are
checked against values that can be verified by hand rather than only against each other.
"""

from __future__ import annotations

import math

import pytest

from flaky_detective.analysis.statistics import (
    DEFAULT_ALPHA,
    MAX_EXACT_TRIALS,
    cdf_at_most,
    lower_bound,
    tail_at_least,
    tail_at_most,
    trials_needed,
    upper_bound,
)


class TestCdf:
    def test_sums_to_one_over_the_whole_range(self) -> None:
        assert cdf_at_most(10, 10, 0.37) == pytest.approx(1.0)

    def test_matches_a_hand_computed_value(self) -> None:
        """P(X <= 1) for n=3, p=0.5 is 4/8."""
        assert cdf_at_most(1, 3, 0.5) == pytest.approx(0.5)

    def test_certainty_at_rate_zero(self) -> None:
        assert cdf_at_most(0, 100, 0.0) == pytest.approx(1.0)

    def test_impossible_below_all_trials_at_rate_one(self) -> None:
        assert cdf_at_most(9, 10, 1.0) == 0.0
        assert cdf_at_most(10, 10, 1.0) == 1.0

    def test_negative_successes_are_impossible(self) -> None:
        assert cdf_at_most(-1, 10, 0.5) == 0.0

    def test_no_trials_is_certain(self) -> None:
        assert cdf_at_most(0, 0, 0.5) == 1.0


class TestTails:
    def test_at_least_one_is_the_complement_of_none(self) -> None:
        rate, trials = 0.2, 15
        assert tail_at_least(1, trials, rate) == pytest.approx(1.0 - (1 - rate) ** trials)

    def test_at_least_zero_is_certain(self) -> None:
        assert tail_at_least(0, 20, 0.3) == 1.0

    def test_more_than_all_trials_is_impossible(self) -> None:
        assert tail_at_least(21, 20, 0.5) == 0.0

    def test_at_least_shrinks_as_the_bar_rises(self) -> None:
        rate = 0.1
        values = [tail_at_least(k, 40, rate) for k in (2, 6, 12, 20)]
        assert values == sorted(values, reverse=True)

    def test_at_most_is_a_clean_streak_probability(self) -> None:
        """Zero failures in n runs at rate p is exactly (1-p)**n.

        The number the whole fix verification rests on.
        """
        assert tail_at_most(0, 30, 0.35) == pytest.approx((1 - 0.35) ** 30)

    def test_at_most_and_at_least_partition_the_space(self) -> None:
        rate, trials, k = 0.28, 25, 9
        assert tail_at_most(k, trials, rate) + tail_at_least(k + 1, trials, rate) == pytest.approx(
            1.0
        )

    def test_huge_trial_counts_stay_in_range_and_finish(self) -> None:
        """Guards against math.comb on a pathological n making a step look hung."""
        assert 0.0 <= tail_at_least(3000, MAX_EXACT_TRIALS * 2, 0.5) <= 1.0
        assert 0.0 <= tail_at_most(3000, MAX_EXACT_TRIALS * 2, 0.5) <= 1.0


class TestUpperBound:
    def test_zero_failures_reproduces_the_rule_of_three(self) -> None:
        """The single most consequential number: a clean baseline is not a zero rate.

        No failures in 40 runs still admits a true rate near 3/40.
        """
        bound = upper_bound(0, 40)
        assert bound == pytest.approx(1.0 - DEFAULT_ALPHA ** (1 / 40))
        assert bound == pytest.approx(3 / 40, abs=0.01)

    def test_tightens_as_evidence_grows(self) -> None:
        assert upper_bound(0, 10) > upper_bound(0, 40) > upper_bound(0, 200)

    def test_sits_at_the_point_where_the_cdf_equals_alpha(self) -> None:
        """What makes it a confidence bound rather than an arbitrary inflation."""
        bound = upper_bound(5, 40)
        assert cdf_at_most(5, 40, bound) == pytest.approx(DEFAULT_ALPHA, abs=1e-3)
        assert 5 / 40 < bound < 1.0

    def test_all_failures_admits_certainty(self) -> None:
        assert upper_bound(10, 10) == 1.0

    def test_no_trials_admits_anything(self) -> None:
        assert upper_bound(0, 0) == 1.0

    def test_a_stricter_alpha_gives_a_wider_bound(self) -> None:
        assert upper_bound(0, 40, 0.01) > upper_bound(0, 40, 0.10)


class TestLowerBound:
    def test_zero_failures_has_no_positive_floor(self) -> None:
        assert lower_bound(0, 40) == 0.0

    def test_sits_below_the_observed_rate(self) -> None:
        """The conservative direction for claiming a fix: assume the old rate was low."""
        assert 0.0 < lower_bound(14, 40) < 14 / 40

    def test_sits_at_the_point_where_the_upper_tail_equals_alpha(self) -> None:
        bound = lower_bound(14, 40)
        assert tail_at_least(14, 40, bound) == pytest.approx(DEFAULT_ALPHA, abs=1e-3)

    def test_all_failures_still_admits_a_rate_below_one(self) -> None:
        bound = lower_bound(10, 10)
        assert 0.0 < bound < 1.0
        assert bound == pytest.approx(DEFAULT_ALPHA ** (1 / 10))

    def test_tightens_towards_the_observed_rate_as_evidence_grows(self) -> None:
        assert lower_bound(5, 20) < lower_bound(50, 200) < lower_bound(500, 2000)

    def test_no_trials_has_no_floor(self) -> None:
        assert lower_bound(0, 0) == 0.0


class TestTrialsNeeded:
    def test_solves_the_clean_streak_equation(self) -> None:
        needed = trials_needed(0.35)
        assert (1 - 0.35) ** needed <= DEFAULT_ALPHA
        assert (1 - 0.35) ** (needed - 1) > DEFAULT_ALPHA

    def test_a_rare_flake_needs_far_more_runs(self) -> None:
        """The asymmetry worth surfacing, because it is counter-intuitive.

        The flake that fails once in fifty runs is the one people declare fixed after
        three green runs, and it is the one that needs the most patience.
        """
        assert trials_needed(0.35) < 10
        assert trials_needed(0.02) > 100

    def test_matches_the_closed_form(self) -> None:
        for rate in (0.05, 0.2, 0.5, 0.9):
            assert trials_needed(rate) == math.ceil(math.log(DEFAULT_ALPHA) / math.log(1 - rate))

    def test_a_rate_of_zero_needs_nothing(self) -> None:
        assert trials_needed(0.0) == 0

    def test_certain_failure_needs_one_run(self) -> None:
        assert trials_needed(1.0) == 1

    def test_never_returns_zero_for_a_real_rate(self) -> None:
        assert trials_needed(0.999) >= 1


class TestDeterminism:
    def test_bounds_are_bit_identical_across_calls(self) -> None:
        """Analysis output has to be reproducible, so the bisection is fixed-step."""
        assert upper_bound(7, 33) == upper_bound(7, 33)
        assert lower_bound(7, 33) == lower_bound(7, 33)
