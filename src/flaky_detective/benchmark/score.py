"""Turn predictions plus ground truth into numbers worth publishing.

Per-label precision and recall rather than a single accuracy figure, because the
classes are heavily unbalanced. A population where 40% of tests are stable can score
40% accuracy by calling everything stable, and that number would tell nobody
anything.

Two figures are lifted out of the table and reported on their own, because averages
hide exactly the thing this tool cares about most.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..models import Verdict

BREAK_VERDICTS = frozenset({Verdict.REGRESSION, Verdict.BROKEN})


@runtime_checkable
class TruthLabel(Protocol):
    """A ground-truth category that knows which verdict it should receive."""

    @property
    def expected_verdict(self) -> Verdict: ...  # pragma: no cover - structural typing


@runtime_checkable
class LabelledTest(Protocol):
    """What this module needs from a ground-truth label.

    Protocols rather than importing `GroundTruth`, so scoring stays independent of the
    generator and the dependency runs one way. Typing these as `object` would achieve the
    same decoupling while throwing the contract away, which is worse than either
    alternative.
    """

    @property
    def truth(self) -> TruthLabel: ...  # pragma: no cover

    @property
    def detectable(self) -> bool: ...  # pragma: no cover

    @property
    def polluter(self) -> str | None: ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class LabelScore:
    """Precision, recall and F1 for one label."""

    label: str
    support: int
    predicted: int
    true_positives: int

    @property
    def precision(self) -> float:
        """Of everything called this label, how much really was?"""
        return self.true_positives / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        """Of everything that really is this label, how much was found?"""
        return self.true_positives / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Everything measured in one benchmark run."""

    labels: tuple[LabelScore, ...]
    confusion: dict[str, dict[str, int]]
    total: int
    correct: int
    undetectable: int

    false_alarms: int
    breaks_total: int
    missed_breaks: int
    flaky_total: int

    order_dependent_total: int = 0
    order_dependent_diagnosed: int = 0
    order_dependent_polluter_correct: int = 0
    order_dependent_polluter_named: int = 0
    """How many order-dependent tests had *some* polluter named, right or wrong.

    The denominator for polluter precision. Precision used to divide by
    `order_dependent_diagnosed`, which counts tests diagnosed as order dependent by any
    route -- including the message-text heuristic, which needs no polluter at all. A victim
    the detector honestly declined to attribute therefore counted against precision as
    though it had been attributed wrongly, and the reported 0.875 hid the fact that every
    polluter actually named was correct.
    """

    runs: int = 0
    commit_coverage: float = 1.0
    order_window: int = 0
    seed: int = 0
    notes: tuple[str, ...] = field(default=())

    @property
    def accuracy(self) -> float:
        """Reported for completeness, but the per-label table is what matters."""
        return self.correct / self.total if self.total else 0.0

    @property
    def false_alarm_rate(self) -> float:
        """How often a real break gets called flaky.

        The worst failure mode this tool has: it teaches the user to re-run instead
        of investigate, which is the habit the tool exists to break. Reported on its
        own so it cannot be averaged away.
        """
        return self.false_alarms / self.breaks_total if self.breaks_total else 0.0

    @property
    def missed_break_rate(self) -> float:
        """How often a flake gets called a real break.

        The mirror failure: sends someone hunting a bad commit that does not exist.
        Less damaging than a false alarm, but not free.
        """
        return self.missed_breaks / self.flaky_total if self.flaky_total else 0.0

    @property
    def polluter_precision(self) -> float:
        """Of the polluters we named, how many were the right test?

        Divides by how many were *named*, not by how many tests were diagnosed. Declining
        to attribute is not the same mistake as attributing wrongly, and a metric that
        treats them alike would push the detector towards guessing.
        """
        if not self.order_dependent_polluter_named:
            return 0.0
        return self.order_dependent_polluter_correct / self.order_dependent_polluter_named

    @property
    def polluter_naming_rate(self) -> float:
        """Of the order-dependent tests, how many got a polluter named at all?

        Reported separately from precision because they move in opposite directions and a
        single number would hide the trade.
        """
        if not self.order_dependent_total:
            return 0.0
        return self.order_dependent_polluter_named / self.order_dependent_total

    @property
    def polluter_recall(self) -> float:
        if not self.order_dependent_total:
            return 0.0
        return self.order_dependent_diagnosed / self.order_dependent_total

    def label(self, name: str) -> LabelScore | None:
        return next((score for score in self.labels if score.label == name), None)


def score_predictions(
    truths: dict[str, LabelledTest],
    predictions: dict[str, Verdict],
    *,
    causes: dict[str, str] | None = None,
    polluters: dict[str, str | None] | None = None,
    runs: int = 0,
    commit_coverage: float = 1.0,
    seed: int = 0,
    order_window: int = 0,
) -> BenchmarkResult:
    """Compare predicted verdicts against ground truth.

    `truths` maps test id to anything satisfying `LabelledTest`.
    """
    causes = causes or {}
    polluters = polluters or {}

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    support: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    hits: Counter[str] = Counter()

    total = correct = undetectable = 0
    false_alarms = breaks_total = missed_breaks = flaky_total = 0
    order_total = order_diagnosed = order_polluter_ok = order_polluter_named = 0

    for test_id, truth in truths.items():
        if not truth.detectable:
            undetectable += 1
            continue

        expected = truth.truth.expected_verdict
        actual = predictions.get(test_id)
        if actual is None:
            # A test present in the answer key but absent from the analysis means the
            # harness and the tool disagree about the population; that is a bug in
            # the harness, not a scoring outcome, so it is surfaced loudly.
            raise ValueError(f"{test_id} was generated but not analyzed")

        total += 1
        expected_name, actual_name = str(expected), str(actual)
        support[expected_name] += 1
        predicted_counts[actual_name] += 1
        confusion[expected_name][actual_name] += 1

        if expected is actual:
            correct += 1
            hits[expected_name] += 1

        # The two figures that get their own headline.
        if expected in BREAK_VERDICTS:
            breaks_total += 1
            if actual is Verdict.FLAKY:
                false_alarms += 1
        if expected is Verdict.FLAKY:
            flaky_total += 1
            if actual in BREAK_VERDICTS:
                missed_breaks += 1

        # Order dependence is a diagnosis, not a verdict, so it is scored separately.
        if str(truth.truth) == "order_dependent":
            order_total += 1
            if causes.get(test_id) == "order_dependence":
                order_diagnosed += 1
            # Counted independently of the diagnosis. A polluter can be named while the
            # cause is attributed elsewhere, and precision has to be measured against what
            # was actually named or declining to attribute looks like attributing wrongly.
            if named := polluters.get(test_id):
                order_polluter_named += 1
                if named == truth.polluter:
                    order_polluter_ok += 1

    labels = tuple(
        LabelScore(
            label=name,
            support=support.get(name, 0),
            predicted=predicted_counts.get(name, 0),
            true_positives=hits.get(name, 0),
        )
        for name in sorted(set(support) | set(predicted_counts))
    )

    return BenchmarkResult(
        labels=labels,
        confusion={k: dict(v) for k, v in confusion.items()},
        total=total,
        correct=correct,
        undetectable=undetectable,
        false_alarms=false_alarms,
        breaks_total=breaks_total,
        missed_breaks=missed_breaks,
        flaky_total=flaky_total,
        order_dependent_total=order_total,
        order_dependent_diagnosed=order_diagnosed,
        order_dependent_polluter_correct=order_polluter_ok,
        order_dependent_polluter_named=order_polluter_named,
        runs=runs,
        commit_coverage=commit_coverage,
        seed=seed,
        order_window=order_window,
    )
