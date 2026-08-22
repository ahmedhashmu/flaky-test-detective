"""Generate test histories whose correct classification is known by construction.

The value of this module depends entirely on it being honest. It would be easy to
generate data the current scorer happens to classify perfectly -- alternate pass and
fail, label it flaky, report 100% accuracy, learn nothing. So the population
deliberately includes the cases that are genuinely hard, and some that are
impossible:

- **Low-rate flakes** (p = 0.05) may not fail at all in a short window. Nothing can
  detect those, and the recall figure should show it rather than hide it.
- **High-rate flakes** (p = 0.9) usually fail every run in a short window, making
  them indistinguishable from a broken test. This case caught the demo suite out
  twice during the first spec.
- **Regressions with flaky history**, which produced a real bug in the first round.
- **Runs with no commit SHA**, where the primary signal is unavailable entirely.

Everything is driven by one seeded Random, so any reported figure is reproducible.
Outcomes are plain `TestOutcome` objects, which is what `analysis.analyze` consumes,
so the harness measures the real pipeline rather than a parallel reimplementation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from ..models import Status, TestOutcome, Verdict


class Truth(StrEnum):
    """What a generated test actually is."""

    FLAKY = "flaky"
    STABLE = "stable"
    BROKEN = "broken"
    REGRESSION = "regression"
    FIXED = "fixed"
    ORDER_DEPENDENT = "order_dependent"

    @property
    def expected_verdict(self) -> Verdict:
        """The verdict the tool should assign.

        Order-dependent tests are a kind of flake, so they share the verdict; what
        distinguishes them is the diagnosed cause, checked separately.
        """
        return {
            Truth.FLAKY: Verdict.FLAKY,
            Truth.STABLE: Verdict.STABLE,
            Truth.BROKEN: Verdict.BROKEN,
            Truth.REGRESSION: Verdict.REGRESSION,
            Truth.FIXED: Verdict.FIXED,
            Truth.ORDER_DEPENDENT: Verdict.FLAKY,
        }[self]


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """The label attached to one generated test."""

    test_id: str
    truth: Truth
    failure_rate: float = 0.0
    polluter: str | None = None
    polluter_distance: int = 0
    """Tests between the polluter and the victim. 1 is immediately before.

    Recorded so accuracy can be reported per distance, which is the only way to see where
    the detector's reach ends rather than averaging it into one number.
    """

    detectable: bool = True
    """False when the generated history cannot support the correct answer.

    A flake that never actually failed in the window is genuinely undetectable, and
    counting it as a miss would understate the tool by measuring it against
    information it was never given. These are reported separately rather than
    silently dropped.
    """


@dataclass(frozen=True, slots=True)
class Population:
    """A generated dataset with its answer key."""

    outcomes: list[TestOutcome]
    truths: dict[str, GroundTruth]
    runs: int
    commit_coverage: float
    seed: int

    @property
    def detectable(self) -> dict[str, GroundTruth]:
        return {k: v for k, v in self.truths.items() if v.detectable}


# Failure probabilities for generated flakes, spanning the range that matters.
# 0.05 is near-undetectable in a short window; 0.9 is nearly indistinguishable from
# broken. Both are included on purpose.
FLAKE_RATES = (0.05, 0.15, 0.3, 0.5, 0.7, 0.9)

POLLUTER_DISTANCES = (1, 2, 3, 5, 8)
"""How many tests separate a polluter from its victim, cycled across the population.

