"""Rendering for fix verification: before, after, and whether to believe it.

Formatting only.

The before/after bar is the one place in this tool that shows a number getting better,
which makes it the one place most at risk of overselling. So the layout puts the outcome
word and the reason side by side, and an inconclusive result gets the same visual weight
as a confirmed one -- a smaller, quieter "not yet" is how people learn to read it as a
yes.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..models import FixOutcome, FixVerification

OUTCOME_STYLE = {
    FixOutcome.FIXED: "green",
    FixOutcome.NOT_FIXED: "red",
    FixOutcome.INCONCLUSIVE: "yellow",
}

OUTCOME_HEADLINE = {
    FixOutcome.FIXED: "Fixed",
    FixOutcome.NOT_FIXED: "Not fixed",
    FixOutcome.INCONCLUSIVE: "Cannot say yet",
}

BAR_WIDTH = 24


def render_console(result: FixVerification, console: Console | None = None) -> None:
    out = console or Console()
    style = OUTCOME_STYLE[result.outcome]

    out.print(f"{result.test_id}", style="bold")
    out.print()
    out.print(
        Panel(
            Text(OUTCOME_HEADLINE[result.outcome], style=f"bold {style}"),
            border_style=style,
            padding=(0, 1),
        )
    )

    out.print()
    _bars(result, out)

    out.print()
    out.print(result.explanation, style="dim")

    _evidence(result, out)


def _bars(result: FixVerification, out: Console) -> None:
    before, after = result.before, result.after

    out.print("Before", style="bold dim")
    out.print(
        f"  {_bar(before.failure_rate)}  {before.failure_rate:>4.0%} failure rate   "
        f"{before.failures}/{before.runs} runs   score {before.score:.2f}"
    )
    out.print()
    out.print("After", style="bold dim")
    out.print(
        f"  {_bar(after.failure_rate)}  {after.failure_rate:>4.0%} failure rate   "
        f"{after.failures}/{after.runs} runs"
    )

    if result.rate_reduction > 0:
        out.print()
        out.print(
            f"  Failure rate down {result.rate_reduction:.0%}. "
            f"About {result.failures_avoided} "
            f"{'failure' if result.failures_avoided == 1 else 'failures'} did not happen "
            f"across those {after.runs} runs that the old rate would have produced "
            f"(estimate).",
            style="dim",
        )


def _as_percent(probability: float) -> str:
    """Shared with analysis/verification.py: never round real evidence down to 0.0%."""
    if probability < 0.0001:
        return "under 0.01%"
    if probability < 0.001:
        return "under 0.1%"
    return f"{probability:.1%}"


def _bar(rate: float) -> str:
    """A fixed-width bar. Plain block characters so it survives a log file."""
    filled = min(BAR_WIDTH, round(rate * BAR_WIDTH))
    return "\u2588" * filled + "\u2591" * (BAR_WIDTH - filled)


def _evidence(result: FixVerification, out: Console) -> None:
    rows: list[tuple[str, str]] = [
        (
            "clean runs needed",
            f"{result.runs_needed} at the old rate of {result.old_rate_bound:.0%}",
        ),
        ("clean runs recorded", str(result.clean_runs)),
        (
            "chance of this streak",
            f"{_as_percent(result.probability)} if nothing had changed",
        ),
    ]

    if result.polluter:
        exposures = result.polluter_exposures
        rows.append(
            (
                "failing sequence run",
                f"{exposures} times after {result.polluter}"
                + ("" if result.exposures_sufficient else f" (need {result.exposures_needed})"),
            )
        )

    rows.append(
        (
            "other tests affected",
            "none" if not result.collateral else ", ".join(result.collateral[:3]),
        )
    )

    out.print()
    out.print("Evidence", style="bold dim")
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        out.print(f"  {label:<{width}}  {value}", style="dim")

    if result.outcome is FixOutcome.INCONCLUSIVE:
        out.print()
        out.print(
            f'Next: keep hunting. `flaky verify "{result.test_id}" --runs '
            f"{max(result.runs_needed, result.after.runs * 2)} -- <your test command>`",
            style="dim",
        )


def render_markdown(result: FixVerification) -> str:
    headline = OUTCOME_HEADLINE[result.outcome]
    before, after = result.before, result.after

    lines = [
        f"## Fix verification: {headline}",
        "",
        f"`{result.test_id}`",
        "",
        "| | Failure rate | Runs | Score |",
        "|---|---:|---:|---:|",
        f"| Before | {before.failure_rate:.0%} | {before.failures}/{before.runs} | "
        f"{before.score:.2f} |",
        f"| After | {after.failure_rate:.0%} | {after.failures}/{after.runs} | {after.score:.2f} |",
        "",
        result.explanation,
        "",
        "| Evidence | |",
        "|---|---|",
        f"| Clean runs needed | {result.runs_needed} at the old rate of "
        f"{result.old_rate_bound:.0%} |",
        f"| Clean runs recorded | {result.clean_runs} |",
        f"| Chance of this streak if unchanged | {_as_percent(result.probability)} |",
    ]

    if result.polluter:
        lines.append(
            f"| Failing sequence exercised | {result.polluter_exposures} times after "
            f"`{result.polluter}` |"
        )

    lines.append(
        f"| Other tests affected | "
        f"{'none' if not result.collateral else ', '.join(f'`{t}`' for t in result.collateral)} |"
    )

    if result.rate_reduction > 0:
        lines += [
            "",
            f"Failure rate down {result.rate_reduction:.0%}. Roughly "
            f"{result.failures_avoided} "
            f"{'failure' if result.failures_avoided == 1 else 'failures'} did not happen "
            f"across those {after.runs} runs that the old rate would have produced "
            f"(estimate).",
        ]

    return "\n".join(lines).rstrip() + "\n"


def verification_to_dict(result: FixVerification) -> dict[str, Any]:
    return {
        "test_id": result.test_id,
        "outcome": str(result.outcome),
        "is_fixed": result.is_fixed,
        "explanation": result.explanation,
        "before": _side(result.before),
        "after": _side(result.after),
        "old_rate_bound": result.old_rate_bound,
        "probability": result.probability,
        "runs_needed": result.runs_needed,
        "clean_runs": result.clean_runs,
        "rate_reduction": round(result.rate_reduction, 4),
        "failures_avoided": result.failures_avoided,
        "failures_avoided_is_estimate": True,
        "polluter": result.polluter,
        "polluter_exposures": result.polluter_exposures,
        "exposures_needed": result.exposures_needed,
        "exposures_sufficient": result.exposures_sufficient,
        "collateral": list(result.collateral),
    }


def _side(analysis: Any) -> dict[str, Any]:
    return {
        "verdict": str(analysis.verdict),
        "score": analysis.score,
        "runs": analysis.runs,
        "passes": analysis.passes,
        "failures": analysis.failures,
        "failure_rate": round(analysis.failure_rate, 4),
        "flips": analysis.flips,
        "divergent_commits": analysis.divergent_commits,
        "observed_commits": analysis.observed_commits,
        "retries": analysis.retries,
    }


def render_json(result: FixVerification, *, indent: int = 2) -> str:
    return json.dumps(verification_to_dict(result), indent=indent) + "\n"


FORMATS = ("console", "md", "markdown", "json")


def render(result: FixVerification, fmt: str) -> str:
    if fmt in ("md", "markdown"):
        return render_markdown(result)
    if fmt == "json":
        return render_json(result)
    raise ValueError(f"Unknown format {fmt!r}. Available: {', '.join(sorted(FORMATS))}")


__all__ = [
    "FORMATS",
    "render",
    "render_console",
    "render_json",
    "render_markdown",
    "verification_to_dict",
]
