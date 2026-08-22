"""Property-based invariants, checked against generated histories.

The example-based tests in this suite assert *outcomes*: this history produces that
verdict. They are the right tool for pinning a decision, and they share one weakness --
every one of them was written by someone who already had a theory about what mattered.
The three fixture bugs recorded in ADR-0014 all survived example tests for exactly that
reason: the fixtures and the detector were built from the same belief, so no hand-written
case disagreed with either.

These tests assert *relationships* instead, and let Hypothesis look for the history that
breaks them. The properties fall into five groups:

- **Analysis contracts.** A score is a probability-shaped number; a test that never failed
  is never flaky; a test that never passed is never flaky either. The second and third are
  the product's central promise, and a promise worth stating is worth searching for a
  counterexample to.
- **Order independence where it is claimed, and only there.** `analyze_test` documents that
  it needs chronological order, so shuffling everything is *not* an invariant. What is one:
  the order in which different tests are interleaved cannot matter, and the order in which
  runs are ingested cannot matter. Both are what sharded CI actually does.
- **Merge is a set union.** Idempotent and commutative, which is the claim `merge_from`'s
  docstring makes and the thing that makes pooling shards safe.
- **Formatting cannot change a conclusion.** The steering rule is that `report/` must not
  compute. This is that rule as an executable check.
- **Numerical identities.** The binomial helpers underpin five callers now, and a tail that
  disagrees with its own CDF would be wrong quietly, in the fourth decimal place, in a
  direction nobody would notice.

Hypothesis is a development dependency. Nothing in the shipped package imports it.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from flaky_detective.analysis import analyze
from flaky_detective.analysis.statistics import (
    cdf_at_most,
    lower_bound,
    tail_at_least,
    tail_at_most,
    trials_needed,
    upper_bound,
)
from flaky_detective.config import Config
from flaky_detective.models import Status, Verdict
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.normalize import normalize_message, normalize_test_id, signature_of
from flaky_detective.report import json_report
from flaky_detective.reproduce import ddmin
from flaky_detective.storage import Storage, StorageError

settings.register_profile(
    "ftd",
    deadline=None,
    max_examples=60,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("ftd")

TEST_IDS = [
    "tests/test_api.py::test_create",
    "tests/test_api.py::test_delete",
    "tests/test_worker.py::test_drain",
    "tests/test_worker.py::test_enqueue",
    "tests/test_cache.py::test_evict",
]

COMMITS = ["a1b2c3d", "e4f5a6b", "c7d8e9f", None]

STATUSES = [Status.PASSED, Status.FAILED, Status.ERROR, Status.SKIPPED]

MESSAGES = [
    None,
    "AssertionError: expected 3, got 4",
    "TimeoutError: waited 30s for the worker",
    "ConnectionResetError: [Errno 104] peer reset",
]


@st.composite
def histories(
    draw: st.DrawFn,
    *,
    min_runs: int = 1,
    max_runs: int = 10,
    max_tests: int = 4,
) -> list[Run]:
    """Generate a plausible recorded history as a list of runs.

    Two constraints are deliberate rather than incidental:

    `started_at` values are distinct and increasing. Storage orders by
    `(started_at, id)`, so runs recorded in the same second fall back to insertion
    order -- which means ingest-order independence is only claimable when timestamps
    distinguish the runs, and a generator that produced ties would be manufacturing a
    counterexample to a property nobody holds.

    Each run's `position` values reflect a real execution order, because order-dependence
    detection reads them. Generating positions independently of the outcome list would
    produce histories no runner could emit.
    """
    names = draw(st.lists(st.sampled_from(TEST_IDS), min_size=1, max_size=max_tests, unique=True))
    run_count = draw(st.integers(min_value=min_runs, max_value=max_runs))

    runs: list[Run] = []
    for index in range(run_count):
        order = draw(st.permutations(names))
        commit = draw(st.sampled_from(COMMITS))
        outcomes: list[Outcome] = []

        for position, test_id in enumerate(order):
            status = draw(st.sampled_from(STATUSES))
            retried = draw(st.booleans()) if status is Status.PASSED else False
            message = draw(st.sampled_from(MESSAGES)) if (status.is_failure or retried) else None
            outcomes.append(
                Outcome(
                    test_id=test_id,
                    name=test_id.rsplit("::", 1)[-1],
                    status=status,
                    message=message,
                    signature=message,
                    position=position,
                    retried=retried,
                )
            )

        runs.append(
            Run(
                run_uid=f"run-{index:03d}",
                started_at=f"2026-08-01T00:{index:02d}:00+00:00",
                outcomes=tuple(outcomes),
                commit_sha=commit,
                runner="pytest",
            )
        )

    return runs


def flatten(runs: list[Run]) -> list[Outcome]:
    """Runs to the denormalized outcome list analysis consumes, run-major."""
    flat: list[Outcome] = []
    for run in runs:
        for item in run.outcomes:
            flat.append(
                Outcome(
                    test_id=item.test_id,
                    name=item.name,
                    status=item.status,
                    message=item.message,
                    signature=item.signature,
                    position=item.position,
                    retried=item.retried,
                    run_uid=run.run_uid,
                    commit_sha=run.commit_sha,
                    started_at=run.started_at,
                )
            )
    return flat


def verdicts(report: Any) -> dict[str, tuple[str, float]]:
    return {t.test_id: (str(t.verdict), t.score) for t in report.tests}


class TestAnalysisContracts:
    @given(histories())
    def test_score_is_probability_shaped(self, runs: list[Run]) -> None:
        for test in analyze(flatten(runs)).tests:
            assert 0.0 <= test.score <= 1.0, f"{test.test_id} scored {test.score}"

    @given(histories())
    def test_a_test_that_never_failed_is_stable(self, runs: list[Run]) -> None:
        """No failure and no retry is no evidence, and no evidence is no verdict."""
        for test in analyze(flatten(runs)).tests:
            if test.failures == 0 and test.retries == 0:
                assert test.verdict is Verdict.STABLE

    @given(histories())
    def test_a_test_that_never_passed_is_never_flaky(self, runs: list[Run]) -> None:
        """The product's central promise, as a searched-for property.

        Calling a consistent failure flaky teaches a reader to re-run instead of
        investigate, which is the habit this tool exists to break.
        """
        for test in analyze(flatten(runs)).tests:
            if test.passes == 0 and test.runs > 0:
                assert test.verdict is not Verdict.FLAKY, (
                    f"{test.test_id} never passed in {test.runs} runs and was called flaky"
                )

    @given(histories())
    def test_flaky_requires_both_a_pass_and_a_failure(self, runs: list[Run]) -> None:
        """Flaky means "different outcomes for the same code". Both halves required."""
        for test in analyze(flatten(runs)).tests:
            if test.verdict is Verdict.FLAKY:
                assert test.passes > 0
                assert test.failures > 0 or test.retries > 0

    @given(histories())
    def test_every_test_seen_is_reported_exactly_once(self, runs: list[Run]) -> None:
        outcomes = flatten(runs)
        report = analyze(outcomes)
        reported = [t.test_id for t in report.tests]
        assert sorted(reported) == sorted({o.test_id for o in outcomes})
        assert len(reported) == len(set(reported))

    @given(histories())
    def test_totals_match_the_input(self, runs: list[Run]) -> None:
        outcomes = flatten(runs)
        report = analyze(outcomes)
        assert report.total_results == len(outcomes)
        assert report.total_runs == len({o.run_uid for o in outcomes})

    @given(histories())
    def test_ranking_is_by_score_then_id(self, runs: list[Run]) -> None:
        """The explicit tiebreaker matters: scores tie at 0.00 constantly."""
        tests = analyze(flatten(runs)).tests
        keys = [(-t.score, t.test_id) for t in tests]
        assert keys == sorted(keys)

    @given(histories())
    def test_run_and_pass_and_failure_counts_agree(self, runs: list[Run]) -> None:
        for test in analyze(flatten(runs)).tests:
            assert test.passes + test.failures == test.runs
            assert test.runs >= 0


class TestTheGeneratorIsNotVacuous:
    """A property that never sees the interesting case passes and proves nothing.

    This is the failure mode property-based testing invites: narrow the strategy just
    enough to make everything green, and end up with a suite that asserts confidently
    about histories no runner could produce. The invariant "a test that never passed is
    never flaky" is worthless if no generated history ever contains a test that never
    passed.

    So the generator's own reach is asserted. Measured over 400 examples while this was
    written: 600 flaky, 247 broken, 45 regression, 160 stable, with 514 tests showing
    same-commit divergence and 433 carrying runner-recorded retries.
    """

    def test_it_reaches_every_state_the_properties_guard(self) -> None:
        seen: Counter[str] = Counter()

        @settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))
        @given(histories())
        def collect(runs: list[Run]) -> None:
            for test in analyze(flatten(runs)).tests:
                seen[str(test.verdict)] += 1
                if test.passes == 0 and test.runs > 0:
                    seen["never_passed"] += 1
                if test.passes > 0 and test.failures > 0:
                    seen["diverged"] += 1
                if test.retries > 0:
                    seen["retried"] += 1

        collect()

        required = (
            "flaky",
            "broken",
            "regression",
            "stable",
            "never_passed",
            "diverged",
            "retried",
        )
        missing = [state for state in required if not seen[state]]
        assert not missing, (
            f"the generator never produced: {', '.join(missing)}. Every property about "
            f"those states is passing vacuously. Saw: {dict(seen)}"
        )


class TestDeterminism:
    @given(histories())
    def test_analysis_is_deterministic(self, runs: list[Run]) -> None:
        """Same input, same output, including order.

        Not a tautology for a function that groups with dicts and de-duplicates with
        sets: string hashing is randomized per process, so an accidental dependence on
        set iteration order would produce output that diffs between runs.
        """
        outcomes = flatten(runs)
        assert analyze(outcomes) == analyze(list(outcomes))

    @given(histories())
    def test_interleaving_between_tests_cannot_change_a_verdict(self, runs: list[Run]) -> None:
        """Grouping is by test id, so how tests interleave must not matter.

        Each test's own outcomes stay in chronological order in both arrangements --
        `analyze_test` documents that it requires that, so a full shuffle would violate
        a stated precondition rather than test one.
        """
        run_major = flatten(runs)
        test_major = sorted(run_major, key=lambda o: o.test_id)  # stable: preserves per-test order
        assert verdicts(analyze(run_major)) == verdicts(analyze(test_major))

    @given(histories())
    def test_duplicated_input_does_not_change_the_verdict_direction(self, runs: list[Run]) -> None:
        """Presenting the same history twice must not invent evidence.

        Analysis cannot deduplicate -- it is handed a list and two identical rows are
        indistinguishable from a genuine retry -- so this asserts what is actually
        guaranteed: the run count doubles and no test crosses from stable into flaky on
        nothing but repetition of the same observations.
        """
        outcomes = flatten(runs)
        once = {t.test_id: t for t in analyze(outcomes).tests}
        twice = {t.test_id: t for t in analyze(outcomes + outcomes).tests}

        for test_id, before in once.items():
            after = twice[test_id]
            if before.verdict is Verdict.STABLE and before.failures == 0:
                assert after.verdict is Verdict.STABLE, (
                    f"{test_id} became {after.verdict} from duplicated passes alone"
                )


class TestStorageIsASet:
    """Merge is a set union over content-addressed run ids.

    Every property here runs against real SQLite. A merge that only worked on
    constructed objects would prove nothing about the file two machines exchange.
    """

    @staticmethod
    def _write(runs: list[Run], path: Path) -> None:
        with Storage(path) as store:
            for run in runs:
                store.add_run(run)

    @given(histories())
    def test_reingesting_a_run_adds_nothing(self, runs: list[Run]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.db"
            with Storage(path) as store:
                for run in runs:
                    _, inserted = store.add_run(run)
                    assert inserted

                for run in runs:
                    _, inserted = store.add_run(run)
                    assert not inserted, f"{run.run_uid} was inserted twice"

                assert len(store.outcomes()) == sum(len(r.outcomes) for r in runs)

    @given(histories())
    def test_a_round_trip_preserves_every_outcome(self, runs: list[Run]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.db"
            self._write(runs, path)
            with Storage(path) as store:
                stored = store.outcomes()

        written = flatten(runs)
        assert len(stored) == len(written)

        def key(item: Outcome) -> tuple[str, str, str, int, bool]:
            return (
                item.run_uid or "",
                item.test_id,
                str(item.status),
                item.position or 0,
                item.retried,
            )

        assert sorted(key(o) for o in stored) == sorted(key(o) for o in written)

    @given(histories(), histories())
    def test_merge_is_idempotent(self, left: list[Run], right: list[Run]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base, other = Path(directory) / "a.db", Path(directory) / "b.db"
            self._write(left, base)
            self._write(_relabel(right, "b"), other)

            with Storage(base) as store:
                first = store.merge_from(other)
                second = store.merge_from(other)
                assert second.runs_added == 0, "merging the same source twice added runs"
                assert first.runs_added >= 0

    @given(histories(), histories())
    def test_merge_is_commutative(self, left: list[Run], right: list[Run]) -> None:
        """A + B and B + A must agree, or pooling shards depends on arrival order."""
        right = _relabel(right, "b")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(left, root / "left.db")
            self._write(right, root / "right.db")

            self._write(left, root / "forward.db")
            with Storage(root / "forward.db") as store:
                store.merge_from(root / "right.db")
                forward = analyze(store.outcomes())

            self._write(right, root / "backward.db")
            with Storage(root / "backward.db") as store:
                store.merge_from(root / "left.db")
                backward = analyze(store.outcomes())

        assert verdicts(forward) == verdicts(backward)
        assert forward.total_runs == backward.total_runs
        assert forward.total_results == backward.total_results

    @given(histories(), histories())
    def test_merge_yields_the_union_of_run_ids(self, left: list[Run], right: list[Run]) -> None:
        right = _relabel(right, "b")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(left, root / "a.db")
            self._write(right, root / "b.db")
            with Storage(root / "a.db") as store:
                store.merge_from(root / "b.db")
                merged = {o.run_uid for o in store.outcomes()}

        expected = {r.run_uid for r in left} | {r.run_uid for r in right}
        assert merged == expected

    @given(histories())
    def test_merging_a_database_into_itself_is_refused(self, runs: list[Run]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.db"
            self._write(runs, path)
            with Storage(path) as store, pytest.raises(StorageError, match="into itself"):
                store.merge_from(path)

    @given(histories())
    def test_ingest_order_cannot_change_a_verdict(self, runs: list[Run]) -> None:
        """Shards arrive in whatever order the network delivers them.

        Storage sorts by `(started_at, id)` on read, so inserting the runs backwards must
        produce the same analysis. This is the property that makes `flaky merge` safe, and
        it is checked through the database rather than around it.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(runs, root / "forward.db")
            self._write(list(reversed(runs)), root / "backward.db")

            with Storage(root / "forward.db") as store:
                forward = analyze(store.outcomes())
            with Storage(root / "backward.db") as store:
                backward = analyze(store.outcomes())

        assert verdicts(forward) == verdicts(backward)


