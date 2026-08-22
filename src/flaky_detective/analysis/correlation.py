"""Does this test's failure track something about the environment it ran in?

"Likely a timeout" is a guess from the failure text. "Fails 19 times in 23 on ARM and twice
in 46 on x86" is a measurement, and it tells you where to go and reproduce the thing. That
gap is the whole reason this module exists.

## Why generic dimensions rather than named ones

The obvious design is columns for OS and architecture. It is wrong in both directions:
projects differ in what actually explains their flakiness, and the interesting dimension is
frequently one nobody would have put in a schema -- a shard index, a parallelism setting, a
dependency version, one bad runner image. So the environment is a bag of labels and this
module correlates against whatever is there, including labels a project invents.

## What it takes to answer at all

Two values of a dimension, both with runs. A database built on one laptop has `os=darwin`
and nothing else, and a dimension with one observed value cannot explain anything. Pooling
history across machines is what makes the question answerable, and `flaky merge` already
does that -- which is why merge carries labels with the runs it copies.

## The statistic, and the correction it needs

For each dimension and each of its values, compare the failure rate *at* that value against
the failure rate everywhere else, and ask how improbable the observed failures would be if
the value made no difference. An exact binomial tail, the same `statistics.tail_at_least`
the branch comparison, the fix verification and the polluter search use.

A suite with six dimensions of three values each offers eighteen hypotheses per test, and a
5% threshold applied eighteen times fires on noise most of the time. The threshold is
divided by the number of value pairs actually tested, exactly as in `ordering.py`. That
correction is the difference between a useful signal and a machine for generating
coincidences, and this module is the third place in the codebase to need it.

Pure, like the rest of `analysis/`: outcomes and a label mapping in, associations out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ..models import DimensionAssociation, TestOutcome
from .statistics import tail_at_least

ALPHA = 0.05
"""Improbability required before an association is reported, before correction."""

MIN_RUNS_PER_VALUE = 4
"""Runs a dimension value needs before it is testable.

Below this the rate is dominated by the sample size. Two failures out of two on a value is
not evidence that the value matters.
"""

MIN_LIFT = 1.5
"""Least ratio between the failure rate at a value and the rate elsewhere.

Keeps out associations that are statistically detectable and practically meaningless -- a
4% rate against 3.5% over enough runs will clear a p-value while telling nobody anything
useful about where to look.
"""

MAX_VALUES_PER_DIMENSION = 12
"""Above this a dimension is ignored entirely.

