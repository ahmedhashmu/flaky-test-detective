"""Order-dependence detection.

A test that only fails when it runs after some other test has a shared-state bug,
and that is a different fix from a timeout. Telling the two apart is worth doing
because "add a retry" is the wrong answer to the first and a tolerable answer to
the second.

Detection requires **naming a polluter**: a test that ran immediately before, whose
presence explains the failures better than the test's own base failure rate does.
Position separation is measured and reported alongside, but is not sufficient on
its own.

That restriction is the result of a measurement, not caution for its own sake. An
earlier version treated position separation as an independent trigger. Run against
the demo suite over 40 shuffled iterations, the two tests with the strongest
position signal (t = 3.47) were both *timing* flakes, while the two genuinely
order-dependent tests scored only t ≈ 2.3. Position correlates with how late in the
run a test executes, which correlates with machine state: warmer caches, more
threads created, more garbage to collect. That is a real effect and a real cause of
flakiness, but it is not shared-state pollution, and labelling it
`order_dependence` would send someone looking for a leaked fixture that does not
exist.

Naming the polluter is also what makes the finding useful. "This test is order
dependent" sends someone hunting. "This test fails whenever it runs after
`test_registers_session`, 20 of 20 times" sends them to a diff.

## The adjacency assumption, and how it was caught

The first version considered only the **immediately** preceding test. The docstring called
that a known limitation and guessed it would be tolerable, reasoning that suites usually
shuffle within a file so the polluter is often adjacent.

That guess was wrong, and the generated benchmark could not tell us so, because
`benchmark/generate.py` placed every polluter immediately before its victim -- encoding the
same assumption. Two components agreeing with each other looked like validation. Scored
against real repositories with published labels, order-dependence *detection* was 146 of
146 and *diagnosis* was 17: the tool knew the tests were flaky and could not say why.

So detection now searches a bounded window of preceding tests. See
[ADR-0014](../../../docs/adr/0014-search-a-window-for-the-polluter.md).

## Why a window needs a multiplicity correction

Widening the search from one predecessor to `window` of them means testing several
hypotheses per victim instead of one, and a 5% threshold applied to eight candidates fires
on noise roughly a third of the time. That is precisely the trap the first two versions of
this module fell into, arriving by a new route.

The threshold is therefore divided by the number of candidates actually tested
(Bonferroni). It is the crudest available correction and the easiest to explain, which for
a rule that has already misfired twice is the right trade.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from ..models import OrderEvidence, TestOutcome
from .statistics import tail_at_least

MIN_OBSERVATIONS_PER_SIDE = 4
"""Below this the statistic is noise. Two or three points always look separated."""

SEPARATION_THRESHOLD = 1.0
"""Reported separation, in standard deviations of the overall position spread.

Kept because it is the interpretable number to show a human, but it is *not* what
the decision is made on. See T_THRESHOLD.
"""

T_THRESHOLD = 2.5
"""Significance gate on the difference of mean positions.

The first implementation divided the difference in means by the pooled standard
deviation and flagged anything above 1.0. That ignores sample size, and it
misfired: run against the demo suite it labelled a purely random test as order
dependent at 1.1 standard deviations and named an innocent predecessor as the
polluter. A false "this is order dependent" is expensive, because it sends someone
hunting for shared state that does not exist.

Dividing by the standard error of the difference instead accounts for how many
observations back the estimate, so a wide gap measured from five noisy points no
longer outranks a narrow gap measured from fifty consistent ones.
"""

MIN_POLLUTER_OBSERVATIONS = 4
"""How often a predecessor must have run immediately before the test."""

POLLUTER_SHARE_THRESHOLD = 0.9
"""The test must fail nearly every time this predecessor runs before it.

Lowering this was tried and reverted, which is worth recording because the reasoning for
trying it was sound. Instrumenting the gates over 146 order-labelled tests in real
repositories showed roughly 109 of them dying here, with a median best-candidate share of
**0.73** -- real pollution is often probabilistic rather than near-deterministic, so 0.9
looked like the binding constraint.

It was not. Dropped to 0.6, real-world polluter naming moved from 8 to 8: the candidates
that newly cleared this gate then failed the significance test underneath it. The gate was
not what was stopping them, and a relaxed safety threshold with no measured benefit is
exactly the change this project does not ship. See ADR-0014 for the full sequence.
"""

MAX_BASE_FAILURE_RATE = 0.75
"""Above this, a predecessor cannot be blamed.

A test that fails three runs in four will fail after *everything*. Attributing that
to whichever test happened to precede it names an innocent bystander.
"""

CHANCE_THRESHOLD = 0.05
"""How improbable the observed failures must be under the test's own base rate.

This is the correction for the detector's second misfire. Requiring only "fails
90% of the time after X" flagged eight of ten demo tests with a reported share of
1.0, because in a shuffled suite of ten tests a given predecessor precedes the
victim only three or four times, and a test that already fails 70% of the time will
fail all four by chance about a quarter of the time.

