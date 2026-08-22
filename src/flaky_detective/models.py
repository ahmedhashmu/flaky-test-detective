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
    labels: tuple[tuple[str, str], ...] = ()
    """Environment properties for this run, as sorted key/value pairs.

    On the run rather than on each outcome: a large ingest allocates hundreds of thousands
    of `TestOutcome` objects and one of these, so the memory belongs here. Analysis receives
    them as a separate `run_uid -> labels` mapping for the same reason.
    """

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

    polluter_distance: float = 0.0
    """Median gap, in test positions, between the polluter and this test on failures.

    1.0 means immediately before. Reported because it tells a reader how to reproduce the
    failure: a polluter four tests back is not something you would find by running the
    pair together.
    """

    polluter_lift: float = 0.0
    """How much more often this test fails after the polluter than it fails at all.

    A ratio, so 6.0 reads as "six times its usual rate". More interpretable than the
    p-value beside it, and the two are reported together because a large lift measured
    over four runs and a large lift measured over forty are not the same claim.
    """

    polluter_observations: int = 0
    """Runs in which the polluter ran ahead of this test, at any distance in the window."""

    candidates_considered: int = 0
    """How many predecessors were tested before this one was named.

    Carried because it is the multiplicity correction's denominator. Searching a window of
    predecessors instead of only the immediate one means testing several hypotheses per
    victim, and a reader is entitled to know how many, since that is exactly what makes a
    0.05 threshold too generous.
    """


@dataclass(frozen=True, slots=True)
class DimensionAssociation:
    """A recorded property of the environment that a test's failures track.

    "Fails on ARM 19 times in 23, and twice in 46 on x86" is a different kind of finding
    from a timeout guess: it is measured, and it tells you where to reproduce. It is still
    an association and not a mechanism -- ARM runners in a given fleet may also be slower
    or busier -- so the counts travel with it and the wording never claims causation.
    """

    dimension: str
    """The label key, for example `os`, `arch`, `python`, `shard`."""

    value: str
    failures: int
    runs: int
    other_failures: int
    other_runs: int
    lift: float
    probability: float
    values_considered: int = 0
    """How many dimension/value pairs were tested, for the multiplicity correction."""

    covaries_with: tuple[str, ...] = ()
    """Other dimension=value labels that split these runs identically.

    Recorded because confounding is the normal case, not the exception. If every ARM runner
    in a fleet also has two CPUs, then `arch=arm64` and `cpus=2` describe exactly the same
    set of runs and the data cannot tell them apart. Reporting both as separate findings
    would invent a second cause; reporting only the first would hide a real alternative.
    So they are reported together, named as indistinguishable.
    """

    @property
    def is_confounded(self) -> bool:
        return bool(self.covaries_with)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def other_rate(self) -> float:
        return self.other_failures / self.other_runs if self.other_runs else 0.0

    @property
    def summary(self) -> str:
        return f"{self.dimension}={self.value}"


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
    environment: tuple[DimensionAssociation, ...] = ()
    """Environment dimensions whose values this test's failures track, strongest first.

    Empty is the normal case, and also what you get when every run came from one machine:
    a dimension with a single observed value cannot explain anything.
    """

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


DEMO_RUNNER = "flaky-demo"
"""Runner name stamped on every run in a generated demo database.

Lives here, in the leaf module, because two unrelated places need it: the builder that
writes the demo history and the dashboard that has to say out loud that what is on screen
was generated rather than observed. A demo that looks like real test results and turns out
not to be would undo the credibility of everything else in the tool, so the marker travels
with the data instead of relying on the user remembering how they got there.
"""


class Change(StrEnum):
    """What a branch did to one test, relative to the baseline it branched from.

    The distinction that matters is between flakiness a change *introduced* and
    flakiness it merely *inherited*. Blocking a merge for a flake that was already
    there punishes whoever touched the file last, which is how a gate stops being
    trusted; letting a newly introduced flake through is how a suite rots.
    """

    NEW_FLAKE = "new_flake"
    """Stable on the baseline, flaky here, by more than sampling noise explains."""

    NEW_BREAK = "new_break"
    """Passing on the baseline, consistently failing here. Not flakiness: breakage."""

    WORSE = "worse"
    """Flaky on both sides, measurably more so here."""

    KNOWN_FLAKE = "known_flake"
    """Flaky on both sides, not measurably worse. Pre-existing, so not this change's debt."""

    IMPROVED = "improved"
    """Flaky on the baseline, clean here. A candidate fix."""

    UNCHANGED = "unchanged"
    """Nothing worth reporting."""

    UNPROVEN = "unproven"
    """Something moved, and the evidence does not support naming it.

    Its own category rather than being folded into `unchanged`, because "we cannot
    tell" and "nothing happened" are different answers and a gate that conflates them
    is quietly guessing.
    """