Includes 8, which is beyond the detector's default search window, on purpose: a benchmark
whose hardest case is inside the implementation's reach cannot report a limit. Recall below
1.000 here is the honest consequence and is published rather than tuned away.
"""

MESSAGES = {
    Truth.FLAKY: "TimeoutError: timed out after 30s waiting for the worker",
    Truth.BROKEN: "ImportError: cannot import name 'missing' from 'module'",
    Truth.REGRESSION: "AssertionError: expected 42, got 41",
    Truth.FIXED: "ConnectionRefusedError: connection refused",
    Truth.ORDER_DEPENDENT: "KeyError: 'session' already exists in the registry",
}


def generate_population(
    *,
    seed: int = 1234,
    runs: int = 30,
    flaky: int = 30,
    stable: int = 40,
    broken: int = 8,
    regression: int = 8,
    fixed: int = 6,
    order_dependent: int = 8,
    commit_coverage: float = 1.0,
    runs_per_commit: int = 2,
) -> Population:
    """Build a population of test histories with known labels.

    `commit_coverage` is the fraction of runs that carry a commit SHA, which lets the
    benchmark show how much accuracy depends on the primary signal being available.

    `runs_per_commit` matters more than it looks: same-commit divergence can only be
    observed where a test ran more than once at one SHA, so a value of 1 removes the
    strongest signal even at full coverage.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducibility is the requirement, not secrecy

    commits = _commit_sequence(runs, runs_per_commit, commit_coverage, rng)
    timestamps = [
        f"2026-08-{1 + (i % 28):02d}T{(i // 28) % 24:02d}:00:00+00:00" for i in range(runs)
    ]

    outcomes: list[TestOutcome] = []
    truths: dict[str, GroundTruth] = {}

    for index in range(stable):
        test_id = f"tests/test_stable.py::test_stable_{index}"
        truths[test_id] = GroundTruth(test_id, Truth.STABLE)
        outcomes.extend(_constant(test_id, Status.PASSED, runs, commits, timestamps))

    for index in range(broken):
        test_id = f"tests/test_broken.py::test_broken_{index}"
        truths[test_id] = GroundTruth(test_id, Truth.BROKEN)
        outcomes.extend(
            _constant(test_id, Status.FAILED, runs, commits, timestamps, MESSAGES[Truth.BROKEN])
        )

    for index in range(flaky):
        rate = FLAKE_RATES[index % len(FLAKE_RATES)]
        test_id = f"tests/test_flaky.py::test_flaky_{index}_p{int(rate * 100)}"
        built, observed_failures, observed_passes = _bernoulli(
            test_id, rate, runs, commits, timestamps, MESSAGES[Truth.FLAKY], rng
        )
        # A flake that never failed, or never passed, left no evidence of being
        # flaky. Marking it undetectable keeps the recall figure honest instead of
        # penalising the tool for information it was never given.
        truths[test_id] = GroundTruth(
            test_id,
            Truth.FLAKY,
            failure_rate=rate,
            detectable=observed_failures > 0 and observed_passes > 0,
        )
        outcomes.extend(built)

    for index in range(regression):
        test_id = f"tests/test_regression.py::test_regression_{index}"
        # Half of these have flaky history before the break. That is the case that
        # produced a real misclassification during the first spec.
        noisy = index % 2 == 1
        truths[test_id] = GroundTruth(test_id, Truth.REGRESSION)
        outcomes.extend(
            _regression(test_id, runs, commits, timestamps, rng, noisy_before_break=noisy)
        )

    for index in range(fixed):
        test_id = f"tests/test_fixed.py::test_fixed_{index}"
        truths[test_id] = GroundTruth(test_id, Truth.FIXED)
        outcomes.extend(_fixed(test_id, runs, commits, timestamps, rng))

    for index in range(order_dependent):
        victim = f"tests/test_order.py::test_victim_{index}"
        polluter = f"tests/test_order.py::test_polluter_{index}"
        # Distances cycle so the population spans adjacency and genuine gaps. Without a
        # spread here the harness can only certify a detector that looks one slot back.
        distance = POLLUTER_DISTANCES[index % len(POLLUTER_DISTANCES)]
        truths[victim] = GroundTruth(
            victim,
            Truth.ORDER_DEPENDENT,
            failure_rate=0.5,
            polluter=polluter,
            polluter_distance=distance,
        )
        truths[polluter] = GroundTruth(polluter, Truth.STABLE)
        outcomes.extend(
            _order_dependent(
                victim, polluter, index, runs, commits, timestamps, rng, distance=distance
            )
        )

    return Population(
        outcomes=outcomes,
        truths=truths,
        runs=runs,
        commit_coverage=commit_coverage,
        seed=seed,
    )


def _commit_sequence(
    runs: int, runs_per_commit: int, coverage: float, rng: random.Random
) -> list[str | None]:
    """One commit SHA per run, with `coverage` of them populated."""
    commits: list[str | None] = []
    for index in range(runs):
        if rng.random() > coverage:
            commits.append(None)
        else:
            commits.append(f"commit{index // max(1, runs_per_commit):04d}")
    return commits


def _outcome(
    test_id: str,
    status: Status,
    run: int,
    commits: list[str | None],
    timestamps: list[str],
    message: str | None = None,
    position: int = 0,
) -> TestOutcome:
    return TestOutcome(
        test_id=test_id,
        name=test_id.rsplit("::", 1)[-1],
        status=status,
        message=message,
        signature=message,
        position=position,
        run_uid=f"run-{run:05d}",
        commit_sha=commits[run],
        started_at=timestamps[run],
    )


def _constant(
    test_id: str,
    status: Status,
    runs: int,
    commits: list[str | None],
    timestamps: list[str],
    message: str | None = None,
) -> list[TestOutcome]:
    return [_outcome(test_id, status, run, commits, timestamps, message) for run in range(runs)]


def _bernoulli(
    test_id: str,
    rate: float,
    runs: int,
    commits: list[str | None],
    timestamps: list[str],
    message: str,
    rng: random.Random,
) -> tuple[list[TestOutcome], int, int]:
    """Independent failures at a fixed probability."""
    built: list[TestOutcome] = []
    failures = passes = 0
    for run in range(runs):
        failed = rng.random() < rate
        if failed:
            failures += 1
        else:
            passes += 1
        built.append(
            _outcome(
                test_id,
                Status.FAILED if failed else Status.PASSED,
                run,
                commits,
                timestamps,
                message if failed else None,
            )
        )
    return built, failures, passes