Comparing against the base rate instead asks the right question: is failing after
X more likely than this test's ordinary behaviour already explains?

**The statistic used to be wrong for anything but a perfect correlation.** It was
`base_rate ** fail_count`, which is the probability that *every* observation after the
candidate failed -- correct only when the share is 1.0, and increasingly conservative
below that. It is now an exact binomial tail, `P(X >= fail_count)` over the runs where the
candidate preceded the victim, which is the same quantity the branch comparison and fix
verification use. That change is what made it safe to lower the share threshold, since the
approximation was the only thing holding the crude gate up.
"""

_EPSILON = 1e-9

DEFAULT_WINDOW = 6
"""How many preceding tests are considered as candidate polluters.

Swept rather than chosen: `flaky benchmark --sweep window` measures polluter precision and
recall at each value. Wider finds polluters further back and costs precision through the
multiplicity correction, since every extra candidate tightens the threshold every candidate
must clear.
"""

MIN_LIFT = 1.5
"""Least association lift a candidate must show.

Mostly implied by the share threshold, and kept as an explicit gate because it is the
number a reader can check by eye: "fails six times more often after this test" is a claim
you can hold against the counts printed beside it.
"""

PredecessorIndex = dict[tuple[str, str], str]
"""(run_uid, test_id) -> test_id that ran immediately before it in that run."""

OrderingIndex = dict[tuple[str, str], tuple[tuple[str, int], ...]]
"""(run_uid, test_id) -> ((candidate, distance), ...) for tests inside the window.

Distance 1 is immediately before. Ordered nearest-first so the cheapest explanation is
considered first.
"""


def build_predecessor_index(outcomes: list[TestOutcome]) -> PredecessorIndex:
    """Map every test to whatever ran just before it, per run.

    The distance-1 view, still used wherever "ran immediately before" is the question being
    displayed rather than searched -- the dashboard's neighbour table, for instance.

    Built once for the whole analysis rather than per test, since the cost is one
    pass over the data either way and per-test would make it quadratic.
    """
    by_run: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.run_uid is not None and outcome.position is not None:
            by_run[outcome.run_uid].append(outcome)

    index: PredecessorIndex = {}
    for run_uid, run_outcomes in by_run.items():
        ordered = sorted(run_outcomes, key=lambda o: o.position or 0)
        for previous, current in itertools.pairwise(ordered):
            index[(run_uid, current.test_id)] = previous.test_id
    return index


def build_ordering_index(
    outcomes: list[TestOutcome], window: int = DEFAULT_WINDOW
) -> OrderingIndex:
    """Map every test to the tests that ran shortly before it, per run.

    Linear in outcomes and in `window`, not quadratic in suite size: each run is sorted
    once and each test looks back a fixed number of slots. Built once for the whole
    analysis, like the distance-1 index.
    """
    if window < 1:
        return {}

    by_run: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.run_uid is not None and outcome.position is not None:
            by_run[outcome.run_uid].append(outcome)

    index: OrderingIndex = {}
    for run_uid, run_outcomes in by_run.items():
        ordered = sorted(run_outcomes, key=lambda o: (o.position or 0, o.test_id))
        for position, current in enumerate(ordered):
            start = max(0, position - window)
            preceding = ordered[start:position]
            # Nearest first, and distance counted in slots rather than in the runner's own
            # position numbers, which are not always contiguous.
            index[(run_uid, current.test_id)] = tuple(
                (earlier.test_id, position - (start + offset))
                for offset, earlier in enumerate(preceding)
            )[::-1]
    return index


def detect_order_dependence(
    outcomes: list[TestOutcome],
    ordering: OrderingIndex | None = None,
) -> OrderEvidence | None:
    """Decide whether a single test's outcome tracks its position in the run.

    Returns None when there is not enough evidence, which is the common case and
    not a failure. Skips are excluded: they carry no information about outcome.
    """
    passes = [o for o in outcomes if o.status.is_pass and o.position is not None and not o.retried]
    failures = [o for o in outcomes if o.status.is_failure and o.position is not None]
    # A retry means the runner saw this test both fail and pass at one position,
    # so it is evidence of failing there.
    failures.extend(o for o in outcomes if o.retried and o.position is not None)

    if len(passes) < MIN_OBSERVATIONS_PER_SIDE or len(failures) < MIN_OBSERVATIONS_PER_SIDE:
        return None

    pass_positions = [float(o.position or 0) for o in passes]
    fail_positions = [float(o.position or 0) for o in failures]
    all_positions = pass_positions + fail_positions

    spread = statistics.pstdev(all_positions)
    if spread < _EPSILON:
        # The test ran at the same index every time, so position cannot explain
        # anything. This is the normal case for a suite that never reorders.
        return None

    mean_pass = statistics.fmean(pass_positions)
    mean_fail = statistics.fmean(fail_positions)
    difference = abs(mean_fail - mean_pass)
    separation = difference / (spread + _EPSILON)

    # Naming a polluter is required, not optional. See the module docstring for the
    # measurement that led to this: position separation on its own turns out to
    # track machine warm-up rather than state pollution.
    found = _likely_polluter(passes, failures, ordering or {})
    if found is None:
        return None

    return OrderEvidence(
        separation=round(separation, 2),
        mean_position_on_fail=round(mean_fail, 1),
        mean_position_on_pass=round(mean_pass, 1),
        likely_polluter=found.test_id,
        polluter_failure_share=round(found.share, 2),
        polluter_distance=round(found.distance, 1),
        polluter_lift=round(found.lift, 1),
        polluter_observations=found.observations,
        candidates_considered=found.candidates,
    )


def _significant(
    pass_positions: list[float], fail_positions: list[float], difference: float
) -> bool:
    """Is the gap in mean position larger than sampling noise explains?

    Uses the standard error of the difference of means rather than the pooled
    spread, so the answer depends on how many observations back the estimate. This
    is the gate that keeps random tests from being labelled order dependent.
    """
    if difference < _EPSILON:
        return False

    standard_error = math.sqrt(
        statistics.variance(pass_positions) / len(pass_positions)
        + statistics.variance(fail_positions) / len(fail_positions)
    )

    if standard_error < _EPSILON:
        # Both groups are perfectly consistent and their means differ, so position
        # explains the outcome exactly. The strongest form of this signal.
        return True

    return difference / standard_error >= T_THRESHOLD


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One predecessor that survived every gate, with the numbers behind it."""

    test_id: str
    share: float
    lift: float
    distance: float
    observations: int
    probability: float
    candidates: int = 0


