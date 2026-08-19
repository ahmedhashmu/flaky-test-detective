"""JSON payloads for the dashboard.

Serialization only. Every number here comes from `analysis/`, the same code the CLI
uses, so the dashboard and the terminal cannot disagree about a verdict. Nothing in
this module computes a score.

Kept free of any web framework: the payloads are plain dicts, which makes them
testable without starting a server and keeps runtime dependencies at typer and rich.
"""

from __future__ import annotations

from typing import Any

from ..analysis import analyze, triage
from ..analysis.attribution import blame
from ..analysis.health import WASTED_TIME_ASSUMPTION, median_run_duration, trust_score
from ..analysis.ordering import build_predecessor_index
from ..config import Config
from ..models import AnalysisReport, TestAnalysis, TestOutcome, TrustScore, Verdict
from ..quarantine import Quarantine, recommend
from ..storage import Storage

API_VERSION = 1

VERDICT_TONE = {
    Verdict.FLAKY: "warning",
    Verdict.REGRESSION: "error",
    Verdict.BROKEN: "error",
    Verdict.FIXED: "success",
    Verdict.STABLE: "neutral",
}
"""Maps a verdict to a semantic tone the UI can theme.

Here rather than in the frontend so that the terminal, the Markdown report and the
dashboard all agree on which verdicts are alarming.
"""


def overview_payload(
    store: Storage, config: Config, *, quarantine: Quarantine | None = None
) -> dict[str, Any]:
    """Everything the home screen needs, in one request.

    One round trip on purpose. The dashboard's job is to answer "can I trust my CI
    right now" immediately, and a screen that assembles itself from six requests does
    not feel immediate.
    """
    outcomes = store.outcomes()
    report = analyze(outcomes, config)
    stats = store.stats()

    days_outstanding = _quarantine_days_outstanding(quarantine)
    durations = [run.duration or 0.0 for run in store.recent_runs(limit=200)]
    score = trust_score(
        report,
        quarantine_days_outstanding=days_outstanding,
        median_run_seconds=median_run_duration(durations),
    )

    return {
        "api_version": API_VERSION,
        "trust": _trust_payload(score),
        "summary": {
            "runs": report.total_runs,
            "results": report.total_results,
            "tests": len(report.tests),
            "flaky": len(report.flaky),
            "regressions": len(report.regressions),
            "broken": len(report.broken),
            "fixed": len(report.fixed),
            "stable": score.stable_tests,
            "has_commit_data": report.has_commit_data,
            "commit_coverage": round(report.commit_coverage, 4),
            "threshold": report.threshold,
            "window_start": report.window_start,
            "window_end": report.window_end,
            "runners": stats.runners,
        },
        "tests": [test_summary(test) for test in report.tests],
        "clusters": [
            {
                "signature": cluster.signature,
                "representative_message": cluster.representative_message,
                "test_ids": list(cluster.test_ids),
                "test_count": cluster.test_count,
                "failure_count": cluster.failure_count,
                "cause": str(cluster.cause.cause) if cluster.cause else None,
            }
            for cluster in report.clusters
            if cluster.test_count > 1
        ][:10],
        "quarantine": _quarantine_payload(quarantine, report, config),
        "caveats": _caveats(report, score),
    }


def _trust_payload(score: TrustScore) -> dict[str, Any]:
    return {
        "score": score.score,
        "band": score.band,
        "deducted": round(score.deducted, 1),
        "components": [
            {
                "name": component.name,
                "detail": component.detail,
                "penalty": component.penalty,
                "weight": component.weight,
                "healthy": component.is_healthy,
            }
            for component in score.components
        ],
        "facts": {
            "total_tests": score.total_tests,
            "stable_tests": score.stable_tests,
            "stable_share": round(score.stable_share, 4),
            "active_flakes": score.active_flakes,
            "unresolved_breaks": score.unresolved_breaks,
            "commit_coverage": round(score.commit_coverage, 4),
            "quarantine_days_outstanding": score.quarantine_days_outstanding,
        },
        "wasted_ci": {
            "seconds": round(score.wasted_ci_seconds, 1),
            "minutes": round(score.wasted_ci_minutes, 1),
            "flaky_failures": score.flaky_failures,
            "median_run_seconds": round(score.median_run_seconds, 2),
            "is_estimate": True,
            "assumption": WASTED_TIME_ASSUMPTION,
        },
    }