def _relabel(runs: list[Run], prefix: str) -> list[Run]:
    """Give a second history distinct run ids and timestamps.

    Without this the two generated databases would collide on `run-000`, and every merge
    property would be testing the empty case.
    """
    return [
        Run(
            run_uid=f"{prefix}-{run.run_uid}",
            started_at=f"2026-09-01T00:{index:02d}:00+00:00",
            outcomes=run.outcomes,
            commit_sha=run.commit_sha,
            runner=run.runner,
        )
        for index, run in enumerate(runs)
    ]


class TestFormattingCannotCompute:
    """`report/` must not compute. The steering rule, made executable."""

    @given(histories())
    def test_json_reports_the_verdicts_analysis_reached(self, runs: list[Run]) -> None:
        report = analyze(flatten(runs))
        payload = json.loads(json_report.render_report(report))
        rendered = {t["test_id"]: (t["verdict"], t["score"]) for t in payload["tests"]}
        assert rendered == verdicts(report)

    @given(histories())
    def test_json_is_valid_for_any_history(self, runs: list[Run]) -> None:
        payload = json.loads(json_report.render_report(analyze(flatten(runs))))
        assert isinstance(payload["tests"], list)

    @given(histories(), st.floats(min_value=0.01, max_value=0.99))
    def test_the_threshold_only_moves_the_flaky_boundary(
        self, runs: list[Run], threshold: float
    ) -> None:
        """Lowering the threshold can only add flaky labels, never remove them.

        A monotonicity claim worth checking because the verdict ladder puts broken,
        regression and fixed *ahead* of flaky, so a threshold change must not be able to
        disturb any of those.
        """
        outcomes = flatten(runs)
        strict = analyze(outcomes, Config(flake_threshold=min(0.99, threshold + 0.01)))
        loose = analyze(outcomes, Config(flake_threshold=threshold))

        strict_by_id = {t.test_id: t for t in strict.tests}
        for test in loose.tests:
            counterpart = strict_by_id[test.test_id]
            if counterpart.verdict is Verdict.FLAKY:
                assert test.verdict is Verdict.FLAKY, (
                    f"{test.test_id} was flaky at the stricter threshold and is "
                    f"{test.verdict} at the looser one"
                )