A dimension with a distinct value for almost every run -- a run id, a timestamp, a commit
SHA someone recorded as a label -- carries no grouping information, and testing every value
would spend the whole multiplicity budget on noise.
"""

LabelIndex = dict[str, dict[str, str]]
"""run_uid -> {dimension: value}."""


def detect_environment_association(
    outcomes: list[TestOutcome],
    labels: LabelIndex | None = None,
    *,
    alpha: float = ALPHA,
) -> tuple[DimensionAssociation, ...]:
    """Find dimensions whose values this test's failures track, strongest first.

    Returns an empty tuple in the common cases: one environment, too little history, or no
    association worth reporting. That is a normal answer and not a failure.
    """
    if not labels:
        return ()

    considered = [o for o in outcomes if o.status.counts_as_evidence and o.run_uid]
    if len(considered) < MIN_RUNS_PER_VALUE * 2:
        return ()

    failures = sum(1 for o in considered if o.status.is_failure or o.retried)
    if failures == 0 or failures == len(considered):
        # Never failed, or always failed. Either way nothing about the environment can
        # explain a difference that does not exist.
        return ()

    tallies = _tally(considered, labels)
    testable = _testable_pairs(tallies)
    if not testable:
        return ()

    threshold = alpha / len(testable)
    found: list[DimensionAssociation] = []

    for dimension, value in testable:
        counts = tallies[dimension]
        runs, fails = counts[value]
        other_runs = sum(r for v, (r, _) in counts.items() if v != value)
        other_fails = sum(f for v, (_, f) in counts.items() if v != value)

        if other_runs < MIN_RUNS_PER_VALUE:
            # Nothing to compare against: this value is effectively the whole history.
            continue

        rate = fails / runs
        other_rate = other_fails / other_runs
        if other_rate <= 0.0:
            # A clean elsewhere-rate makes the lift infinite and the comparison
            # degenerate, so the baseline is the whole-history rate instead, which is the
            # conservative reading.
            other_rate = failures / len(considered)
        if other_rate <= 0.0 or rate <= other_rate:
            continue

        lift = rate / other_rate
        if lift < MIN_LIFT:
            continue

        probability = tail_at_least(fails, runs, other_rate)
        if probability > threshold:
            continue

        found.append(
            DimensionAssociation(
                dimension=dimension,
                value=value,
                failures=fails,
                runs=runs,
                other_failures=other_fails,
                other_runs=other_runs,
                lift=round(lift, 1),
                probability=round(probability, 6),
                values_considered=len(testable),
            )
        )

    # Strongest first: biggest lift, then most improbable, then by name so the order is
    # stable when two dimensions say the same thing.
    found.sort(key=lambda a: (-a.lift, a.probability, a.dimension, a.value))
    return _mark_confounded(found, considered, labels)


def _mark_confounded(
    found: list[DimensionAssociation],
    outcomes: list[TestOutcome],
    labels: LabelIndex,
) -> tuple[DimensionAssociation, ...]:
    """Note which associations split the runs identically.

    Every ARM runner in a fleet having two CPUs makes `arch=arm64` and `cpus=2` the same set
    of runs. Presenting them as two findings would invent a second cause and send someone
    looking for a CPU-count bug; presenting only the strongest would hide a real
    alternative. Naming them as indistinguishable is the only honest option, and it is
    computed from the actual run sets rather than guessed from matching counts, because two
    unrelated dimensions can coincidentally share a tally.
    """
    if len(found) < 2:
        return tuple(found)

    run_sets: dict[str, frozenset[str]] = {}
    for association in found:
        key = association.summary
        run_sets[key] = frozenset(
            outcome.run_uid or ""
            for outcome in outcomes
            if labels.get(outcome.run_uid or "", {}).get(association.dimension) == association.value
        )

    marked: list[DimensionAssociation] = []
    for association in found:
        mine = run_sets[association.summary]
        twins = tuple(
            other.summary
            for other in found
            if other.summary != association.summary and run_sets[other.summary] == mine
        )
        marked.append(replace(association, covaries_with=twins))

    return tuple(marked)


def _tally(
    outcomes: list[TestOutcome], labels: LabelIndex
) -> dict[str, dict[str, tuple[int, int]]]:
    """dimension -> value -> (runs, failures) for this test."""
    counts: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    for outcome in outcomes:
        for dimension, value in labels.get(outcome.run_uid or "", {}).items():
            entry = counts[dimension][value]
            entry[0] += 1
            if outcome.status.is_failure or outcome.retried:
                entry[1] += 1

    return {
        dimension: {value: (pair[0], pair[1]) for value, pair in values.items()}
        for dimension, values in counts.items()
    }


def _testable_pairs(
    tallies: dict[str, dict[str, tuple[int, int]]],
) -> list[tuple[str, str]]:
    """Which dimension/value pairs have enough evidence to be worth testing.

    The multiplicity correction's denominator, so it counts only pairs genuinely examined.
    Dimensions with a single observed value are dropped here: they cannot explain a
    difference, and counting them would tighten the threshold for the dimensions that can.
    """
    pairs: list[tuple[str, str]] = []
    for dimension in sorted(tallies):
        values = tallies[dimension]
        if len(values) < 2 or len(values) > MAX_VALUES_PER_DIMENSION:
            continue
        for value in sorted(values):
            runs, _ = values[value]
            if runs >= MIN_RUNS_PER_VALUE:
                pairs.append((dimension, value))
    return pairs


__all__ = [
    "ALPHA",
    "MAX_VALUES_PER_DIMENSION",
    "MIN_LIFT",
    "MIN_RUNS_PER_VALUE",
    "LabelIndex",
    "detect_environment_association",
]