def test_summary(test: TestAnalysis) -> dict[str, Any]:
    """One row of the ranked table.

    Carries the counts behind the score, not just the score, so the table itself is
    checkable without opening a detail page.
    """
    return {
        "test_id": test.test_id,
        "name": test.name,
        "suite": test.suite,
        "verdict": str(test.verdict),
        "tone": VERDICT_TONE.get(test.verdict, "neutral"),
        "score": test.score,
        "confidence": round(test.confidence, 4),
        "runs": test.runs,
        "passes": test.passes,
        "failures": test.failures,
        "skips": test.skips,
        "flips": test.flips,
        "failure_rate": round(test.failure_rate, 4),
        "divergent_commits": test.divergent_commits,
        "observed_commits": test.observed_commits,
        "retries": test.retries,
        "cause": str(test.cause.cause) if test.cause else None,
        "cause_confidence": test.cause.confidence if test.cause else None,
        "polluter": test.order.likely_polluter if test.order else None,
        "last_seen": test.last_seen,
        "first_seen": test.first_seen,
        "last_status": str(test.last_status) if test.last_status else None,
        "signature_count": len(test.signatures),
    }


def test_detail_payload(
    store: Storage, config: Config, test_id: str, *, quarantine: Quarantine | None = None
) -> dict[str, Any] | None:
    """Everything the investigation page needs for one test.

    Returns None when the test is unknown, so the caller can answer 404 rather than
    inventing an empty record.
    """
    outcomes = store.outcomes_for_test(test_id)
    if not outcomes:
        return None

    all_outcomes = store.outcomes()
    report = analyze(all_outcomes, config)
    test = next((t for t in report.tests if t.test_id == test_id), None)
    if test is None:
        return None

    attribution = blame(test_id, outcomes)
    predecessors = build_predecessor_index(all_outcomes)

    return {
        "api_version": API_VERSION,
        "test": test_summary(test),
        "evidence": _evidence(test),
        "timeline": [
            {
                "started_at": outcome.started_at,
                "status": str(outcome.status),
                "failed": outcome.status.is_failure,
                "commit_sha": outcome.commit_sha,
                "branch": outcome.branch,
                "iteration": outcome.iteration,
                "retried": outcome.retried,
                "duration": outcome.duration,
                "message": outcome.message,
                "position": outcome.position,
            }
            for outcome in outcomes
        ],
        "diagnosis": _diagnosis(test),
        "blame": {
            "attribution": str(attribution.attribution),
            "actionable": attribution.is_actionable,
            "commit_sha": attribution.commit_sha,
            "previous_clean_sha": attribution.previous_clean_sha,
            "explanation": attribution.explanation,
            "observable_commits": attribution.observable_commits,
            "commits": [
                {
                    "commit_sha": window.commit_sha,
                    "runs": window.runs,
                    "passes": window.passes,
                    "failures": window.failures,
                    "diverged": window.diverged,
                    "observable": window.observable,
                    "first_seen": window.first_seen,
                }
                for window in attribution.timeline
            ],
        },
        "signatures": _signatures(outcomes),
        "neighbours": _neighbours(test_id, outcomes, predecessors),
        "quarantined": _quarantine_entry(quarantine, test_id),
        "actions": _actions(test),
    }


def _evidence(test: TestAnalysis) -> dict[str, Any]:
    """The proof, separated from the guesswork.

    The UI renders these two groups differently on purpose. A measured fact and a
    pattern match should not look alike, or the weaker one borrows the authority of
    the stronger.
    """
    proven: list[dict[str, str]] = []
    inferred: list[dict[str, str]] = []

    if test.divergent_commits:
        proven.append(
            {
                "label": "Same-commit divergence",
                "detail": (
                    f"Passed and failed at the same commit in "
                    f"{test.divergent_commits} of {test.observed_commits} commits where "
                    "it ran more than once. The code was identical, so the code is not "
                    "the variable."
                ),
            }
        )
    if test.retries:
        proven.append(
            {
                "label": "Runner-recorded retry",
                "detail": (
                    f"The test runner itself reported {test.retries} "
                    f"{'retry' if test.retries == 1 else 'retries'}: it watched this "
                    "test fail and then pass inside a single run."
                ),
            }
        )
    if test.order and test.order.likely_polluter:
        proven.append(
            {
                "label": "Polluter correlation",
                "detail": (
                    f"Fails after {test.order.likely_polluter} in "
                    f"{test.order.polluter_failure_share:.0%} of its failures, more "
                    "often than its own base failure rate explains."
                ),
            }
        )

    if test.flips:
        inferred.append(
            {
                "label": "Flip rate",
                "detail": (
                    f"{test.flips} pass/fail transitions across {test.runs} runs "
                    f"({test.flip_rate:.0%}). Suggestive, but a single transition is "
                    "more often a regression than a flake."
                ),
            }
        )
    if not test.has_divergence_data:
        inferred.append(
            {
                "label": "No commit data",
                "detail": (
                    "No run carried a commit SHA, so same-commit divergence could not "
                    "be measured and this verdict rests on the weaker signal."
                ),
            }
        )

    return {
        "proven": proven,
        "inferred": inferred,
        "score_breakdown": {
            "divergence_rate": test.divergence_rate,
            "flip_rate": test.flip_rate,
            "confidence": test.confidence,
            "score": test.score,
        },
    }