class TestBinomialIdentities:
    counts = st.integers(min_value=0, max_value=60)
    rates = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

    @given(counts, st.integers(min_value=1, max_value=60), rates)
    def test_a_cdf_is_a_probability(self, successes: int, trials: int, rate: float) -> None:
        assert 0.0 <= cdf_at_most(successes, trials, rate) <= 1.0

    @given(st.integers(min_value=1, max_value=40), st.floats(min_value=0.01, max_value=0.99))
    def test_the_cdf_never_decreases(self, trials: int, rate: float) -> None:
        values = [cdf_at_most(k, trials, rate) for k in range(trials + 1)]
        assert values == sorted(values)

    @given(st.integers(min_value=1, max_value=40), st.floats(min_value=0.01, max_value=0.99))
    def test_the_upper_tail_never_increases(self, trials: int, rate: float) -> None:
        values = [tail_at_least(k, trials, rate) for k in range(trials + 1)]
        assert values == sorted(values, reverse=True)

    @given(
        st.integers(min_value=1, max_value=40),
        st.integers(min_value=1, max_value=40),
        st.floats(min_value=0.01, max_value=0.99),
    )
    def test_the_tail_and_the_cdf_partition_one(
        self, successes: int, trials: int, rate: float
    ) -> None:
        """P(X >= k) + P(X <= k-1) == 1.

        Two independent summations over the same distribution. They lived as separate
        ad-hoc calculations in three modules before `statistics.py` existed, and this is
        the identity that would have caught the disagreement.
        """
        assume(successes <= trials)
        total = tail_at_least(successes, trials, rate) + cdf_at_most(successes - 1, trials, rate)
        assert math.isclose(total, 1.0, abs_tol=1e-9)

    @given(st.integers(min_value=1, max_value=40), st.floats(min_value=0.01, max_value=0.99))
    def test_the_whole_distribution_sums_to_one(self, trials: int, rate: float) -> None:
        assert math.isclose(cdf_at_most(trials, trials, rate), 1.0, abs_tol=1e-9)
        assert math.isclose(tail_at_most(trials, trials, rate), 1.0, abs_tol=1e-9)

    @given(st.integers(min_value=0, max_value=40), st.integers(min_value=1, max_value=40))
    def test_the_confidence_interval_contains_the_observation(
        self, successes: int, trials: int
    ) -> None:
        """Clopper-Pearson brackets the observed rate. If it did not, every claim built
        on it -- "no failures in 40 runs still admits 7%" -- would be arbitrary."""
        assume(successes <= trials)
        observed = successes / trials
        low, high = lower_bound(successes, trials), upper_bound(successes, trials)

        assert 0.0 <= low <= 1.0
        assert 0.0 <= high <= 1.0
        assert low <= observed + 1e-9
        assert observed <= high + 1e-9

    @given(st.floats(min_value=0.001, max_value=0.999))
    def test_trials_needed_actually_beats_chance(self, rate: float) -> None:
        """The number it returns has to do the job it is quoted for."""
        needed = trials_needed(rate)
        assert needed >= 1
        assert (1.0 - rate) ** needed <= 0.05 + 1e-12

    @given(st.floats(min_value=0.01, max_value=0.5), st.floats(min_value=0.01, max_value=0.5))
    def test_rarer_flakes_need_more_clean_runs(self, first: float, second: float) -> None:
        low, high = min(first, second), max(first, second)
        assert trials_needed(low) >= trials_needed(high)


