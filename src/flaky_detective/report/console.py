"""Terminal output.

Read by someone whose build just went red. Lead with the answer, keep the columns
scannable, and never imply more certainty than the counts support.

Formatting only. Every number here is read straight off the analysis; if a
reporter needs a derived value it belongs in `analysis` instead, or the console and
markdown outputs will quietly drift apart.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..models import (
    AnalysisReport,
    Cause,
    DatabaseStats,
    FailureCluster,
    TestAnalysis,
    TriageReport,
    Verdict,
)

VERDICT_STYLE = {
    Verdict.FLAKY: "yellow",
    Verdict.REGRESSION: "red",
    Verdict.BROKEN: "red dim",
    Verdict.FIXED: "green",
    Verdict.STABLE: "dim",
}

CAUSE_LABEL = {
    Cause.TIMEOUT: "timeout",
    Cause.RACE: "race",
    Cause.ORDER_DEPENDENCE: "order",
    Cause.NETWORK: "network",
    Cause.RESOURCE: "resource",
    Cause.TIME_DEPENDENCE: "time",
    Cause.RANDOMNESS: "random",
    Cause.ASSERTION: "assertion",
    Cause.UNKNOWN: "unknown",
}


def render_report(
    report: AnalysisReport,
    console: Console | None = None,
    *,
    limit: int = 25,
    show_clusters: bool = True,
    show_stable: bool = False,
) -> None:
    """Print the ranked flake report."""
    out = console or Console()

    _render_headline(report, out)

    rows = [t for t in report.tests if show_stable or t.verdict is not Verdict.STABLE]
    if not rows:
        out.print("No flaky tests, regressions, or broken tests found.", style="green")
        _render_caveats(report, out)
        return

    shown = rows[:limit]
    prefix = _common_prefix([t.test_id for t in shown])
    if prefix:
        out.print(f"All under {prefix}", style="dim")
    out.print(_tests_table(shown, prefix, width=out.width))

    if len(rows) > limit:
        out.print(f"  ... {len(rows) - limit} more. Use --limit to see them.", style="dim")

    _render_diagnosis(rows[:limit], out)

    if show_clusters and report.clusters:
        shared = [c for c in report.clusters if c.test_count > 1]
        if shared:
            out.print()
            out.print(_clusters_table(shared[:10]))

    _render_caveats(report, out)


def _render_headline(report: AnalysisReport, out: Console) -> None:
    counts = {
        "flaky": len(report.flaky),
        "regression": len(report.regressions),
        "broken": len(report.broken),
        "fixed": len(report.fixed),
    }

    headline = Text()
    if counts["flaky"]:
        headline.append(f"{counts['flaky']} flaky", style="bold yellow")
    else:
        headline.append("0 flaky", style="bold green")

    for label, style in (("regression", "bold red"), ("broken", "red"), ("fixed", "green")):
        if counts[label]:
            headline.append("  ")
            headline.append(f"{counts[label]} {label}", style=style)

    window = ""
    if report.window_start and report.window_end:
        window = f"\n{_date(report.window_start)} to {_date(report.window_end)}"

    out.print(
        Panel(
            headline,
            title=(
                f"{report.total_runs} runs, {report.total_results} results, "
                f"{len(report.tests)} tests{window}"
            ),
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
    )


POSITION_DETAIL_THRESHOLD = 0.5
"""Below this, position separation is not worth reporting.

Order dependence is established by naming a polluter, not by position, so a
near-zero separation is normal and printing it only invites the reader to doubt a
sound verdict.
"""

FIXED_COLUMNS_WIDTH = 64
"""Total width of every column except the test id, including separators.

