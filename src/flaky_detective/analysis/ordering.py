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

**Known limitation:** only the immediately preceding test is considered. A polluter
that runs several tests earlier will be missed. Checking every earlier test for
every candidate is quadratic in suite size, and the cheap version catches the
common case, where the polluter is adjacent because suites are usually shuffled
within a file or class.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

from ..models import OrderEvidence, TestOutcome

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
"""The test must fail nearly every time this predecessor runs before it."""

MAX_BASE_FAILURE_RATE = 0.75
"""Above this, a predecessor cannot be blamed.

A test that fails three runs in four will fail after *everything*. Attributing that
to whichever test happened to precede it names an innocent bystander.
"""

CHANCE_THRESHOLD = 0.05
"""How improbable the observed run of failures must be under the base rate.

This is the correction for the detector's second misfire. Requiring only "fails
90% of the time after X" flagged eight of ten demo tests with a reported share of
1.0, because in a shuffled suite of ten tests a given predecessor precedes the
victim only three or four times, and a test that already fails 70% of the time will
fail all four by chance about a quarter of the time.

Comparing against the base rate instead asks the right question: is failing after
X more likely than this test's ordinary behaviour already explains?
"""

_EPSILON = 1e-9

PredecessorIndex = dict[tuple[str, str], str]
"""(run_uid, test_id) -> test_id that ran immediately before it in that run."""


def build_predecessor_index(outcomes: list[TestOutcome]) -> PredecessorIndex:
    """Map every test to whatever ran just before it, per run.

    Built once for the whole analysis rather than per test, since the cost is one
    pass over the data either way and per-test would make it quadratic.
    """
    by_run: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.run_uid is not None and outcome.position is not None:
            by_run[outcome.run_uid].append(outcome)

    index: PredecessorIndex = {}
    for run_uid, run_outcomes in by_run.items():
        ordered = sorted(run_outcomes, key=lambda o: (o.position or 0))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            index[(run_uid, current.test_id)] = previous.test_id
    return index


def detect_order_dependence(
    outcomes: list[TestOutcome],
    predecessors: PredecessorIndex | None = None,
) -> OrderEvidence | None:
    """Decide whether a single test's outcome tracks its position in the run.

    Returns None when there is not enough evidence, which is the common case and
    not a failure. Skips are excluded: they carry no information about outcome.
    """
    passes = [
        o for o in outcomes if o.status.is_pass and o.position is not None and not o.retried
    ]
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
    polluter, share = _likely_polluter(passes, failures, predecessors or {})
    if polluter is None:
        return None

    return OrderEvidence(
        separation=round(separation, 2),
        mean_position_on_fail=round(mean_fail, 1),
        mean_position_on_pass=round(mean_pass, 1),
        likely_polluter=polluter,
        polluter_failure_share=round(share, 2),
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


def _likely_polluter(
    passes: list[TestOutcome],
    failures: list[TestOutcome],
    predecessors: PredecessorIndex,
) -> tuple[str | None, float]:
    """Find a predecessor whose presence explains the failures.

    "Explains" is the operative word. It is not enough that the test usually fails
    after this predecessor; it has to fail after it *more than its own base failure
    rate already accounts for*. Otherwise a test that fails most of the time gets a
    randomly-chosen neighbour blamed for it.
    """
    if not predecessors:
        return None, 0.0

    observations = len(passes) + len(failures)
    if not observations:
        return None, 0.0

    base_rate = len(failures) / observations
    if base_rate >= MAX_BASE_FAILURE_RATE:
        return None, 0.0

    before_failure: Counter[str] = Counter()
    before_pass: Counter[str] = Counter()

    for outcome in failures:
        previous = predecessors.get((outcome.run_uid or "", outcome.test_id))
        if previous:
            before_failure[previous] += 1

    for outcome in passes:
        previous = predecessors.get((outcome.run_uid or "", outcome.test_id))
        if previous:
            before_pass[previous] += 1

    best_test: str | None = None
    best_share = 0.0

    for candidate, fail_count in before_failure.most_common():
        seen = fail_count + before_pass.get(candidate, 0)
        if seen < MIN_POLLUTER_OBSERVATIONS:
            continue

        share = fail_count / seen
        if share < POLLUTER_SHARE_THRESHOLD:
            continue

        # Probability of seeing at least this many failures in `seen` attempts if
        # the predecessor made no difference. Near-certain failure after the
        # candidate makes the simple power a close enough approximation, and it
        # keeps the reasoning explainable.
        if base_rate**fail_count > CHANCE_THRESHOLD:
            continue

        if share > best_share or (share == best_share and fail_count > 0):
            best_test, best_share = candidate, share

    if best_test is None:
        return None, 0.0
    return best_test, best_share
