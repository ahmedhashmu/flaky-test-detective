"""Rendering for reproduction: the command, and what it cost to find.

Formatting only.

The layout is built around one line. Everything else on the screen -- the candidate count,
the trial counts, the control rate -- exists to make that one line believable, so the
command gets a panel and the evidence gets dim text underneath it. A reader who takes
nothing else away should still leave with something they can paste.

The non-answers are laid out the same way on purpose. "It fails on its own" and "nothing
tried made it fail" are useful results, and giving them a smaller, apologetic treatment
would teach people to read them as failures of the tool rather than as findings.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..models import ReproduceOutcome, Reproduction

OUTCOME_STYLE = {
    ReproduceOutcome.REPRODUCED: "green",
    ReproduceOutcome.FAILS_ALONE: "yellow",
    ReproduceOutcome.NOT_REPRODUCED: "yellow",
    ReproduceOutcome.BUDGET_EXHAUSTED: "yellow",
    ReproduceOutcome.UNSUPPORTED: "red",
}

OUTCOME_HEADLINE = {
    ReproduceOutcome.REPRODUCED: "Reproduced on demand",
    ReproduceOutcome.FAILS_ALONE: "Fails on its own",
    ReproduceOutcome.NOT_REPRODUCED: "Not reproduced",
    ReproduceOutcome.BUDGET_EXHAUSTED: "Reproduced, not minimized",
    ReproduceOutcome.UNSUPPORTED: "Runner not supported",
}


def render_console(result: Reproduction, console: Console | None = None) -> None:
    out = console or Console()
    style = OUTCOME_STYLE[result.outcome]

    out.print(result.test_id, style="bold")
    out.print()
    out.print(
        Panel(
            Text(OUTCOME_HEADLINE[result.outcome], style=f"bold {style}"),
            border_style=style,
            padding=(0, 1),
        )
    )

    if result.command:
        out.print()
        out.print("Run this", style="bold dim")
        out.print(
            Panel(
                Text(result.command, style="bold"),
                border_style=style,
                padding=(0, 1),
            )
        )

    if result.sequence:
        out.print()
        out.print("Minimal failing sequence", style="bold dim")
        for index, test_id in enumerate(result.sequence, start=1):
            out.print(f"  {index}. {test_id}", style="dim")
        out.print(f"  {len(result.sequence) + 1}. {result.test_id}", style="bold")

    out.print()
    out.print(result.explanation, style="dim")

    _evidence(result, out)


def _evidence(result: Reproduction, out: Console) -> None:
    rows: list[tuple[str, str]] = [
        ("in this order", _rate(result.failures, result.trials)),
        ("alone (control)", _rate(result.control_failures, result.control_trials)),
    ]

    if result.reduction:
        rows.append(("search", result.reduction))
    if result.oracle_calls:
        rows.append(("experiments", str(result.oracle_calls)))
    if result.suite_runs:
        rows.append(("suite executions", str(result.suite_runs)))

    out.print()
    out.print("Evidence", style="bold dim")
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        out.print(f"  {label:<{width}}  {value}", style="dim")

    _next_step(result, out)


def _next_step(result: Reproduction, out: Console) -> None:
    if result.outcome is ReproduceOutcome.REPRODUCED and result.sequence:
        out.print()
        out.print(
            "Next: fix the shared state, then prove it with "
            f'`flaky verify "{result.test_id}" -- <your test command>`.',
            style="dim",
        )
        return

    if result.outcome is ReproduceOutcome.FAILS_ALONE:
        out.print()
        out.print(
            "Next: it needs no ordering, so repeat it directly. The cause is inside the "
            "test or its fixtures, not its neighbours.",
            style="dim",
        )
        return

    if result.outcome is ReproduceOutcome.NOT_REPRODUCED:
        out.print()
        out.print(
            "Next: the cause is likely outside test ordering. Check "
            f'`flaky investigate "{result.test_id}"` for an environment association, and '
            "consider that timing or external services may be involved.",
            style="dim",
        )


def _rate(failures: int, trials: int) -> str:
    if trials <= 0:
        return "not measured"
    return f"{failures}/{trials} failed ({failures / trials:.0%})"


def render_markdown(result: Reproduction) -> str:
    lines = [
        f"## Reproduction: {OUTCOME_HEADLINE[result.outcome]}",
        "",
        f"`{result.test_id}`",
        "",
    ]

    if result.command:
        lines += ["```sh", result.command, "```", ""]

    if result.sequence:
        lines.append("Minimal failing sequence:")
        lines.append("")
        for index, test_id in enumerate(result.sequence, start=1):
            lines.append(f"{index}. `{test_id}`")
        lines.append(f"{len(result.sequence) + 1}. `{result.test_id}`")
        lines.append("")

    lines += [
        result.explanation,
        "",
        "| Evidence | |",
        "|---|---|",
        f"| In this order | {_rate(result.failures, result.trials)} |",
        f"| Alone (control) | {_rate(result.control_failures, result.control_trials)} |",
    ]

    if result.reduction:
        lines.append(f"| Search | {result.reduction} |")
    if result.oracle_calls:
        lines.append(f"| Experiments | {result.oracle_calls} |")
    if result.suite_runs:
        lines.append(f"| Suite executions | {result.suite_runs} |")

    return "\n".join(lines).rstrip() + "\n"


def reproduction_to_dict(result: Reproduction) -> dict[str, Any]:
    return {
        "test_id": result.test_id,
        "outcome": str(result.outcome),
        "reproduced": result.reproduced,
        "command": result.command,
        "sequence": list(result.sequence),
        "failures": result.failures,
        "trials": result.trials,
        "failure_rate": round(result.failure_rate, 4),
        "control_failures": result.control_failures,
        "control_trials": result.control_trials,
        "control_rate": round(result.control_rate, 4),
        "candidates_started": result.candidates_started,
        "oracle_calls": result.oracle_calls,
        "suite_runs": result.suite_runs,
        "explanation": result.explanation,
    }


def render_json(result: Reproduction, *, indent: int = 2) -> str:
    return json.dumps(reproduction_to_dict(result), indent=indent) + "\n"


FORMATS = ("console", "md", "markdown", "json")


def render(result: Reproduction, fmt: str) -> str:
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
    "reproduction_to_dict",
]
