"""Score the detector against published flaky-test labels from real projects.

The generated benchmark in this package measures the scoring rules against their own
model of the world. Useful, and not sufficient: a detector can fit its own generator
perfectly and still miss real flakiness.

So this module scores the same shipped `analyze()` against
[IDoFT](https://github.com/TestingResearchIllinois/idoft), a dataset of flaky tests in
real repositories, labelled by researchers and in many cases confirmed by the projects'
own maintainers. The labels are not ours, which removes the easiest way to produce a
flattering result.

Pure functions over already-collected data. `validation/run.py` does the cloning,
installing and running; this only scores what came out, so the published numbers can be
recomputed from committed raw results without a network or an afternoon.

Two measures, for a reason explained at length in `validation/README.md`:

**Recall** against the external labels: did we find what humans found?

**Precision** against *observed divergence* rather than against the dataset. IDoFT lists
flaky tests researchers found, not every flaky test that exists, so treating an unlisted
detection as a false positive would understate precision by construction. Instead a
detection counts as correct when the recorded history shows the test passing and failing
at the same commit SHA. That is an observation, not an inference: the code was identical.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import Verdict

ORDER_CATEGORIES = frozenset({"OD", "OD-Vic", "OD-Brit"})
"""IDoFT categories that mean order dependence.

OD-Vic is a victim: passes in the original order, fails after some polluter ran. OD-Brit
is brittle: fails alone, passes once something else has set state up. Both are outcomes
that depend on order, which is what this tool claims to detect.
"""

NONDETERMINISTIC_CATEGORIES = frozenset({"NOD"})
"""Flaky without being order-dependent: timing, concurrency, randomness, network."""

NON_IDEMPOTENT_CATEGORIES = frozenset({"NIO"})
"""Non-idempotent outcome: passes once, fails when re-run inside the same process.