@dataclass(frozen=True, slots=True)
class TestComparison:
    """One test, judged across two histories."""

    test_id: str
    name: str
    change: Change

    baseline: TestAnalysis | None
    head: TestAnalysis

    baseline_rate_bound: float = 0.0
    """Upper confidence bound on the baseline failure rate.

    The baseline gets the benefit of the doubt on purpose. Comparing against its
    observed rate would call a change guilty whenever the baseline happened to look
    cleaner than it is; comparing against the highest rate its runs are consistent
    with means a flake has to clear a bar the baseline's own uncertainty already sets.
    """

    probability: float = 1.0
    """Chance of this many failures here if the baseline rate were the bound above.

    Low means the change is the more likely explanation. This is the number the
    verdict rests on, so it is carried rather than discarded after the decision.
    """

    explanation: str = ""

    @property
    def blocks(self) -> bool:
        """Should this stop a merge?

        Only for flakiness or breakage the change introduced. `WORSE` is reported
        loudly and deliberately does not block: a pre-existing flake getting worse is
        often a coincidence of sampling, and blocking on it would make the gate
        unpredictable, which costs more than it saves.
        """
        return self.change in (Change.NEW_FLAKE, Change.NEW_BREAK)

    @property
    def confidence(self) -> str:
        """A word for how much to believe this, derived from the two things that matter.

        Proof means same-commit divergence or a runner-recorded retry in the new runs:
        the test both passed and failed with identical code. Without that, a low
        probability alone is a statistical argument rather than a demonstration, and it
        is reported as the weaker thing it is.
        """
        if self.change in (Change.UNCHANGED, Change.UNPROVEN):
            return "none"
        proven = self.head.divergent_commits > 0 or self.head.retries > 0
        if proven and self.probability <= STRONG_PROBABILITY:
            return "high"
        if proven or self.probability <= STRONG_PROBABILITY:
            return "moderate"
        return "weak"

    @property
    def baseline_summary(self) -> str:
        if self.baseline is None:
            return "not on the baseline"
        return f"{self.baseline.failures}/{self.baseline.runs} failed"

    @property
    def head_summary(self) -> str:
        return f"{self.head.failures}/{self.head.runs} failed"


STRONG_PROBABILITY = 0.01
"""Probability below which the statistical side of a comparison counts as strong.

Ten times stricter than the 0.05 gate that decides whether a change is reported at
all. The gate answers "is this worth saying"; this answers "how firmly", and those
should not be the same number or every reported change would look equally certain.
"""


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Whether a branch introduced flakiness, relative to where it branched from."""

    entries: tuple[TestComparison, ...]

    baseline_runs: int = 0
    head_runs: int = 0
    baseline_label: str | None = None
    head_label: str | None = None
    baseline_tests: int = 0
    head_tests: int = 0

    @property
    def new_flakes(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.NEW_FLAKE)

    @property
    def new_breaks(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.NEW_BREAK)

    @property
    def worse(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.WORSE)

    @property
    def known_flakes(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.KNOWN_FLAKE)

    @property
    def improved(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.IMPROVED)

    @property
    def unproven(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.change is Change.UNPROVEN)

    @property
    def blocking(self) -> tuple[TestComparison, ...]:
        return tuple(e for e in self.entries if e.blocks)

    @property
    def introduced_flakiness(self) -> bool:
        return bool(self.new_flakes)

    @property
    def clean(self) -> bool:
        return not self.blocking

    @property
    def enough_baseline(self) -> bool:
        """Was there enough baseline history for the comparison to mean anything?

        Reported rather than assumed. Comparing a PR against three runs of `main` and
        announcing that it introduced a flake is a guess wearing a verdict's clothes.
        """
        return self.baseline_runs >= MIN_BASELINE_RUNS


MIN_BASELINE_RUNS = 8
"""Baseline runs needed before "this was stable before" is a claim rather than a hope.

