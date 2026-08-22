"""Render accuracy measurements.

The rule here is the same one the rest of the tool follows: report the weak numbers
as prominently as the strong ones. A benchmark that only surfaces favourable results
is marketing, and this project's whole argument is that it can be trusted.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..benchmark.score import BenchmarkResult


def render_console(
    result: BenchmarkResult, console: Console | None = None, *, show_confusion: bool = True
) -> None:
    """Print the measurement to a terminal."""
    out = console or Console()

    out.print(
        f"Measured against {result.total} tests with known labels, "
        f"{result.runs} runs each, {result.commit_coverage:.0%} commit coverage "
        f"(seed {result.seed}).",
        style="dim",
    )
    if result.undetectable:
        out.print(
            f"{result.undetectable} generated flakes never actually failed in the "
            "window, so no detector could find them. Excluded rather than counted "
            "as misses.",
            style="dim",
        )
    out.print()

    out.print(_headline(result))
    out.print()
    out.print(_label_table(result))

    if result.order_dependent_total:
        out.print()
        out.print(
            f"Order dependence: {result.order_dependent_diagnosed} of "
            f"{result.order_dependent_total} diagnosed "
            f"(recall {result.polluter_recall:.0%}), and "
            f"{result.order_dependent_polluter_correct} named the correct polluter "
            f"(precision {result.polluter_precision:.0%})."
        )

    if show_confusion and result.confusion:
        out.print()
        out.print(_confusion_table(result))


def _headline(result: BenchmarkResult) -> Table:
    """The two numbers that matter more than accuracy."""
    table = Table(
        box=None,
        pad_edge=False,
        show_header=False,
        title="Headline",
        title_style="bold",
        title_justify="left",
    )
    table.add_column("metric", width=44, no_wrap=True)
    table.add_column("value", width=20, no_wrap=True)

    false_alarm = result.false_alarm_rate
    table.add_row(
        "false alarm (real break called flaky)",
        Text(
            f"{false_alarm:.1%}  ({result.false_alarms}/{result.breaks_total})",
            style="green" if false_alarm == 0 else "red",
        ),
    )
    table.add_row(
        "missed break (flake called a break)",
        Text(
            f"{result.missed_break_rate:.1%}  ({result.missed_breaks}/{result.flaky_total})",
            style="green" if result.missed_break_rate < 0.1 else "yellow",
        ),
    )
    table.add_row("overall accuracy", f"{result.accuracy:.1%}")
    return table


def _label_table(result: BenchmarkResult) -> Table:
    table = Table(box=None, pad_edge=False, header_style="bold dim")
    table.add_column("label", width=12)
    table.add_column("support", justify="right", width=7)
    table.add_column("predicted", justify="right", width=9)
    table.add_column("precision", justify="right", width=9)
    table.add_column("recall", justify="right", width=7)
    table.add_column("f1", justify="right", width=6)

    for score in result.labels:
        table.add_row(
            score.label,
            str(score.support),
            str(score.predicted),
            f"{score.precision:.3f}",
            f"{score.recall:.3f}",
            f"{score.f1:.3f}",
        )
    return table


def _confusion_table(result: BenchmarkResult) -> Table:
    labels = sorted({*result.confusion, *(k for row in result.confusion.values() for k in row)})

    table = Table(
        box=None,
        pad_edge=False,
        header_style="bold dim",
        title="Confusion (rows are truth, columns are what the tool said)",
        title_style="bold dim",
        title_justify="left",
    )
    table.add_column("truth", width=12)
    for label in labels:
        table.add_column(label[:10], justify="right", width=10)

    for truth in labels:
        row = result.confusion.get(truth, {})
        if not row:
            continue
        cells = []
        for predicted in labels:
            count = row.get(predicted, 0)
            if count == 0:
                cells.append(Text("-", style="dim"))
            elif predicted == truth:
                cells.append(Text(str(count), style="green"))
            else:
                cells.append(Text(str(count), style="yellow"))
        table.add_row(truth, *cells)
    return table


def to_dict(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "setup": {
            "tests": result.total,
            "runs": result.runs,
            "commit_coverage": result.commit_coverage,
            "seed": result.seed,
            "undetectable_excluded": result.undetectable,
        },
        "headline": {
            "false_alarm_rate": round(result.false_alarm_rate, 4),
            "false_alarms": result.false_alarms,
            "breaks_total": result.breaks_total,
            "missed_break_rate": round(result.missed_break_rate, 4),
            "missed_breaks": result.missed_breaks,
            "flaky_total": result.flaky_total,
            "accuracy": round(result.accuracy, 4),
        },
        "labels": [
            {
                "label": score.label,
                "support": score.support,
                "predicted": score.predicted,
                "precision": round(score.precision, 4),
                "recall": round(score.recall, 4),
                "f1": round(score.f1, 4),
            }
            for score in result.labels
        ],
        "order_dependence": {
            "total": result.order_dependent_total,
            "diagnosed": result.order_dependent_diagnosed,
            "polluter_correct": result.order_dependent_polluter_correct,
            "precision": round(result.polluter_precision, 4),
            "recall": round(result.polluter_recall, 4),
        },
        "confusion": result.confusion,
    }


def render_json(result: BenchmarkResult) -> str:
    return json.dumps(to_dict(result), indent=2) + "\n"


def render_markdown(result: BenchmarkResult) -> str:
    """Markdown suitable for pasting into documentation."""
    lines = [
        "### Measured accuracy",
        "",
        f"{result.total} tests with known labels, {result.runs} runs each, "
        f"{result.commit_coverage:.0%} commit coverage, seed {result.seed}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| False alarm rate (real break called flaky) | **{result.false_alarm_rate:.1%}** "
        f"({result.false_alarms}/{result.breaks_total}) |",
        f"| Missed break rate (flake called a break) | {result.missed_break_rate:.1%} "
        f"({result.missed_breaks}/{result.flaky_total}) |",
        f"| Overall accuracy | {result.accuracy:.1%} |",
        "",
        "| Label | Support | Precision | Recall | F1 |",
        "|---|------:|----------:|-------:|---:|",
    ]
    for score in result.labels:
        lines.append(
            f"| `{score.label}` | {score.support} | {score.precision:.3f} | "
            f"{score.recall:.3f} | {score.f1:.3f} |"
        )

    if result.order_dependent_total:
        lines.extend(
            [
                "",
                f"Order dependence: {result.order_dependent_diagnosed} of "
                f"{result.order_dependent_total} diagnosed, "
                f"{result.order_dependent_polluter_correct} naming the correct "
                f"polluter (precision {result.polluter_precision:.0%}, "
                f"recall {result.polluter_recall:.0%}).",
            ]
        )

    if result.undetectable:
        lines.extend(
            [
                "",
                f"> {result.undetectable} generated flakes never failed within the "
                "window, so no detector could have found them. Excluded rather than "
                "counted as misses.",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_sweep_markdown(results: list[BenchmarkResult], axis: str) -> str:
    """A table showing how accuracy responds to the evidence available."""
    if axis == "window":
        # The window sweep is about attribution, not classification, so it gets its own
        # columns. Showing flaky recall against window would report a number the axis does
        # not move and hide the two it does.
        lines = [
            "| Search window | Polluter named | Polluter precision | False alarm rate | Accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
        for result in results:
            named = f"{result.order_dependent_polluter_named}/{result.order_dependent_total}"
            lines.append(
                f"| {result.order_window} | {named} | {result.polluter_precision:.3f} | "
                f"{result.false_alarm_rate:.1%} | {result.accuracy:.1%} |"
            )
        return "\n".join(lines) + "\n"

    heading = "Runs recorded" if axis == "runs" else "Commit coverage"
    lines = [
        f"| {heading} | Flaky recall | Flaky precision | False alarm rate | Accuracy |",
        "|---|---:|---:|---:|---:|",
    ]
    for result in results:
        flaky = result.label("flaky")
        axis_value = result.runs if axis == "runs" else f"{result.commit_coverage:.0%}"
        lines.append(
            f"| {axis_value} | {flaky.recall:.3f} | {flaky.precision:.3f} | "
            f"{result.false_alarm_rate:.1%} | {result.accuracy:.1%} |"
            if flaky
            else f"| {axis_value} | - | - | {result.false_alarm_rate:.1%} | {result.accuracy:.1%} |"
        )
    return "\n".join(lines) + "\n"