Scored separately and excluded from the headline recall. This tool reads JUnit XML from
separate suite executions, so unless a suite runs a test twice in one session there is
nothing here for it to observe. Folding these into recall would hide a real limitation
behind an average.
"""

EXCLUDED_CATEGORIES = frozenset({"ID", "UD"})
"""Implementation-dependent and undetermined. Neither is a claim this tool makes."""


@dataclass(frozen=True, slots=True)
class TestOutcomeRow:
    """One test as the detector reported it, reduced to what scoring needs."""

    test_id: str
    verdict: str
    score: float
    runs: int
    passes: int
    failures: int
    divergent_commits: int
    retries: int
    cause: str | None
    polluter: str | None

    @property
    def called_flaky(self) -> bool:
        return self.verdict == str(Verdict.FLAKY)

    @property
    def diverged(self) -> bool:
        """Did this test demonstrably produce two different outcomes for one commit?

        The whole basis of the precision measure. A runner-recorded retry counts too:
        the runner watched the test fail and then pass inside a single execution, which
        is the same proof arriving by a different route.
        """
        return self.divergent_commits > 0 or self.retries > 0

    @property
    def always_failed(self) -> bool:
        return self.runs > 0 and self.passes == 0

    @property
    def always_passed(self) -> bool:
        return self.runs > 0 and self.failures == 0 and self.retries == 0


@dataclass(frozen=True, slots=True)
class ProjectScore:
    """How the detector did on one repository."""

    repo: str
    sha: str
    iterations: int
    collected: int
    runs: int
    results: int

    labelled: int = 0
    labelled_scored: int = 0
    """Labels excluding NIO and the categories this tool makes no claim about."""

    executed: int = 0
    """Scored labels whose test actually ran here. A label for a test that no longer
    exists at this SHA in this environment cannot be found or missed."""

    reproduced: int = 0
    """Executed labels that actually varied during our runs."""

    detected: int = 0
    """Reproduced labels the detector called flaky."""

    not_reproducible_passed: int = 0
    not_reproducible_failed: int = 0

    order_labels_detected: int = 0
    order_diagnosed: int = 0
    order_polluter_named: int = 0

    correctly_withheld: int = 0
    """Labelled tests that never passed here, which the detector refused to call flaky.

    The most important row on real data. A test that fails every single run is broken in
    this environment, and calling it flaky because a dataset says it is flaky elsewhere
    would be exactly the false alarm this tool exists not to raise.
    """

    wrongly_called_flaky: int = 0

    flagged: int = 0
    flagged_with_divergence: int = 0
    unlabelled_flagged: int = 0
    unlabelled_flagged_with_divergence: int = 0

    by_category: dict[str, tuple[int, int]] = field(default_factory=dict)
    """category -> (reproduced, detected)."""

    misses: tuple[str, ...] = ()
    suspect: tuple[str, ...] = ()
    """Flagged without observed divergence. Candidate false positives, listed by name so
    they can be inspected rather than counted and forgotten."""

    @property
    def recall(self) -> float:
        return self.detected / self.reproduced if self.reproduced else 0.0

    @property
    def precision(self) -> float:
        return self.flagged_with_divergence / self.flagged if self.flagged else 0.0


@dataclass(frozen=True, slots=True)
class RealWorldResult:
    """The whole evaluation, across every project that could be run."""

    projects: tuple[ProjectScore, ...]
    skipped: tuple[dict[str, str], ...] = ()
    dataset_sha: str = ""

    @property
    def repositories(self) -> int:
        return len(self.projects)

    @property
    def runs(self) -> int:
        return sum(p.runs for p in self.projects)

    @property
    def results(self) -> int:
        return sum(p.results for p in self.projects)

    @property
    def collected(self) -> int:
        return sum(p.collected for p in self.projects)

    @property
    def labelled(self) -> int:
        return sum(p.labelled for p in self.projects)

    @property
    def executed(self) -> int:
        return sum(p.executed for p in self.projects)

    @property
    def reproduced(self) -> int:
        return sum(p.reproduced for p in self.projects)

    @property
    def detected(self) -> int:
        return sum(p.detected for p in self.projects)

    @property
    def flagged(self) -> int:
        return sum(p.flagged for p in self.projects)

    @property
    def flagged_with_divergence(self) -> int:
        return sum(p.flagged_with_divergence for p in self.projects)

    @property
    def not_reproducible(self) -> int:
        return sum(p.not_reproducible_passed + p.not_reproducible_failed for p in self.projects)

    @property
    def correctly_withheld(self) -> int:
        return sum(p.correctly_withheld for p in self.projects)

    @property
    def wrongly_called_flaky(self) -> int:
        return sum(p.wrongly_called_flaky for p in self.projects)

    @property
    def unlabelled_flagged(self) -> int:
        return sum(p.unlabelled_flagged for p in self.projects)

    @property
    def unlabelled_flagged_with_divergence(self) -> int:
        return sum(p.unlabelled_flagged_with_divergence for p in self.projects)

    @property
    def recall(self) -> float:
        return self.detected / self.reproduced if self.reproduced else 0.0

    @property
    def precision(self) -> float:
        return self.flagged_with_divergence / self.flagged if self.flagged else 0.0

    @property
    def order_reproduced(self) -> int:
        return sum(
            p.by_category.get(category, (0, 0))[0]
            for p in self.projects
            for category in ORDER_CATEGORIES
        )

    @property
    def order_detected(self) -> int:
        return sum(p.order_labels_detected for p in self.projects)

    @property
    def order_diagnosed(self) -> int:
        return sum(p.order_diagnosed for p in self.projects)

    @property
    def order_polluter_named(self) -> int:
        return sum(p.order_polluter_named for p in self.projects)

    def category_totals(self) -> dict[str, tuple[int, int]]:
        """category -> (reproduced, detected), summed across projects."""
        reproduced: Counter[str] = Counter()
        detected: Counter[str] = Counter()
        for project in self.projects:
            for category, (seen, found) in project.by_category.items():
                reproduced[category] += seen
                detected[category] += found
        return {c: (reproduced[c], detected[c]) for c in sorted(reproduced)}


def _rows(report: dict[str, Any]) -> dict[str, TestOutcomeRow]:
    """Flatten the JSON report into the handful of fields scoring needs.

    Reads the report's public shape rather than reaching into the analysis objects, so
    this scores exactly what the tool tells the outside world.
    """
    rows: dict[str, TestOutcomeRow] = {}
    for test in report.get("tests", []):
        evidence = test.get("evidence") or {}
        order = test.get("order_dependence") or {}
        cause = test.get("cause") or {}
        rows[test["test_id"]] = TestOutcomeRow(
            test_id=test["test_id"],
            verdict=str(test["verdict"]),
            score=float(test.get("score", 0.0)),
            runs=int(evidence.get("runs", 0)),
            passes=int(evidence.get("passes", 0)),
            failures=int(evidence.get("failures", 0)),
            divergent_commits=int(evidence.get("divergent_commits", 0)),
            retries=int(evidence.get("retries", 0)),
            cause=cause.get("category") if isinstance(cause, dict) else str(cause),
            polluter=order.get("likely_polluter") if isinstance(order, dict) else None,
        )
    return rows


def score_project(raw: dict[str, Any]) -> ProjectScore:
    """Score one project's recorded run against its published labels."""
    report = raw["report"]
    labels: dict[str, str] = raw["labels"]
    rows = _rows(report)
    summary = report.get("summary", {})

    scored = {
        test_id: category
        for test_id, category in labels.items()
        if category not in EXCLUDED_CATEGORIES and category not in NON_IDEMPOTENT_CATEGORIES
    }

    executed = 0
    reproduced = 0
    detected = 0
    nr_passed = 0
    nr_failed = 0
    withheld = 0
    wrongly = 0
    order_detected = 0
    order_diagnosed = 0
    order_polluter = 0
    per_category_seen: Counter[str] = Counter()
    per_category_found: Counter[str] = Counter()
    misses: list[str] = []

    for test_id, category in sorted(scored.items()):
        row = rows.get(test_id)
        if row is None or row.runs == 0:
            continue
        executed += 1

        if not row.diverged:
            # The label says flaky; here the test never varied. Not a detector miss, and
            # saying otherwise would blame it for a flake that did not happen.
            if row.always_failed:
                nr_failed += 1
                if row.called_flaky:
                    wrongly += 1
                else:
                    withheld += 1
            else:
                nr_passed += 1
            continue

        reproduced += 1
        per_category_seen[category] += 1
        if row.called_flaky:
            detected += 1
            per_category_found[category] += 1
            if category in ORDER_CATEGORIES:
                order_detected += 1
                if row.cause == "order_dependence":
                    order_diagnosed += 1
                if row.polluter:
                    order_polluter += 1
        else:
            misses.append(f"{test_id} [{category}] -> {row.verdict}")

    flagged = [row for row in rows.values() if row.called_flaky]
    with_divergence = [row for row in flagged if row.diverged]
    unlabelled = [row for row in flagged if row.test_id not in labels]

    return ProjectScore(
        repo=raw["repo"],
        sha=raw["sha"],
        iterations=int(raw.get("iterations", 0)),
        collected=int(raw.get("collected", 0)),
        runs=int(summary.get("runs", 0)),
        results=int(summary.get("results", 0)),
        labelled=len(labels),
        labelled_scored=len(scored),
        executed=executed,
        reproduced=reproduced,
        detected=detected,
        not_reproducible_passed=nr_passed,
        not_reproducible_failed=nr_failed,
        order_labels_detected=order_detected,
        order_diagnosed=order_diagnosed,
        order_polluter_named=order_polluter,
        correctly_withheld=withheld,
        wrongly_called_flaky=wrongly,
        flagged=len(flagged),
        flagged_with_divergence=len(with_divergence),
        unlabelled_flagged=len(unlabelled),
        unlabelled_flagged_with_divergence=sum(1 for row in unlabelled if row.diverged),
        by_category={
            category: (per_category_seen[category], per_category_found[category])
            for category in sorted(per_category_seen)
        },
        misses=tuple(misses),
        suspect=tuple(sorted(row.test_id for row in flagged if not row.diverged)),
    )


def score_all(
    results: list[dict[str, Any]], *, skipped: list[dict[str, str]] | None = None
) -> RealWorldResult:
    scores = tuple(score_project(raw) for raw in results)
    dataset_shas = {raw.get("dataset_sha", "") for raw in results} - {""}
    return RealWorldResult(
        projects=tuple(sorted(scores, key=lambda p: p.repo)),
        skipped=tuple(skipped or ()),
        dataset_sha=sorted(dataset_shas)[0] if len(dataset_shas) == 1 else "",
    )


def load_results(directory: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read committed raw results from disk.

    Kept here rather than in the CLI so that `flaky validate` stays argument handling,
    and kept out of `score_all` so the scoring itself remains a pure function of data.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"No such directory: {directory}")

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "skipped.json":
            skipped = list(payload)
            continue
        if not isinstance(payload, dict) or "report" not in payload:
            continue
        results.append(payload)

    if not results:
        raise FileNotFoundError(f"No result files in {directory}. Run validation/run.py first.")

    return results, skipped