def _diagnosis(test: TestAnalysis) -> dict[str, Any] | None:
    if test.cause is None:
        return None
    return {
        "cause": str(test.cause.cause),
        "confidence": test.cause.confidence,
        "matched": list(test.cause.matched),
        "remediation": test.cause.remediation,
        "is_heuristic": test.order is None,
        "order": (
            {
                "separation": test.order.separation,
                "mean_position_on_fail": test.order.mean_position_on_fail,
                "mean_position_on_pass": test.order.mean_position_on_pass,
                "likely_polluter": test.order.likely_polluter,
                "polluter_failure_share": test.order.polluter_failure_share,
            }
            if test.order
            else None
        ),
    }


def _signatures(outcomes: list[TestOutcome]) -> list[dict[str, Any]]:
    """Distinct failure signatures for one test, most frequent first.

    More than one usually means more than one bug, which is worth seeing rather than
    averaging into a single "cause".
    """
    counts: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not (outcome.status.is_failure or outcome.retried) or not outcome.signature:
            continue
        entry = counts.setdefault(
            outcome.signature,
            {"signature": outcome.signature, "count": 0, "example": outcome.message},
        )
        entry["count"] += 1

    return sorted(counts.values(), key=lambda e: -int(e["count"]))


def _neighbours(
    test_id: str, outcomes: list[TestOutcome], predecessors: dict[tuple[str, str], str]
) -> list[dict[str, Any]]:
    """Which tests ran immediately before this one, split by outcome.

    Shown so the polluter verdict can be checked rather than believed. If a
    predecessor precedes failures and passes about equally, the reader can see that
    the correlation is not there.
    """
    tally: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        previous = predecessors.get((outcome.run_uid or "", test_id))
        if not previous:
            continue
        entry = tally.setdefault(previous, {"before_failure": 0, "before_pass": 0})
        if outcome.status.is_failure or outcome.retried:
            entry["before_failure"] += 1
        elif outcome.status.is_pass:
            entry["before_pass"] += 1

    rows: list[tuple[float, int, dict[str, Any]]] = []
    for name, counts in tally.items():
        failures = counts["before_failure"]
        passes = counts["before_pass"]
        share = round(failures / max(1, failures + passes), 4)
        rows.append(
            (
                share,
                failures,
                {
                    "test_id": name,
                    "before_failure": failures,
                    "before_pass": passes,
                    "share": share,
                },
            )
        )

    rows.sort(key=lambda row: (-row[0], -row[1]))
    return [row[2] for row in rows[:8]]


def _actions(test: TestAnalysis) -> list[dict[str, str]]:
    """Copy-pasteable next steps for this specific test.

    Real commands rather than buttons that pretend to do something. The dashboard is
    read-only by design; anything that changes state is a command the user runs and
    can review.
    """
    actions = [
        {
            "label": "Show full history",
            "command": f'flaky history "{test.test_id}"',
            "kind": "inspect",
        },
        {
            "label": "Find the introducing commit",
            "command": f'flaky blame "{test.test_id}"',
            "kind": "inspect",
        },
    ]

    if test.verdict is Verdict.FLAKY:
        actions.append(
            {
                "label": "Quarantine with an expiry",
                "command": f'flaky quarantine add "{test.test_id}" --days 14',
                "kind": "mutate",
            }
        )
        actions.append(
            {
                "label": "Copy an issue body",
                "command": f'flaky issue "{test.test_id}"',
                "kind": "export",
            }
        )
    if test.verdict in (Verdict.REGRESSION, Verdict.BROKEN):
        actions.append(
            {
                "label": "This needs a human, not a re-run",
                "command": f'flaky history "{test.test_id}"',
                "kind": "warn",
            }
        )

    return actions


