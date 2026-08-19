"""Suite-level health: one number, fully explainable.

A dashboard needs a headline figure. The temptation is to invent a weighted index
nobody can interrogate, which for this project would be self-defeating -- the whole
argument of the tool is that a conclusion you cannot check is a conclusion you should
not trust.

So the trust score is built only from figures already collected, and every point it
deducts is attributed to a named component with a sentence explaining it. The component
penalties sum exactly to `TrustScore.deducted`, and the headline score is
`100 - deducted` rounded to a whole number. There is no residual and no fudge factor:
the only thing between the components and the score is that rounding.

Pure functions over analyses, like the rest of `analysis/`.
"""

from __future__ import annotations

import statistics

from ..models import (
    AnalysisReport,
    HealthComponent,
    TestAnalysis,
    TrustScore,
    Verdict,
)

MAX_FLAKE_PENALTY = 30.0
"""Flakiness is the main thing being measured, so it carries the largest weight."""

MAX_BREAK_PENALTY = 35.0
"""A single unresolved break outranks any amount of flakiness.

Deliberately the heaviest component: a suite with one real regression is less
trustworthy than a suite with five known flakes, because the flakes are known.
"""

MAX_COVERAGE_PENALTY = 20.0
"""Without commit SHAs the tool's own verdicts are measurably weaker.

Benchmarked: the false alarm rate goes from 0% to 25% without commit data. A suite
that cannot supply it should not be reported as fully trustworthy, and the score is
the honest place to say so.
"""

MAX_QUARANTINE_PENALTY = 15.0
"""Quarantine is a tourniquet. Leaving it on is a cost, not a fix."""

FLAKE_SATURATION = 10
"""Number of active flakes at which the flake penalty reaches its ceiling."""

QUARANTINE_DAY_SATURATION = 60
"""Outstanding quarantine-days at which that penalty reaches its ceiling."""


def trust_score(
    report: AnalysisReport,
    *,
    quarantine_days_outstanding: int = 0,
    median_run_seconds: float | None = None,
) -> TrustScore:
    """Score how much this suite can be believed, out of 100.

    `quarantine_days_outstanding` is the total days elapsed past expiry across all
    quarantine entries. Passed in rather than read here, because `analysis/` does not
    touch the filesystem.
    """
    considered = [t for t in report.tests if t.runs > 0]
    total = len(considered)

    stable = sum(1 for t in considered if t.verdict in (Verdict.STABLE, Verdict.FIXED))
    flakes = [t for t in considered if t.verdict is Verdict.FLAKY]
    breaks = [t for t in considered if t.verdict in (Verdict.REGRESSION, Verdict.BROKEN)]

    components = (
        _flake_component(flakes),
        _break_component(breaks),
        _coverage_component(report),
        _quarantine_component(quarantine_days_outstanding),
    )

    # Whole numbers only. A trust score of 57.6 would imply a precision the inputs do
    # not have; `TrustScore.deducted` keeps the exact figure for anyone checking.
    deducted = sum(component.penalty for component in components)
    score = max(0, min(100, round(100 - deducted)))

    duration = median_run_seconds if median_run_seconds is not None else 0.0
    flaky_failures = sum(t.failures for t in flakes)

    return TrustScore(
        score=score,
        components=components,
        total_tests=total,
        stable_tests=stable,
        active_flakes=len(flakes),
        unresolved_breaks=len(breaks),
        commit_coverage=report.commit_coverage,
        quarantine_days_outstanding=quarantine_days_outstanding,
        wasted_ci_seconds=flaky_failures * duration,
        median_run_seconds=duration,
        flaky_failures=flaky_failures,
    )


def _flake_component(flakes: list[TestAnalysis]) -> HealthComponent:
    """Penalty for active flakes, weighted by how bad each one is.

    Counting flakes alone would treat a test failing 1 run in 20 the same as one
    failing 10 in 20. Summing their scores instead means a nearly-stable flake costs
    almost nothing, which is the right incentive.
    """
    if not flakes:
        return HealthComponent(
            name="Flaky tests",
            detail="No active flakes.",
            penalty=0.0,
            weight=MAX_FLAKE_PENALTY,
        )

    severity = sum(t.score for t in flakes)
    penalty = min(MAX_FLAKE_PENALTY, MAX_FLAKE_PENALTY * severity / FLAKE_SATURATION)

    worst = max(flakes, key=lambda t: t.score)
    return HealthComponent(
        name="Flaky tests",
        detail=(
            f"{len(flakes)} active "
            f"{'flake' if len(flakes) == 1 else 'flakes'}, worst scoring "
            f"{worst.score:.2f} ({worst.test_id.rsplit('::', 1)[-1]})."
        ),
        penalty=round(penalty, 1),
        weight=MAX_FLAKE_PENALTY,
    )