Used to cap the test column so the table's natural width fits the terminal.
Without the cap, rich shrinks proportionally and squeezes the numeric headers into
ellipses, which is the one thing that must stay readable.
"""


def _tests_table(tests: list[TestAnalysis], prefix: str = "", width: int = 100) -> Table:
    """Build the ranked table.

    The test column is capped to whatever the terminal has left after the numeric
    columns, so long ids lose characters instead of the headers doing so.
    """
    table = Table(box=None, pad_edge=False, header_style="bold dim", expand=False)
    table.add_column("score", justify="right", width=5, no_wrap=True)
    table.add_column("verdict", width=10, no_wrap=True)
    table.add_column("runs", justify="right", width=4, no_wrap=True)
    table.add_column("p/f", justify="right", width=7, no_wrap=True)
    table.add_column("flips", justify="right", width=5, no_wrap=True)
    table.add_column("commit", justify="right", width=6, no_wrap=True)
    table.add_column("cause", width=9, no_wrap=True)
    # One row per test. Folding long ids across lines makes the table much harder
    # to scan.
    table.add_column(
        "test",
        no_wrap=True,
        overflow="ellipsis",
        max_width=max(20, width - FIXED_COLUMNS_WIDTH),
    )

    for test in tests:
        divergence = (
            f"{test.divergent_commits}/{test.observed_commits}" if test.observed_commits else "-"
        )
        cause = CAUSE_LABEL.get(test.cause.cause, "") if test.cause else ""

        table.add_row(
            f"{test.score:.2f}",
            Text(str(test.verdict), style=VERDICT_STYLE.get(test.verdict, "")),
            str(test.runs),
            f"{test.passes}/{test.failures}",
            str(test.flips),
            divergence,
            Text(cause, style="cyan" if cause else ""),
            _strip(test.test_id, prefix),
        )

    return table


def _common_prefix(test_ids: list[str]) -> str:
    """Longest shared directory prefix, when stripping it would help.

    Test ids in one report usually share a long path prefix that carries no
    information and costs the width the actual test name needs.
    """
    if len(test_ids) < 2:
        return ""

    shortest = min(test_ids, key=len)
    prefix = ""
    for index, char in enumerate(shortest):
        if all(t[index] == char for t in test_ids):
            prefix += char
        else:
            break

    cut = max(prefix.rfind("/"), prefix.rfind("::"))
    if cut <= 0:
        return ""
    trimmed = prefix[: cut + 1]
    return trimmed if len(trimmed) >= 8 else ""


def _strip(test_id: str, prefix: str) -> str:
    return test_id[len(prefix) :] if prefix and test_id.startswith(prefix) else test_id


def _render_diagnosis(tests: list[TestAnalysis], out: Console) -> None:
    """Print the actionable detail for the worst offenders.

    Only for tests where there is something concrete to say. A generic
    "assertion failed" line adds noise without adding information.
    """
    interesting = [
        t
        for t in tests
        if t.verdict is Verdict.FLAKY
        and t.cause is not None
        and (t.order is not None or t.cause.cause is not Cause.ASSERTION)
    ][:5]

    if not interesting:
        return

    out.print()
    out.print("Diagnosis", style="bold dim")

    for test in interesting:
        out.print(f"  {_short(test.test_id)}", style="bold")
        assert test.cause is not None

        if test.order is not None:
            # The polluter is the evidence the verdict rests on, so it leads.
            if test.order.likely_polluter:
                out.print(
                    f"    order dependent: fails after "
                    f"{_short(test.order.likely_polluter)} in "
                    f"{test.order.polluter_failure_share:.0%} of its failures",
                    style="cyan",
                )
            # Position separation is supporting detail only, and near zero it says
            # nothing worth the line it would occupy.
            if test.order.separation >= POSITION_DETAIL_THRESHOLD:
                out.print(
                    f"    also runs later when it fails: position "
                    f"{test.order.mean_position_on_fail:.0f} on average versus "
                    f"{test.order.mean_position_on_pass:.0f} when passing",
                    style="dim",
                )
        elif test.cause.matched:
            out.print(
                f"    likely {test.cause.cause} (matched: {', '.join(test.cause.matched)})",
                style="cyan",
            )

        out.print(f"    {test.cause.remediation}", style="dim")

        if len(test.signatures) > 1:
            out.print(
                f"    {len(test.signatures)} distinct failure signatures, "
                "which usually means more than one bug",
                style="dim",
            )


def _clusters_table(clusters: list[FailureCluster]) -> Table:
    table = Table(
        box=None,
        pad_edge=False,
        header_style="bold dim",
        title="Shared failure signatures (one cause, several tests)",
        title_style="bold dim",
        title_justify="left",
    )
    table.add_column("tests", justify="right", width=5)
    table.add_column("fails", justify="right", width=5)
    table.add_column("cause", width=9)
    table.add_column("signature", overflow="fold")

    for cluster in clusters:
        cause = CAUSE_LABEL.get(cluster.cause.cause, "") if cluster.cause else ""
        table.add_row(
            str(cluster.test_count),
            str(cluster.failure_count),
            cause,
            cluster.signature[:110],
        )
    return table


def _render_caveats(report: AnalysisReport, out: Console) -> None:
    """State plainly when the evidence is weak.

    The tool's credibility depends on never overstating what it knows, so this is
    not optional decoration.
    """
    if not report.has_commit_data:
        out.print()
        out.print(
            "Note: no run carried a commit SHA, so same-commit divergence could not "
            "be measured.\nScores rest on flip rate alone, which is a weaker signal. "
            "Run inside a git repo, or pass --commit, to get the primary signal.",
            style="yellow",
        )
        return

    thin = [t for t in report.flaky if t.confidence < 1.0]
    if thin:
        out.print()
        out.print(
            f"Note: {len(thin)} of {len(report.flaky)} flaky verdicts rest on fewer "
            "runs than the confidence threshold.\nTheir scores are damped and will "
            "move as more runs accumulate.",
            style="dim",
        )


def render_triage(result: TriageReport, console: Console | None = None, *, limit: int = 20) -> None:
    """Print the build-duty answer: investigate, or re-run?"""
    out = console or Console()

    if result.total_failures == 0:
        out.print("No failures in this run.", style="green")
        return

    if result.all_known_flaky:
        out.print(
            Panel(
                Text(
                    f"All {_plural(result.total_failures, 'failure')} "
                    "are known flakes. No new breakage.",
                    style="bold yellow",
                ),
                border_style="yellow",
                padding=(0, 1),
            )
        )
    else:
        count = len(result.actionable)
        out.print(
            Panel(
                Text(
                    f"{_plural(count, 'failure')} "
                    f"{'needs' if count == 1 else 'need'} attention "
                    f"({len(result.known_flakes)} known "
                    f"{'flake' if len(result.known_flakes) == 1 else 'flakes'} ignored)",
                    style="bold red",
                ),
                border_style="red",
                padding=(0, 1),
            )
        )

    if result.regressions:
        out.print()
        out.print("Known regressions or broken tests", style="bold red")
        for entry in result.regressions[:limit]:
            verdict = entry.history.verdict if entry.history else "unknown"
            out.print(f"  [{verdict}] {_short(entry.test_id)}")
            if entry.message:
                out.print(f"      {_one_line(entry.message)}", style="dim")

    if result.new_failures:
        out.print()
        out.print("New failures, no flaky history", style="bold red")
        for entry in result.new_failures[:limit]:
            out.print(f"  {_short(entry.test_id)}")
            if entry.message:
                out.print(f"      {_one_line(entry.message)}", style="dim")

    if result.known_flakes:
        out.print()
        out.print("Known flakes", style="bold yellow")
        for entry in result.known_flakes[:limit]:
            history = entry.history
            evidence = (
                f"score {history.score:.2f}, {history.failures}/{history.runs} runs failed"
                if history
                else ""
            )
            out.print(f"  {_short(entry.test_id)}", style="yellow")
            out.print(f"      {evidence}", style="dim")


def render_stats(stats: DatabaseStats, console: Console | None = None) -> None:
    """Print a database summary."""
    out = console or Console()

    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")

    for label, value in (
        ("database", stats.path),
        ("runs", str(stats.runs)),
        ("results", str(stats.results)),
        ("distinct tests", str(stats.tests)),
        ("failures", str(stats.failures)),
        ("commits", str(stats.commits)),
        ("branches", str(stats.branches)),
        (
            "runners",
            ", ".join(f"{name} ({count})" for name, count in stats.runners.items()) or "none",
        ),
        ("first run", _date(stats.first_run or "") or "-"),
        ("last run", _date(stats.last_run or "") or "-"),
    ):
        table.add_row(label, value)

    out.print(table)

    if stats.is_empty:
        out.print()
        out.print("Nothing recorded yet. Start with:\n  flaky hunt -- pytest tests/", style="dim")


def render_history(
    test_id: str,
    analysis: TestAnalysis,
    timeline: list[tuple[str, str, str | None]],
    console: Console | None = None,
) -> None:
    """Print one test's timeline.

    `timeline` is (started_at, status, message) already prepared by the caller.
    """
    out = console or Console()

    out.print(f"{test_id}", style="bold")
    out.print(
        f"  {analysis.verdict}, score {analysis.score:.2f}  |  "
        f"{analysis.passes} passed, {analysis.failures} failed, "
        f"{analysis.skips} skipped over {analysis.runs} runs  |  "
        f"{analysis.flips} flips",
        style="dim",
    )
    if analysis.observed_commits:
        out.print(
            f"  same-commit divergence: {analysis.divergent_commits} of "
            f"{analysis.observed_commits} commits where it ran more than once",
            style="dim",
        )
    out.print()

    table = Table(box=None, pad_edge=False, header_style="bold dim")
    table.add_column("when", width=17)
    table.add_column("status", width=8)
    table.add_column("failure", overflow="fold")

    for started_at, status, message in timeline:
        style = (
            "red" if status in ("failed", "error") else ("dim" if status == "skipped" else "green")
        )
        table.add_row(
            _date(started_at, with_time=True),
            Text(status, style=style),
            _one_line(message or ""),
        )

    out.print(table)

    if analysis.cause:
        out.print()
        out.print(f"Likely cause: {analysis.cause.cause}", style="cyan")
        if analysis.cause.matched:
            out.print(f"  matched: {', '.join(analysis.cause.matched)}", style="dim")
        out.print(f"  {analysis.cause.remediation}", style="dim")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _short(test_id: str, width: int = 78) -> str:
    """Trim from the left, because the distinguishing part of a test id is the end."""
    if len(test_id) <= width:
        return test_id
    return "..." + test_id[-(width - 3) :]


def _one_line(text: str, width: int = 100) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 3] + "..."


def _date(value: str, *, with_time: bool = False) -> str:
    if not value:
        return ""
    return value[:16].replace("T", " ") if with_time else value[:10]