def triage_payload(
    store: Storage, config: Config, run_outcomes: list[TestOutcome], *, source: str | None = None
) -> dict[str, Any]:
    """Known flakes versus genuine breakage for one run.

    History excludes the run being triaged, so a first-time failure cannot use the
    evidence of itself.
    """
    run_uids = {o.run_uid for o in run_outcomes if o.run_uid}
    history = [o for o in store.outcomes() if o.run_uid not in run_uids]
    baseline = analyze(history, config)
    result = triage(run_outcomes, baseline, source=source)

    def entry(failure: Any) -> dict[str, Any]:
        return {
            "test_id": failure.test_id,
            "name": failure.name,
            "status": str(failure.status),
            "message": failure.message,
            "score": failure.score,
            "verdict": str(failure.history.verdict) if failure.history else None,
            "failures": failure.history.failures if failure.history else None,
            "runs": failure.history.runs if failure.history else None,
        }

    return {
        "api_version": API_VERSION,
        "summary": {
            "source": result.source,
            "commit_sha": result.commit_sha,
            "total_tests": result.total_tests,
            "total_failures": result.total_failures,
            "known_flakes": len(result.known_flakes),
            "new_failures": len(result.new_failures),
            "regressions": len(result.regressions),
            "actionable": len(result.actionable),
            "all_known_flaky": result.all_known_flaky,
        },
        "known_flakes": [entry(f) for f in result.known_flakes],
        "new_failures": [entry(f) for f in result.new_failures],
        "regressions": [entry(f) for f in result.regressions],
    }


def _quarantine_payload(
    quarantine: Quarantine | None, report: AnalysisReport, config: Config
) -> dict[str, Any]:
    if quarantine is None:
        return {"available": False, "active": [], "expired": [], "recommended": []}

    def entry(item: Any) -> dict[str, Any]:
        return {
            "test_id": item.test_id,
            "reason": item.reason,
            "score": item.score,
            "added_at": item.added_at,
            "expires_at": item.expires_at,
            "days_remaining": item.days_remaining(),
            "expired": item.is_expired(),
        }

    return {
        "available": True,
        "path": str(quarantine.path),
        "active": [entry(e) for e in quarantine.active()],
        "expired": [entry(e) for e in quarantine.expired()],
        "recommended": [
            {
                "test_id": test.test_id,
                "score": test.score,
                "cause": str(test.cause.cause) if test.cause else None,
                "failures": test.failures,
                "runs": test.runs,
            }
            for test in recommend(report, config)
        ],
    }


def _quarantine_entry(quarantine: Quarantine | None, test_id: str) -> dict[str, Any] | None:
    if quarantine is None:
        return None
    entry = quarantine.get(test_id)
    if entry is None:
        return None
    return {
        "reason": entry.reason,
        "expires_at": entry.expires_at,
        "days_remaining": entry.days_remaining(),
        "expired": entry.is_expired(),
    }


def _quarantine_days_outstanding(quarantine: Quarantine | None) -> int:
    """Total days elapsed past expiry across every quarantine entry."""
    if quarantine is None:
        return 0

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    total = 0
    for entry in quarantine.expired(now):
        try:
            deadline = datetime.fromisoformat(entry.expires_at)
        except ValueError:
            continue
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        total += max(0, (now - deadline).days)
    return total


def _caveats(report: AnalysisReport, score: TrustScore) -> list[dict[str, str]]:
    """Things the dashboard must say out loud rather than let a reader assume.

    The same caveats the CLI prints. A prettier interface is exactly where honesty
    about weak evidence is most likely to get quietly dropped.
    """
    caveats: list[dict[str, str]] = []

    if not report.has_commit_data:
        caveats.append(
            {
                "severity": "warning",
                "title": "No commit data",
                "detail": (
                    "No run carried a commit SHA, so same-commit divergence could not be "
                    "measured. Benchmarked, the false alarm rate rises from 0% to 25% "
                    "without it. Run inside a git repository, or pass --commit."
                ),
            }
        )

    thin = [t for t in report.flaky if t.confidence < 1.0]
    if thin:
        caveats.append(
            {
                "severity": "info",
                "title": "Thin evidence",
                "detail": (
                    f"{len(thin)} of {len(report.flaky)} flaky verdicts rest on fewer runs "
                    "than the confidence threshold. Their scores are damped and will move "
                    "as more runs accumulate."
                ),
            }
        )

    if report.total_runs < 10:
        caveats.append(
            {
                "severity": "warning",
                "title": "Short history",
                "detail": (
                    f"Only {report.total_runs} runs recorded. Measured accuracy drops "
                    "sharply below 10 runs: the false alarm rate at 5 runs is 12.5%. "
                    "Try `flaky hunt -n 20`."
                ),
            }
        )

    if score.wasted_ci_seconds and not score.median_run_seconds:
        caveats.append(
            {
                "severity": "info",
                "title": "Wasted CI time unavailable",
                "detail": "No run durations recorded, so the estimate cannot be computed.",
            }
        )

    return caveats