class TestDeltaDebuggingInvariants:
    """`ddmin` is where a subtle bug would be most expensive.

    A wrong reduction still prints a command, and the command still fails, so the mistake
    reaches a user looking exactly like a correct answer. These properties hold for any
    oracle, which is the only way to check it without a real suite.
    """

    items = st.lists(st.integers(min_value=0, max_value=40), min_size=0, max_size=24, unique=True)

    @given(items, st.integers(min_value=0, max_value=40))
    def test_the_result_is_an_ordered_subset(self, candidates: list[int], culprit: int) -> None:
        names = [str(c) for c in candidates]
        target = str(culprit)
        result = ddmin(names, lambda seq: target in seq)

        assert set(result.subset) <= set(names)
        assert list(result.subset) == [n for n in names if n in set(result.subset)]

    @given(items)
    def test_a_reduction_still_reproduces(self, candidates: list[int]) -> None:
        """The defining guarantee: whatever it returns must still satisfy the oracle."""
        names = [str(c) for c in candidates]
        assume(len(names) >= 2)
        target = names[len(names) // 2]

        def oracle(sequence: Any) -> bool:
            return target in sequence

        result = ddmin(names, oracle)
        assert oracle(result.subset)

    @given(items)
    def test_a_single_culprit_is_isolated(self, candidates: list[int]) -> None:
        names = [str(c) for c in candidates]
        assume(names)
        target = names[-1]
        result = ddmin(names, lambda seq: target in seq)
        assert result.subset == (target,)

    @given(items, st.integers(min_value=1, max_value=30))
    def test_the_budget_is_never_exceeded(self, candidates: list[int], budget: int) -> None:
        names = [str(c) for c in candidates]
        result = ddmin(names, lambda seq: len(seq) == len(names), budget=budget)
        assert result.calls <= budget

    @given(items)
    def test_two_runs_agree(self, candidates: list[int]) -> None:
        names = [str(c) for c in candidates]
        assume(names)
        target = names[0]
        first = ddmin(names, lambda seq: target in seq)
        second = ddmin(names, lambda seq: target in seq)
        assert first == second

    @given(items)
    def test_an_oracle_that_needs_everything_gets_everything(self, candidates: list[int]) -> None:
        names = [str(c) for c in candidates]
        result = ddmin(names, lambda seq: len(seq) == len(names))
        assert result.subset == tuple(names)


class TestNormalizationIsStable:
    text = st.text(min_size=0, max_size=120)

    @given(text)
    def test_normalization_is_deterministic(self, message: str) -> None:
        assert normalize_message(message) == normalize_message(message)

    @given(text)
    def test_a_signature_is_always_a_usable_key(self, message: str) -> None:
        signature = signature_of(message)
        assert isinstance(signature, str)
        assert "\n" not in signature, "a cluster key spanning lines breaks every report"

    @given(text, text)
    def test_signatures_collide_only_by_normalization(self, first: str, second: str) -> None:
        """Equal normalizations must give equal signatures, and vice versa.

        The signature is the cluster key; if two messages normalize the same way and
        signature differently, clustering silently splits one incident into two.
        """
        same_normal = normalize_message(first) == normalize_message(second)
        same_signature = signature_of(first) == signature_of(second)
        assert same_normal == same_signature

    @given(st.sampled_from(TEST_IDS))
    def test_normalizing_a_test_id_is_idempotent(self, test_id: str) -> None:
        once = normalize_test_id(test_id)
        assert normalize_test_id(once) == once
