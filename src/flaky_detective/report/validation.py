"""Render the real-world validation: the detector against published labels.

Formatting only, like every other module in `report/`. The numbers come from
`benchmark/realworld.py`.

The presentation rule here is stricter than elsewhere in the tool, because this is the
most quotable result in the project. Every headline figure is printed next to the count
it came from, and the rows that make the tool look worse -- labels it missed, flakes it
flagged without proof, projects that could not be built at all -- are printed in the same
table as the ones that make it look good, not in a footnote.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table

from ..benchmark.realworld import (
    NON_IDEMPOTENT_CATEGORIES,
    ProjectScore,
    RealWorldResult,
)

CATEGORY_MEANING = {
    "OD-Vic": "order-dependent victim: passes in order, fails after a polluter",
    "OD-Brit": "order-dependent brittle: fails alone, passes after a state-setter",
    "OD": "order-dependent, subtype not recorded",
    "NOD": "nondeterministic: timing, concurrency, randomness, network",
    "NIO": "non-idempotent outcome: fails only when re-run in the same process",
}


def render_console(result: RealWorldResult, console: Console | None = None) -> None:
    out = console or Console()

    out.print(
        f"Measured against IDoFT, {result.repositories} repositories, "
        f"{result.runs} suite runs, {result.results:,} test executions.",
        style="dim",
    )
    if result.dataset_sha:
        out.print(f"Label set: idoft@{result.dataset_sha[:10]}", style="dim")
    out.print()

    out.print(_headline(result))
    out.print()
    out.print(_projects_table(result))
    out.print()
    out.print(_category_table(result))

    if notes := _notes(result):
        out.print()
        for note in notes:
            out.print(note, style="dim")


def _headline(result: RealWorldResult) -> Table:
    table = Table(
        title="Headline", title_justify="left", show_header=False, box=None, pad_edge=False
    )
    table.add_column("metric", no_wrap=True)
    table.add_column("value", justify="right")
    table.add_column("of", style="dim")

    table.add_row(
        "recall (labelled flakes we found)",
        f"{result.recall:.1%}",
        f"({result.detected}/{result.reproduced} that reproduced here)",
    )
    table.add_row(
        "precision (flagged with proof)",
        f"{result.precision:.1%}",
        f"({result.flagged_with_divergence}/{result.flagged} showed same-commit divergence)",
    )
    table.add_row(
        "order dependence diagnosed",
        _ratio(result.order_diagnosed, result.order_detected),
        f"({result.order_diagnosed}/{result.order_detected} order-labelled flakes we found)",
    )
    table.add_row(
        "polluter named",
        _ratio(result.order_polluter_named, result.order_detected),
        f"({result.order_polluter_named}/{result.order_detected})",
    )
    table.add_row("", "", "")
    table.add_row(
        "labels that never varied here",
        str(result.not_reproducible),
        "not counted as misses; the flake did not occur",
    )
    table.add_row(
        "consistently failing, correctly not called flaky",
        str(result.correctly_withheld),
        "the false alarm that matters most",
    )
    table.add_row(
        "consistently failing, wrongly called flaky",
        str(result.wrongly_called_flaky),
        "should be zero",
    )
    table.add_row(
        "flagged but absent from the dataset",
        str(result.unlabelled_flagged),
        f"{result.unlabelled_flagged_with_divergence} of them did diverge",
    )
    return table


def _projects_table(result: RealWorldResult) -> Table:
    table = Table(title="Per repository", title_justify="left")
    table.add_column("repository", no_wrap=True)
    table.add_column("tests", justify="right")
    table.add_column("runs", justify="right")
    table.add_column("labels", justify="right")
    table.add_column("reproduced", justify="right")
    table.add_column("found", justify="right")
    table.add_column("recall", justify="right")
    table.add_column("flagged", justify="right")
    table.add_column("with proof", justify="right")

    for project in result.projects:
        table.add_row(
            project.repo,
            str(project.collected),
            str(project.runs),
            str(project.labelled),
            str(project.reproduced),
            str(project.detected),
            _ratio(project.detected, project.reproduced),
            str(project.flagged),
            str(project.flagged_with_divergence),
        )
    return table


def _category_table(result: RealWorldResult) -> Table:
    table = Table(title="By labelled cause", title_justify="left")
    table.add_column("category", no_wrap=True)
    table.add_column("reproduced", justify="right")
    table.add_column("found", justify="right")
    table.add_column("recall", justify="right")
    table.add_column("meaning", style="dim")

    for category, (reproduced, detected) in result.category_totals().items():
        table.add_row(
            category,
            str(reproduced),
            str(detected),
            _ratio(detected, reproduced),
            CATEGORY_MEANING.get(category, ""),
        )
    return table


def _notes(result: RealWorldResult) -> list[str]:
    notes: list[str] = []

    if result.skipped:
        notes.append(
            f"{len(result.skipped)} repositories in the manifest could not be evaluated and are "
            "listed in validation/results/skipped.json with the reason for each."
        )

    excluded = ", ".join(sorted(NON_IDEMPOTENT_CATEGORIES))
    notes.append(
        f"{excluded} labels are excluded from recall. Those tests fail only when re-run inside "
        "one process, and this tool reads reports from separate executions, so there is nothing "
        "for it to observe."
    )

    if result.order_detected and not result.order_diagnosed:
        notes.append(
            "Order dependence was detected as flakiness but not diagnosed as ordering on any "
            "labelled test. Worth reading as a limitation of the diagnosis, not the detection."
        )

    misses = [miss for project in result.projects for miss in project.misses]
    if misses:
        notes.append(f"Missed labels ({len(misses)}): " + "; ".join(misses[:6]))

    suspect = [name for project in result.projects for name in project.suspect]
    if suspect:
        notes.append(
            f"Flagged without observed divergence ({len(suspect)}), listed so they can be "
            "inspected: " + "; ".join(suspect[:6])
        )

    return notes


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "n/a"


def render_markdown(result: RealWorldResult) -> str:
    lines: list[str] = [
        "## Real-world validation",
        "",
        f"Measured against [IDoFT](https://github.com/TestingResearchIllinois/idoft): "
        f"**{result.repositories} repositories**, {result.runs} suite runs, "
        f"{result.results:,} test executions."
        + (f" Label set `idoft@{result.dataset_sha[:10]}`." if result.dataset_sha else ""),
        "",
        "| Metric | Value | Of |",
        "|---|---:|---|",
        f"| Recall — labelled flakes found | **{result.recall:.1%}** | "
        f"{result.detected}/{result.reproduced} that reproduced here |",
        f"| Precision — flagged with same-commit proof | **{result.precision:.1%}** | "
        f"{result.flagged_with_divergence}/{result.flagged} |",
        f"| Order dependence diagnosed | {_ratio(result.order_diagnosed, result.order_detected)} | "
        f"{result.order_diagnosed}/{result.order_detected} |",
        f"| Polluter named | {_ratio(result.order_polluter_named, result.order_detected)} | "
        f"{result.order_polluter_named}/{result.order_detected} |",
        f"| Labels that never varied here | {result.not_reproducible} | not counted as misses |",
        f"| Consistently failing, correctly withheld | {result.correctly_withheld} | "
        "the false alarm that matters most |",
        f"| Consistently failing, wrongly called flaky | {result.wrongly_called_flaky} | "
        "should be zero |",
        f"| Flagged but absent from the dataset | {result.unlabelled_flagged} | "
        f"{result.unlabelled_flagged_with_divergence} of them did diverge |",
        "",
        "| Repository | Tests | Runs | Labels | Reproduced | Found | Recall |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for project in result.projects:
        lines.append(
            f"| [{project.repo}](https://github.com/{project.repo}) | {project.collected} | "
            f"{project.runs} | {project.labelled} | {project.reproduced} | {project.detected} | "
            f"{_ratio(project.detected, project.reproduced)} |"
        )

    lines += [
        "",
        "| Labelled cause | Reproduced | Found | Recall | Meaning |",
        "|---|---:|---:|---:|---|",
    ]
    for category, (reproduced, detected) in result.category_totals().items():
        lines.append(
            f"| `{category}` | {reproduced} | {detected} | {_ratio(detected, reproduced)} | "
            f"{CATEGORY_MEANING.get(category, '')} |"
        )

    lines += ["", *(f"- {note}" for note in _notes(result))]
    return "\n".join(lines) + "\n"


def render_json(result: RealWorldResult) -> str:
    payload: dict[str, Any] = {
        "dataset": "IDoFT",
        "dataset_sha": result.dataset_sha,
        "repositories": result.repositories,
        "suite_runs": result.runs,
        "test_executions": result.results,
        "tests_collected": result.collected,
        "labels_total": result.labelled,
        "labels_executed": result.executed,
        "labels_reproduced": result.reproduced,
        "labels_detected": result.detected,
        "recall": round(result.recall, 4),
        "flagged": result.flagged,
        "flagged_with_divergence": result.flagged_with_divergence,
        "precision": round(result.precision, 4),
        "order_detected": result.order_detected,
        "order_diagnosed": result.order_diagnosed,
        "order_polluter_named": result.order_polluter_named,
        "not_reproducible": result.not_reproducible,
        "correctly_withheld": result.correctly_withheld,
        "wrongly_called_flaky": result.wrongly_called_flaky,
        "unlabelled_flagged": result.unlabelled_flagged,
        "unlabelled_flagged_with_divergence": result.unlabelled_flagged_with_divergence,
        "by_category": {
            category: {"reproduced": reproduced, "detected": detected}
            for category, (reproduced, detected) in result.category_totals().items()
        },
        "projects": [_project_payload(project) for project in result.projects],
        "skipped": list(result.skipped),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _project_payload(project: ProjectScore) -> dict[str, Any]:
    return {
        "repo": project.repo,
        "sha": project.sha,
        "iterations": project.iterations,
        "tests_collected": project.collected,
        "runs": project.runs,
        "results": project.results,
        "labels": project.labelled,
        "labels_scored": project.labelled_scored,
        "labels_executed": project.executed,
        "labels_reproduced": project.reproduced,
        "labels_detected": project.detected,
        "recall": round(project.recall, 4),
        "flagged": project.flagged,
        "flagged_with_divergence": project.flagged_with_divergence,
        "precision": round(project.precision, 4),
        "order_detected": project.order_labels_detected,
        "order_diagnosed": project.order_diagnosed,
        "order_polluter_named": project.order_polluter_named,
        "not_reproducible_passed": project.not_reproducible_passed,
        "not_reproducible_failed": project.not_reproducible_failed,
        "correctly_withheld": project.correctly_withheld,
        "wrongly_called_flaky": project.wrongly_called_flaky,
        "unlabelled_flagged": project.unlabelled_flagged,
        "by_category": {
            category: {"reproduced": reproduced, "detected": detected}
            for category, (reproduced, detected) in project.by_category.items()
        },
        "misses": list(project.misses),
        "suspect": list(project.suspect),
    }


FORMATS = ("console", "md", "json")


def render(result: RealWorldResult, fmt: str) -> str:
    """Non-console formats. `console` goes through `render_console`."""
    if fmt in ("md", "markdown"):
        return render_markdown(result)
    if fmt == "json":
        return render_json(result)
    raise ValueError(f"Unknown format {fmt!r}. Expected one of: {', '.join(FORMATS)}")


__all__ = ["FORMATS", "render", "render_console", "render_json", "render_markdown"]