def _regression(
    test_id: str,
    runs: int,
    commits: list[str | None],
    timestamps: list[str],
    rng: random.Random,
    *,
    noisy_before_break: bool,
) -> list[TestOutcome]:
    """Passes, then fails from a break point onward and never recovers.

    With `noisy_before_break`, the test also flakes occasionally *before* the break.
    That is the genuinely ambiguous case, and the one the tool got wrong first time:
    a test with flaky history that has now actually broken.
    """
    break_at = int(runs * 0.7)
    built: list[TestOutcome] = []
    for run in range(runs):
        if run >= break_at:
            failed = True
        elif noisy_before_break:
            failed = rng.random() < 0.2
        else:
            failed = False
        built.append(
            _outcome(
                test_id,
                Status.FAILED if failed else Status.PASSED,
                run,
                commits,
                timestamps,
                MESSAGES[Truth.REGRESSION] if failed else None,
            )
        )
    return built


def _fixed(
    test_id: str,
    runs: int,
    commits: list[str | None],
    timestamps: list[str],
    rng: random.Random,
) -> list[TestOutcome]:
    """Flaky early, then a clean streak long enough to count as fixed."""
    # The streak has to exceed the configured fixed_run_streak (default 10), so the
    # flaky period is capped to leave room even at modest run counts.
    flaky_until = max(2, min(runs - 12, runs // 3))
    built: list[TestOutcome] = []
    for run in range(runs):
        failed = run < flaky_until and rng.random() < 0.5
        built.append(
            _outcome(
                test_id,
                Status.FAILED if failed else Status.PASSED,
                run,
                commits,
                timestamps,
                MESSAGES[Truth.FIXED] if failed else None,
            )
        )
    return built


ORDER_BAND = 100
"""Positions reserved per order-dependent group.

Each group needs its own range with its own filler tests. The first version of this
generator gave every group the same handful of positions and shared filler ids, so
several tests occupied the same position within a run. Predecessor computation sorts
by position, and with ties that ordering is arbitrary -- which made the benchmark
report polluter precision of 0.000 while the detector was working correctly. The
harness was measuring its own bug.
"""


def _order_dependent(
    victim: str,
    polluter: str,
    group: int,
    runs: int,
    commits: list[str | None],
    timestamps: list[str],
    rng: random.Random,
    distance: int = 1,
) -> list[TestOutcome]:
    """The victim fails whenever the polluter ran earlier, at `distance` tests back.

    Order genuinely varies between runs, which is what a shuffling runner produces
    and what the detector needs in order to have anything to correlate. Each group
    occupies its own position band with its own fillers, so positions stay unique
    within a run.

    **`distance` exists because this generator was wrong.** Every polluter used to sit
    immediately before its victim, which encoded the detector's own adjacency assumption
    into the answer key. The benchmark scored order dependence at 1.000 precision and
    recall while the real-world figure was 11.6%, because both halves believed the same
    false thing. Varying the distance is what makes the measurement able to disagree with
    the implementation. See ADR-0014.
    """
    base = ORDER_BAND * (group + 1)
    gap = max(1, distance)
    built: list[TestOutcome] = []

    # Fillers sit between the polluter and the victim, so the gap is real: `distance`
    # other tests genuinely executed in between.
    spacers = [f"tests/test_order.py::test_spacer_{group}_{i}" for i in range(gap - 1)]

    for run in range(runs):
        polluter_first = rng.random() < 0.5

        if polluter_first:
            # An extra leading filler, so the victim lands at a different index than it
            # does in the clean layout. Detection requires the victim's position to vary
            # at all -- a test pinned to one index cannot have its outcome explained by
            # position -- and a generator that pinned it would silently test nothing.
            layout = [
                (f"tests/test_order.py::test_filler_{group}_a", base + 0, True),
                (f"tests/test_order.py::test_filler_{group}_c", base + 1, True),
            ]
            slot = base + 3
            layout.append((polluter, slot, True))
            for spacer in spacers:
                slot += 1
                layout.append((spacer, slot, True))
            layout.append((victim, slot + 1, False))
            layout.append((f"tests/test_order.py::test_filler_{group}_b", slot + 3, True))
        else:
            # The spacers still run *before* the victim, and only the polluter moves to the
            # end. That isolation is the whole point of the layout: if the spacers ran
            # before the victim only in the polluting case, they would be perfectly
            # correlated with the polluter and no detector could tell them apart. Measured
            # with the confounded version, polluter accuracy was 2/8 at every window
            # because the nearest spacer was blamed every time.
            layout = [(f"tests/test_order.py::test_filler_{group}_a", base + 0, True)]
            slot = base + 2
            for spacer in spacers:
                layout.append((spacer, slot, True))
                slot += 1
            layout.append((victim, slot + 1, True))
            layout.append((polluter, slot + 3, True))
            layout.append((f"tests/test_order.py::test_filler_{group}_b", slot + 5, True))

        for test_id, position, passes in layout:
            built.append(
                _outcome(
                    test_id,
                    Status.PASSED if passes else Status.FAILED,
                    run,
                    commits,
                    timestamps,
                    None if passes else MESSAGES[Truth.ORDER_DEPENDENT],
                    position=position,
                )
            )

    return built
