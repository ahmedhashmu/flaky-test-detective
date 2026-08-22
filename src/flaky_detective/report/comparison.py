"""Rendering for the branch comparison: what did this change introduce?

Formatting only. Every number is read off the `ComparisonReport`.

The layout has one job: make the merge decision readable in about three seconds, and
make the reason for it readable in about thirty. So the headline is the decision, the
blocking entries come first with their evidence, and the pre-existing flakes go last
where they cannot be mistaken for something the change caused.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..models import Change, ComparisonReport, TestComparison

CHANGE_STYLE = {
    Change.NEW_BREAK: "red",
    Change.NEW_FLAKE: "red",
    Change.WORSE: "yellow",
    Change.KNOWN_FLAKE: "dim yellow",
    Change.IMPROVED: "green",
    Change.UNPROVEN: "dim",
    Change.UNCHANGED: "dim",
}

CHANGE_LABEL = {
    Change.NEW_BREAK: "new break",
    Change.NEW_FLAKE: "new flake",
    Change.WORSE: "worse",
    Change.KNOWN_FLAKE: "known flake",
    Change.IMPROVED: "improved",
    Change.UNPROVEN: "unproven",
    Change.UNCHANGED: "unchanged",
}


def render_console(
    result: ComparisonReport, console: Console | None = None, *, limit: int = 20
) -> None:
    """Print the merge decision and the evidence under it."""
    out = console or Console()

    _headline(result, out)
    _window(result, out)

    if not result.enough_baseline:
        out.print()
        out.print(
            f"Warning: the baseline has only {result.baseline_runs} "
            f"{'run' if result.baseline_runs == 1 else 'runs'}. Anything reported below as "
            "introduced is a weak claim, because there is not enough history to establish "
            "what was stable before.",
            style="yellow",
        )

    _section(out, result.new_breaks, "Breakage introduced here", "bold red", limit)
    _section(out, result.new_flakes, "Flakiness introduced here", "bold red", limit)
    _section(out, result.worse, "Already flaky, worse here", "bold yellow", limit)
    _section(out, result.improved, "Improved", "bold green", limit)

    if result.known_flakes:
        out.print()
        out.print(f"Pre-existing flakes, tolerated ({len(result.known_flakes)})", style="bold dim")
        for entry in result.known_flakes[:limit]:
            out.print(
                f"  {_short(entry.test_id)}  {entry.baseline_summary} -> {entry.head_summary}",
                style="dim",
            )

    if result.unproven:
        out.print()
        out.print(f"Not enough evidence to attribute ({len(result.unproven)})", style="bold dim")
        for entry in result.unproven[:limit]:
            out.print(f"  {_short(entry.test_id)}", style="dim")
            out.print(f"      {entry.explanation}", style="dim")


def _headline(result: ComparisonReport, out: Console) -> None:
    if result.clean:
        message = Text("No flakiness or breakage introduced.", style="bold green")
        if result.known_flakes:
            message.append(
                f"\n{len(result.known_flakes)} pre-existing "
                f"{'flake' if len(result.known_flakes) == 1 else 'flakes'} were seen and are "
                "not this change's debt.",
                style="dim",
            )
        out.print(Panel(message, border_style="green", padding=(0, 1)))
        return

    parts = []
    if result.new_breaks:
        parts.append(f"{len(result.new_breaks)} new {_plural(len(result.new_breaks), 'break')}")
    if result.new_flakes:
        parts.append(f"{len(result.new_flakes)} new {_plural(len(result.new_flakes), 'flake')}")

    text = Text(f"{' and '.join(parts)} introduced by this change.", style="bold red")
    text.append("\nRecommendation: do not merge until these are addressed.", style="red")
    if result.known_flakes:
        text.append(
            f"\n{len(result.known_flakes)} pre-existing "
            f"{'flake' if len(result.known_flakes) == 1 else 'flakes'} ignored.",
            style="dim",
        )
    out.print(Panel(text, border_style="red", padding=(0, 1)))


def _window(result: ComparisonReport, out: Console) -> None:
    baseline = result.baseline_label or "baseline"
    head = result.head_label or "this change"
    out.print(
        f"{baseline}: {result.baseline_runs} runs, {result.baseline_tests} tests   |   "
        f"{head}: {result.head_runs} runs, {result.head_tests} tests",
        style="dim",
    )


def _section(
    out: Console, entries: tuple[TestComparison, ...], title: str, style: str, limit: int
) -> None:
    if not entries:
        return

    out.print()
    out.print(title, style=style)
    for entry in entries[:limit]:
        out.print(f"  {_short(entry.test_id)}")
        out.print(
            f"      {entry.baseline_summary} -> {entry.head_summary}"
            f"   confidence {entry.confidence}",
            style="dim",
        )
        if entry.explanation:
            out.print(f"      {entry.explanation}", style="dim")

    if len(entries) > limit:
        out.print(f"  ... {len(entries) - limit} more", style="dim")


def _table(entries: tuple[TestComparison, ...]) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold dim")
    table.add_column("change", width=12, no_wrap=True)
    table.add_column("baseline", justify="right", width=10, no_wrap=True)
    table.add_column("here", justify="right", width=10, no_wrap=True)
    table.add_column("p", justify="right", width=7, no_wrap=True)
    table.add_column("test", no_wrap=True, overflow="ellipsis")

    for entry in entries:
        table.add_row(
            Text(CHANGE_LABEL[entry.change], style=CHANGE_STYLE[entry.change]),
            entry.baseline_summary,
            entry.head_summary,
            f"{entry.probability:.3f}",
            _short(entry.test_id),
        )
    return table


def render_markdown(result: ComparisonReport) -> str:
    """For a pull-request comment."""
    baseline = result.baseline_label or "baseline"
    head = result.head_label or "this change"

    lines: list[str] = ["## Flaky Test Detective", ""]

    if result.clean:
        lines.append("**No flakiness or breakage introduced.**")
        if result.known_flakes:
            lines.append("")
            lines.append(
                f"{len(result.known_flakes)} pre-existing "
                f"{'flake' if len(result.known_flakes) == 1 else 'flakes'} were seen and are not "
                "this change's debt."
            )
    else:
        headline = []
        if breaks := len(result.new_breaks):
            headline.append(f"{breaks} new {_plural(breaks, 'break')}")
        if flakes := len(result.new_flakes):
            headline.append(f"{flakes} new {_plural(flakes, 'flake')}")
        lines.append(f"**{' and '.join(headline)} introduced by this change.**")
        lines.append("")
        lines.append("Recommendation: **do not merge** until these are addressed.")

    lines += [
        "",
        f"`{baseline}`: {result.baseline_runs} runs, {result.baseline_tests} tests  ·  "
        f"`{head}`: {result.head_runs} runs, {result.head_tests} tests",
    ]

    if not result.enough_baseline:
        lines += [
            "",
            f"> The baseline has only {result.baseline_runs} runs, which is not enough to "
            "establish what was stable before. Treat anything below as a weak claim.",
        ]

    for title, entries in (
        ("Breakage introduced here", result.new_breaks),
        ("Flakiness introduced here", result.new_flakes),
        ("Already flaky, worse here", result.worse),
        ("Improved", result.improved),
    ):
        if not entries:
            continue
        lines += ["", f"### {title}", ""]
        for entry in entries:
            lines.append(f"**`{entry.test_id}`**")
            lines.append("")
            lines.append(
                f"| baseline | here | confidence | p |\n|---|---|---|---|\n"
                f"| {entry.baseline_summary} | {entry.head_summary} | {entry.confidence} | "
                f"{entry.probability:.3f} |"
            )
            lines.append("")
            lines.append(entry.explanation)
            lines.append("")
            lines.append(
                f'<sub>`flaky history "{entry.test_id}"` · '
                f'`flaky blame "{entry.test_id}"` · '
                f'`flaky issue "{entry.test_id}"`</sub>'
            )
            lines.append("")

    if result.known_flakes:
        lines += [
            "",
            f"<details><summary>{len(result.known_flakes)} pre-existing "
            f"{'flake' if len(result.known_flakes) == 1 else 'flakes'}, not blocking</summary>",
            "",
            "| Baseline | Here | Test |",
            "|---|---|---|",
        ]
        for entry in result.known_flakes:
            lines.append(f"| {entry.baseline_summary} | {entry.head_summary} | `{entry.test_id}` |")
        lines += ["", "</details>"]

    if result.unproven:
        lines += [
            "",
            f"<details><summary>{len(result.unproven)} changes the evidence cannot "
            "attribute</summary>",
            "",
        ]
        for entry in result.unproven:
            lines.append(f"- `{entry.test_id}` — {entry.explanation}")
        lines += ["", "</details>"]

    return "\n".join(lines).rstrip() + "\n"


def comparison_to_dict(result: ComparisonReport) -> dict[str, Any]:
    return {
        "summary": {
            "clean": result.clean,
            "baseline_label": result.baseline_label,
            "head_label": result.head_label,
            "baseline_runs": result.baseline_runs,
            "head_runs": result.head_runs,
            "baseline_tests": result.baseline_tests,
            "head_tests": result.head_tests,
            "enough_baseline": result.enough_baseline,
            "new_flakes": len(result.new_flakes),
            "new_breaks": len(result.new_breaks),
            "worse": len(result.worse),
            "known_flakes": len(result.known_flakes),
            "improved": len(result.improved),
            "unproven": len(result.unproven),
            "blocking": len(result.blocking),
        },
        "entries": [_entry_to_dict(entry) for entry in result.entries],
    }


def _entry_to_dict(entry: TestComparison) -> dict[str, Any]:
    return {
        "test_id": entry.test_id,
        "name": entry.name,
        "change": str(entry.change),
        "blocks": entry.blocks,
        "confidence": entry.confidence,
        "probability": entry.probability,
        "baseline_rate_bound": entry.baseline_rate_bound,
        "explanation": entry.explanation,
        "baseline": _side(entry.baseline),
        "head": _side(entry.head),
    }


def _side(analysis: Any) -> dict[str, Any] | None:
    if analysis is None:
        return None
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


def render_json(result: ComparisonReport, *, indent: int = 2) -> str:
    return json.dumps(comparison_to_dict(result), indent=indent) + "\n"


FORMATS = ("console", "md", "markdown", "json")


def render(result: ComparisonReport, fmt: str) -> str:
    """Non-console formats. `console` goes through `render_console`."""
    if fmt in ("md", "markdown"):
        return render_markdown(result)
    if fmt == "json":
        return render_json(result)
    raise ValueError(f"Unknown format {fmt!r}. Available: {', '.join(sorted(FORMATS))}")


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


def _short(test_id: str, width: int = 78) -> str:
    """Trim from the left: the distinguishing part of a node id is at the end."""
    return test_id if len(test_id) <= width else "..." + test_id[-(width - 3) :]


__all__ = [
    "FORMATS",
    "comparison_to_dict",
    "render",
    "render_console",
    "render_json",
    "render_markdown",
]
