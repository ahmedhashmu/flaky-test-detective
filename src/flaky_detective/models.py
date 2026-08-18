"""Core data model.

These types are the contract between ingest, storage, analysis, and reporting.
They import nothing else from the package, which keeps the dependency direction
one-way (see .kiro/steering/structure.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    """Outcome of a single test in a single run."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

    @property
    def is_failure(self) -> bool:
        """Errors count as failures. The distinction matters for display, not detection."""
        return self in (Status.FAILED, Status.ERROR)

    @property
    def is_pass(self) -> bool:
        return self is Status.PASSED

    @property
    def counts_as_evidence(self) -> bool:
        """Skips carry no information about flakiness and are excluded from scoring."""
        return self is not Status.SKIPPED


class Verdict(StrEnum):
    """What we conclude about a test across its whole recorded history.

    Vocabulary is fixed by .kiro/steering/product.md and must not gain synonyms.
    """

    FLAKY = "flaky"
    REGRESSION = "regression"
    BROKEN = "broken"
    FIXED = "fixed"
    STABLE = "stable"


class Cause(StrEnum):
    """Heuristic root-cause category. Always presented with its evidence."""

    TIMEOUT = "timeout"
    RACE = "race"
    ORDER_DEPENDENCE = "order_dependence"
    NETWORK = "network"
    RESOURCE = "resource"
    TIME_DEPENDENCE = "time_dependence"
    RANDOMNESS = "randomness"
    ASSERTION = "assertion"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TestOutcome:
    """One test, one run.

    Allocated once per result row; a large ingest creates hundreds of thousands
    of these, hence slots.
    """

    test_id: str
    name: str
    status: Status
    suite: str | None = None
    duration: float | None = None
    message: str | None = None
    detail: str | None = None
    signature: str | None = None
    position: int | None = None

    # Set when the runner itself recorded a retry for this test: Surefire's
    # <flakyFailure>/<rerunFailure> and pytest-rerunfailures' <rerunFailure>.
    # That is the runner stating outright that the test flaked, which is the
    # strongest single-run evidence available.
    retried: bool = False

    # Denormalized run context, populated when read back from storage so that
    # analysis functions never need to join.
    run_uid: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    started_at: str | None = None
    iteration: int | None = None


@dataclass(frozen=True, slots=True)
class TestRun:
    """One execution of a suite."""

    run_uid: str
    started_at: str
    outcomes: tuple[TestOutcome, ...]
    commit_sha: str | None = None
    branch: str | None = None
    ci_run_id: str | None = None
    source_path: str | None = None
    runner: str = "unknown"
    iteration: int | None = None
    seed: str | None = None
    duration: float | None = None

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status.is_failure)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status is Status.SKIPPED)


@dataclass(frozen=True, slots=True)
class OrderEvidence:
    """Why we think a test depends on execution order."""

    separation: float
    mean_position_on_fail: float
    mean_position_on_pass: float
    likely_polluter: str | None = None
    polluter_failure_share: float = 0.0


@dataclass(frozen=True, slots=True)
class CauseEvidence:
    """Which terms triggered a root-cause guess, so a human can overrule it."""

    cause: Cause
    matched: tuple[str, ...] = ()
    remediation: str = ""
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class TestAnalysis:
    """Everything concluded about one test, with the counts behind it.

    Every derived number here is reproducible from the raw counts, which is a
    requirement: output must be traceable to observations the user can inspect.
    """

    test_id: str
    name: str
    suite: str | None
    verdict: Verdict
    score: float

    runs: int
    passes: int
    failures: int
    skips: int

    flips: int
    flip_rate: float
    divergent_commits: int
    observed_commits: int
    divergence_rate: float
    confidence: float
    retries: int = 0

    first_seen: str | None = None
    last_seen: str | None = None
    last_status: Status | None = None
    consecutive_passes: int = 0

    signatures: tuple[str, ...] = ()
    representative_message: str | None = None
    cause: CauseEvidence | None = None
    order: OrderEvidence | None = None

    @property
    def failure_rate(self) -> float:
        evidence = self.passes + self.failures
        return self.failures / evidence if evidence else 0.0

    @property
    def has_divergence_data(self) -> bool:
        """False when no run carried a commit SHA, which weakens every conclusion."""
        return self.observed_commits > 0


@dataclass(frozen=True, slots=True)
class FailureCluster:
    """A group of failures sharing a normalized signature."""

    signature: str
    representative_message: str
    test_ids: tuple[str, ...]
    failure_count: int
    cause: CauseEvidence | None = None

    @property
    def test_count(self) -> int:
        return len(self.test_ids)


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """The full result of an analysis pass."""

    tests: tuple[TestAnalysis, ...]
    clusters: tuple[FailureCluster, ...]
    total_runs: int
    total_results: int
    window_start: str | None = None
    window_end: str | None = None
    threshold: float = 0.0
    runs_with_commit: int = 0
    skipped_sources: tuple[str, ...] = field(default=())

    @property
    def has_commit_data(self) -> bool:
        """Whether the primary signal was available at all.

        False means no run carried a commit SHA, so same-commit divergence could
        not be computed and every conclusion rests on the weaker flip-rate signal.
        Reports must say so rather than presenting the scores as equally sound.
        """
        return self.runs_with_commit > 0

    @property
    def commit_coverage(self) -> float:
        return self.runs_with_commit / self.total_runs if self.total_runs else 0.0

    @property
    def flaky(self) -> tuple[TestAnalysis, ...]:
        return tuple(t for t in self.tests if t.verdict is Verdict.FLAKY)

    @property
    def regressions(self) -> tuple[TestAnalysis, ...]:
        return tuple(t for t in self.tests if t.verdict is Verdict.REGRESSION)

    @property
    def broken(self) -> tuple[TestAnalysis, ...]:
        return tuple(t for t in self.tests if t.verdict is Verdict.BROKEN)

    @property
    def fixed(self) -> tuple[TestAnalysis, ...]:
        return tuple(t for t in self.tests if t.verdict is Verdict.FIXED)


@dataclass(frozen=True, slots=True)
class TriagedFailure:
    """One failure from a specific run, judged against recorded history."""

    test_id: str
    name: str
    message: str | None
    status: Status
    known_flake: bool
    history: TestAnalysis | None = None

    @property
    def score(self) -> float:
        return self.history.score if self.history else 0.0


@dataclass(frozen=True, slots=True)
class TriageReport:
    """Answer to the question someone on build duty actually has.

    Not "which tests are flaky in general", but "this build is red, do I
    investigate or re-run?". Splitting one run's failures into known flakes and
    new failures answers it directly.
    """

    known_flakes: tuple[TriagedFailure, ...]
    new_failures: tuple[TriagedFailure, ...]
    regressions: tuple[TriagedFailure, ...]
    source: str | None = None
    commit_sha: str | None = None
    total_tests: int = 0

    @property
    def total_failures(self) -> int:
        return len(self.known_flakes) + len(self.new_failures) + len(self.regressions)

    @property
    def actionable(self) -> tuple[TriagedFailure, ...]:
        """Failures that need a human. Known flakes do not."""
        return self.regressions + self.new_failures

    @property
    def all_known_flaky(self) -> bool:
        """True when every failure in the run is already a recorded flake."""
        return self.total_failures > 0 and not self.actionable


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of an ingest batch.

    A malformed artifact must not abort a batch, so failures are collected here
    rather than raised.
    """

    runs_added: int = 0
    runs_skipped: int = 0
    results_added: int = 0
    failures: tuple[tuple[str, str], ...] = ()

    @property
    def had_failures(self) -> bool:
        return bool(self.failures)
