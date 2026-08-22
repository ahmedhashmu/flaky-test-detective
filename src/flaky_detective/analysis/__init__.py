"""Analysis entry point.

Takes outcomes, returns conclusions. Imports no storage and touches no
filesystem: the caller does the query and passes the data in, which is what makes
every number here reproducible in a test with constructed data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ..config import Config
from ..models import (
    AnalysisReport,
    TestAnalysis,
    TestOutcome,
    TriagedFailure,
    TriageReport,
    Verdict,
)
from .classify import classify, remediation_for
from .clustering import cluster_failures
from .comparison import compare
from .flakiness import analyze_test
from .ordering import build_predecessor_index, detect_order_dependence

__all__ = [
    "analyze",
    "analyze_one",
    "build_predecessor_index",
    "classify",
    "cluster_failures",
    "compare",
    "detect_order_dependence",
    "remediation_for",
    "triage",
]


def analyze(outcomes: list[TestOutcome], config: Config | None = None) -> AnalysisReport:
    """Score every test in the given outcomes and cluster their failures."""
    settings = config or Config()
    considered = _apply_ignores(outcomes, settings.ignore)

    by_test: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in considered:
        by_test[outcome.test_id].append(outcome)

    # Built once over the whole dataset; per-test construction would be quadratic.
    predecessors = build_predecessor_index(considered)

    analyses: list[TestAnalysis] = []
    for test_id, test_outcomes in by_test.items():
        analyses.append(analyze_one(test_id, test_outcomes, settings, predecessors=predecessors))

    # Sort by score, then test_id. The explicit tiebreaker keeps output stable
    # between runs when scores tie, which they often do at 0.0.
    analyses.sort(key=lambda a: (-a.score, a.test_id))

    started = [o.started_at for o in considered if o.started_at]
    return AnalysisReport(
        tests=tuple(analyses),
        clusters=cluster_failures(considered),
        total_runs=len({o.run_uid for o in considered if o.run_uid}),
        total_results=len(considered),
        window_start=min(started) if started else None,
        window_end=max(started) if started else None,
        threshold=settings.flake_threshold,
        runs_with_commit=len({o.run_uid for o in considered if o.run_uid and o.commit_sha}),
    )


def analyze_one(
    test_id: str,
    outcomes: list[TestOutcome],
    config: Config | None = None,
    *,
    predecessors: dict[tuple[str, str], str] | None = None,
) -> TestAnalysis:
    """Analyze a single test, including diagnosis.

    Order detection runs before classification because a measured order dependence
    overrides any guess made from the failure text.
    """
    settings = config or Config()

    analysis = analyze_test(
        test_id,
        outcomes,
        threshold=settings.flake_threshold,
        confidence_runs=settings.confidence_runs,
        fixed_run_streak=settings.fixed_run_streak,
    )

    order = detect_order_dependence(outcomes, predecessors)
    messages = [o.message or "" for o in outcomes if o.status.is_failure or o.retried]
    cause = classify(messages, order) if (messages or order) else None

    return replace(analysis, order=order, cause=cause)


def triage(
    run_outcomes: list[TestOutcome],
    history: AnalysisReport,
    *,
    source: str | None = None,
) -> TriageReport:
    """Split one run's failures into known flakes and things needing attention.

    `history` must be an analysis of the accumulated history, and should normally
    exclude the run being triaged: judging a failure partly on the evidence of
    itself would let a first-time failure argue its own way into looking flaky.

    A failure counts as a known flake only if history says FLAKY. A test whose
    history says REGRESSION or BROKEN is reported separately, because those need a
    human even though they are not new.
    """
    known_by_id = {t.test_id: t for t in history.tests}

    known: list[TriagedFailure] = []
    new: list[TriagedFailure] = []
    regressions: list[TriagedFailure] = []

    for outcome in run_outcomes:
        if not (outcome.status.is_failure or outcome.retried):
            continue

        record = known_by_id.get(outcome.test_id)
        entry = TriagedFailure(
            test_id=outcome.test_id,
            name=outcome.name,
            message=outcome.message,
            status=outcome.status,
            known_flake=record is not None and record.verdict is Verdict.FLAKY,
            history=record,
        )

        if entry.known_flake:
            known.append(entry)
        elif record is not None and record.verdict in (Verdict.REGRESSION, Verdict.BROKEN):
            regressions.append(entry)
        else:
            new.append(entry)

    known.sort(key=lambda f: (-f.score, f.test_id))
    new.sort(key=lambda f: f.test_id)
    regressions.sort(key=lambda f: f.test_id)

    commit = next((o.commit_sha for o in run_outcomes if o.commit_sha), None)
    return TriageReport(
        known_flakes=tuple(known),
        new_failures=tuple(new),
        regressions=tuple(regressions),
        source=source,
        commit_sha=commit,
        total_tests=len(run_outcomes),
    )


def _apply_ignores(outcomes: list[TestOutcome], ignore: tuple[str, ...]) -> list[TestOutcome]:
    if not ignore:
        return outcomes
    return [o for o in outcomes if not any(fragment in o.test_id for fragment in ignore)]