Chosen to match the shape of the accuracy sweep rather than by taste: measured over
generated populations, the false-alarm rate at five runs of history is 12.5% and only
reaches zero around ten. Eight is the point where the upper confidence bound on a
clean baseline drops below roughly a third, which is tight enough for a real
regression to clear it and loose enough not to fire on noise.
"""


class FixOutcome(StrEnum):
    """Whether a candidate fix can be believed.

    Three values, not two. "Not yet provable" is a different and much more common answer
    than "not fixed", and collapsing them would make the tool either pessimistic about
    real fixes or credulous about lucky streaks.
    """

    FIXED = "fixed"
    """Clean for longer than the old failure rate explains, with the failing conditions
    actually exercised and nothing else broken."""

    NOT_FIXED = "not_fixed"
    """Still failing."""

    INCONCLUSIVE = "inconclusive"
    """Clean, and not yet clean enough to say -- or clean for the wrong reason."""


@dataclass(frozen=True, slots=True)
class FixVerification:
    """The evidence for or against a candidate fix."""

    test_id: str
    outcome: FixOutcome
    before: TestAnalysis
    after: TestAnalysis

    old_rate_bound: float = 0.0
    """Lower confidence bound on the old failure rate.

    The conservative direction when claiming an improvement: assuming the old rate was as
    low as its data allows makes a clean streak less surprising, so the fix has to work
    harder to be believed.
    """

    probability: float = 1.0
    """Chance of a streak this clean, if the old rate were unchanged."""

    runs_needed: int = 0
    """Clean runs required to clear the bar at the old rate.

    The actionable number. A test that failed 35% of the time needs 8; one that failed 2%
    of the time needs 149, and that is the one people declare fixed after three.
    """

    polluter: str | None = None
    polluter_exposures: int | None = None
    """Times the polluter ran ahead of this test in the new runs. None if not applicable."""

    exposures_needed: int = 0
    collateral: tuple[str, ...] = ()
    """Tests this change made flaky or broke. A fix that moves the problem is not a fix."""

    explanation: str = ""

    @property
    def is_fixed(self) -> bool:
        return self.outcome is FixOutcome.FIXED

    @property
    def clean_runs(self) -> int:
        return self.after.runs if self.after.failures == 0 else 0

    @property
    def rate_reduction(self) -> float:
        """Percentage points of failure rate removed. Negative means it got worse."""
        return self.before.failure_rate - self.after.failure_rate

    @property
    def failures_avoided(self) -> int:
        """Failures the old rate would have produced over the new runs, minus what happened.

        A counterfactual over runs that actually took place, not a projection into the
        future: at the old observed rate these runs would have produced roughly this many
        failures, and they produced fewer. Still an estimate, and labelled as one wherever
        it is displayed, because a rate measured over one window does not have to hold in
        the next.
        """
        expected = self.before.failure_rate * self.after.runs
        return max(0, round(expected - self.after.failures))

    @property
    def exposures_sufficient(self) -> bool:
        """Was the sequence that used to fail actually attempted often enough?"""
        if self.polluter_exposures is None:
            return True
        return self.polluter_exposures >= self.exposures_needed


class Attribution(StrEnum):
    """How much the recorded history can say about when flakiness started."""

    INTRODUCED = "introduced"
    """Divergence appears at a commit, with an observably clean commit before it."""

    PREDATES_HISTORY = "predates_history"
    """Divergence at the earliest recorded commit, so it began before the window."""

    NO_DIVERGENCE = "no_divergence"
    """Never both passed and failed at one commit, so there is nothing to attribute."""

    NO_COMMIT_DATA = "no_commit_data"
    """No run carried a commit SHA."""

    TOO_SPARSE = "too_sparse"
    """No commit ran the test more than once, so divergence was unobservable."""


@dataclass(frozen=True, slots=True)
class CommitWindow:
    """One commit's outcomes for a single test."""

    commit_sha: str
    runs: int
    passes: int
    failures: int
    first_seen: str | None = None

    @property
    def diverged(self) -> bool:
        return self.passes > 0 and self.failures > 0

    @property
    def observable(self) -> bool:
        """Could divergence have been seen here at all?

        One run at a commit proves nothing either way, and reporting it as "did not
        diverge" would imply evidence of stability that does not exist.
        """
        return self.runs > 1