def _break_component(breaks: list[TestAnalysis]) -> HealthComponent:
    """Penalty for regressions and broken tests.

    Steeper per test than flakiness and saturating faster: two unresolved breaks
    already means the suite is not telling you the truth about the code.
    """
    if not breaks:
        return HealthComponent(
            name="Unresolved breaks",
            detail="No regressions or broken tests.",
            penalty=0.0,
            weight=MAX_BREAK_PENALTY,
        )

    penalty = min(MAX_BREAK_PENALTY, MAX_BREAK_PENALTY * len(breaks) / 2)
    regressions = sum(1 for t in breaks if t.verdict is Verdict.REGRESSION)
    broken = len(breaks) - regressions

    parts = []
    if regressions:
        parts.append(f"{regressions} regression{'s' if regressions != 1 else ''}")
    if broken:
        parts.append(f"{broken} never passing")

    return HealthComponent(
        name="Unresolved breaks",
        detail=" and ".join(parts) + ". These need a human, not a re-run.",
        penalty=round(penalty, 1),
        weight=MAX_BREAK_PENALTY,
    )


def _coverage_component(report: AnalysisReport) -> HealthComponent:
    """Penalty for missing commit SHAs.

    Not a style complaint. Without commit data the tool's measured false alarm rate
    goes from 0% to 25%, so the verdicts above are genuinely less reliable and the
    score should reflect that rather than quietly presenting them as equivalent.
    """
    coverage = report.commit_coverage
    if coverage >= 0.999:
        return HealthComponent(
            name="Commit evidence",
            detail="Every run carries a commit SHA, so same-commit divergence is available.",
            penalty=0.0,
            weight=MAX_COVERAGE_PENALTY,
        )

    penalty = MAX_COVERAGE_PENALTY * (1 - coverage)
    return HealthComponent(
        name="Commit evidence",
        detail=(
            f"Only {coverage:.0%} of runs carry a commit SHA. Same-commit divergence "
            "is the strongest signal available, and verdicts are weaker without it."
        ),
        penalty=round(penalty, 1),
        weight=MAX_COVERAGE_PENALTY,
    )


def _quarantine_component(days_outstanding: int) -> HealthComponent:
    """Penalty for quarantine entries left past their expiry.

    Quarantine buys time; it does not spend it. An entry that expired a month ago is
    a test nobody is watching and nobody is fixing.
    """
    if days_outstanding <= 0:
        return HealthComponent(
            name="Quarantine debt",
            detail="Nothing quarantined past its expiry.",
            penalty=0.0,
            weight=MAX_QUARANTINE_PENALTY,
        )

    penalty = min(
        MAX_QUARANTINE_PENALTY,
        MAX_QUARANTINE_PENALTY * days_outstanding / QUARANTINE_DAY_SATURATION,
    )
    return HealthComponent(
        name="Quarantine debt",
        detail=(
            f"{days_outstanding} quarantined-test "
            f"{'day' if days_outstanding == 1 else 'days'} outstanding past expiry. "
            "Run `flaky quarantine verify`."
        ),
        penalty=round(penalty, 1),
        weight=MAX_QUARANTINE_PENALTY,
    )


def median_run_duration(durations: list[float]) -> float:
    """Median suite duration, used to estimate wasted CI time."""
    usable = [d for d in durations if d and d > 0]
    return statistics.median(usable) if usable else 0.0


WASTED_TIME_ASSUMPTION = (
    "Estimated as (flaky failures) x (median suite duration), on the assumption that "
    "each flaky failure costs one re-run of the suite. It is a model, not a "
    "measurement: the tool cannot see re-runs that happened outside its own view."
)
"""Stated wherever the figure is displayed.

An unlabelled cost estimate would be the most quotable number in the product and the
least defensible, which is exactly the combination to avoid.
"""