def _likely_polluter(
    passes: list[TestOutcome],
    failures: list[TestOutcome],
    ordering: OrderingIndex,
) -> _Candidate | None:
    """Find a preceding test whose presence explains the failures.

    "Explains" is the operative word. It is not enough that the test usually fails
    after this predecessor; it has to fail after it *more than its own base failure
    rate already accounts for*. Otherwise a test that fails most of the time gets a
    randomly-chosen neighbour blamed for it.

    Searching a window rather than one slot means several hypotheses per victim, so the
    improbability threshold is divided by the number of candidates that were genuinely
    tested. Without that, widening the search would trade the misfire this module already
    fixed for the same misfire at a different distance.
    """
    if not ordering:
        return None

    observations = len(passes) + len(failures)
    if not observations:
        return None

    base_rate = len(failures) / observations
    if base_rate >= MAX_BASE_FAILURE_RATE or base_rate <= 0.0:
        return None

    before_failure: Counter[str] = Counter()
    before_pass: Counter[str] = Counter()
    distances: dict[str, list[int]] = defaultdict(list)

    for group, tally in ((failures, before_failure), (passes, before_pass)):
        for outcome in group:
            seen_here: set[str] = set()
            for candidate, distance in ordering.get((outcome.run_uid or "", outcome.test_id), ()):
                # A test appearing twice in one window would otherwise count twice for a
                # single run, inflating its association.
                if candidate in seen_here:
                    continue
                seen_here.add(candidate)
                tally[candidate] += 1
                if tally is before_failure:
                    distances[candidate].append(distance)

    # The correction's denominator is the number of candidates with enough observations to
    # be testable at all, not every test that ever appeared in a window. Counting
    # untestable candidates would penalise a large suite for tests it never really
    # considered.
    testable = [
        candidate
        for candidate in before_failure
        if before_failure[candidate] + before_pass.get(candidate, 0) >= MIN_POLLUTER_OBSERVATIONS
    ]
    if not testable:
        return None

    threshold = CHANCE_THRESHOLD / len(testable)
    best: _Candidate | None = None

    for candidate in sorted(testable):
        fail_count = before_failure[candidate]
        seen = fail_count + before_pass.get(candidate, 0)

        share = fail_count / seen
        if share < POLLUTER_SHARE_THRESHOLD:
            continue

        lift = share / base_rate
        if lift < MIN_LIFT:
            continue

        # Probability of seeing at least this many failures in `seen` runs if the
        # predecessor made no difference. An exact binomial tail rather than
        # `base_rate ** fail_count`, which answered a different question -- the chance that
        # *all* of them failed -- and was only right when the share was 1.0.
        probability = tail_at_least(fail_count, seen, base_rate)
        if probability > threshold:
            continue

        median_distance = statistics.median(distances[candidate]) if distances[candidate] else 0.0
        found = _Candidate(
            test_id=candidate,
            share=share,
            lift=lift,
            distance=float(median_distance),
            observations=seen,
            probability=probability,
            candidates=len(testable),
        )

        # Prefer the strongest association; break ties toward the nearer and
        # better-observed candidate, and finally by name so the result is deterministic.
        if best is None or (share, -found.distance, seen, candidate) > (
            best.share,
            -best.distance,
            best.observations,
            best.test_id,
        ):
            best = found

    return best