@dataclass(frozen=True, slots=True)
class BlameResult:
    """Where a test's flakiness appears to start, and how much to trust that."""

    test_id: str
    attribution: Attribution
    commit_sha: str | None = None
    previous_clean_sha: str | None = None
    timeline: tuple[CommitWindow, ...] = ()
    observable_commits: int = 0

    @property
    def is_actionable(self) -> bool:
        return self.attribution is Attribution.INTRODUCED

    @property
    def explanation(self) -> str:
        """Plain-language answer, including when there is no answer.

        The unknowable cases get full sentences rather than a shrug, because naming a
        commit the data does not implicate is how someone ends up reverting an
        innocent change.
        """
        if self.attribution is Attribution.INTRODUCED:
            return (
                f"First divergence at {self.commit_sha}, the earliest commit where this "
                f"test both passed and failed. The commit before it "
                f"({self.previous_clean_sha}) ran more than once without diverging."
            )
        if self.attribution is Attribution.PREDATES_HISTORY:
            return (
                f"Already diverging at {self.commit_sha}, the earliest commit in the "
                "recorded history. The flakiness began before this window, so no commit "
                "here can be blamed for introducing it."
            )
        if self.attribution is Attribution.NO_DIVERGENCE:
            return (
                "No commit shows this test both passing and failing, so there is no "
                "divergence to attribute. It may be flaky by flip rate alone, or it may "
                "simply never have run twice on one commit."
            )
        if self.attribution is Attribution.TOO_SPARSE:
            return (
                "No commit ran this test more than once, so divergence could not have "
                "been observed anywhere. Record more than one run per commit -- "
                "`flaky hunt` does that -- and try again."
            )
        return (
            "No run carried a commit SHA, so there is nothing to attribute flakiness "
            "to. Ingest inside a git repository, or pass --commit."
        )


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Summary of one recorded run, for listings and duration statistics."""

    run_uid: str
    started_at: str
    commit_sha: str | None = None
    branch: str | None = None
    runner: str = "unknown"
    iteration: int | None = None
    total: int = 0
    failed: int = 0
    skipped: int = 0
    duration: float | None = None


@dataclass(frozen=True, slots=True)
class HealthComponent:
    """One contribution to the trust score, with its reasoning attached.

    The score is only useful if every point it deducts can be traced to something
    the user can go and look at. A single opaque number would be the same mistake
    this tool exists to correct.
    """

    name: str
    detail: str
    penalty: float
    """Points deducted. Zero means this component is healthy."""

    weight: float = 0.0
    """The most this component could ever deduct, so a reader can see its ceiling."""

    @property
    def is_healthy(self) -> bool:
        return self.penalty == 0.0


@dataclass(frozen=True, slots=True)
class TrustScore:
    """How much the test suite can be believed right now, out of 100.

    Built from figures already collected rather than from a fitted model, so it can
    be explained line by line. `components` accounts for `deducted` exactly, and
    `score` is that deduction subtracted from 100 and rounded to a whole number.
    """

    score: int
    components: tuple[HealthComponent, ...]

    total_tests: int = 0
    stable_tests: int = 0
    active_flakes: int = 0
    unresolved_breaks: int = 0
    commit_coverage: float = 0.0
    quarantine_days_outstanding: int = 0

    wasted_ci_seconds: float = 0.0
    wasted_ci_is_estimate: bool = True
    median_run_seconds: float = 0.0
    flaky_failures: int = 0

    @property
    def band(self) -> str:
        """A word for the number, so a reader does not have to invent thresholds."""
        if self.score >= 90:
            return "healthy"
        if self.score >= 75:
            return "fair"
        if self.score >= 50:
            return "poor"
        return "critical"

    @property
    def stable_share(self) -> float:
        return self.stable_tests / self.total_tests if self.total_tests else 0.0

    @property
    def wasted_ci_minutes(self) -> float:
        return self.wasted_ci_seconds / 60

    @property
    def penalties(self) -> tuple[HealthComponent, ...]:
        """Only the components actually costing points."""
        return tuple(c for c in self.components if not c.is_healthy)

    @property
    def deducted(self) -> float:
        """Points removed from 100, before the score is rounded and clamped.

        Exposed so the headline number can be checked rather than believed: this is
        the exact sum of the component penalties, and `score` is derived from it by
        rounding. Without it, a reader adding up the displayed penalties would find
        them up to half a point short of `100 - score` and have no way to tell
        rounding from a fudge factor.
        """
        return sum(component.penalty for component in self.components)


@dataclass(frozen=True, slots=True)
class DatabaseStats:
    """Summary of what a history database contains."""

    path: str
    runs: int = 0
    results: int = 0
    tests: int = 0
    failures: int = 0
    commits: int = 0
    branches: int = 0
    first_run: str | None = None
    last_run: str | None = None
    runners: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.runs == 0


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
