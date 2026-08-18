"""Markdown output, sized for a pull-request comment.

A PR comment nobody reads is worse than no comment, so this is deliberately short:
the headline, a table capped at a sensible length, and the diagnosis for the worst
few. Detail lives behind `flaky history`.
"""

from __future__ import annotations

from ..models import AnalysisReport, Cause, TestAnalysis, TriageReport, Verdict

POSITION_DETAIL_THRESHOLD = 0.5
"""Below this, position separation is supporting detail not worth printing."""

VERDICT_MARK = {
    Verdict.FLAKY: "flaky",
    Verdict.REGRESSION: "regression",
    Verdict.BROKEN: "broken",
    Verdict.FIXED: "fixed",
    Verdict.STABLE: "stable",
}


def render_report(report: AnalysisReport, *, limit: int = 15) -> str:
    """Render the ranked flake report as Markdown."""
    lines: list[str] = ["## Flaky test report", ""]

    counts = (
        f"**{len(report.flaky)} flaky**, {len(report.regressions)} regression, "
        f"{len(report.broken)} broken, {len(report.fixed)} fixed "
        f"across {report.total_runs} runs of {len(report.tests)} tests."
    )
    lines.extend([counts, ""])

    if report.window_start and report.window_end:
        lines.extend([f"Window: {report.window_start[:10]} to {report.window_end[:10]}", ""])

    rows = [t for t in report.tests if t.verdict is not Verdict.STABLE]
    if not rows:
        lines.append("No flaky tests, regressions, or broken tests found.")
        lines.extend(_caveats(report))
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            "| Score | Verdict | Runs | Pass/Fail | Flips | Same-commit | Cause | Test |",
            "|------:|---------|-----:|----------:|------:|------------:|-------|------|",
        ]
    )
    for test in rows[:limit]:
        divergence = (
            f"{test.divergent_commits}/{test.observed_commits}" if test.observed_commits else "-"
        )
        cause = test.cause.cause if test.cause else ""
        lines.append(
            f"| {test.score:.2f} | {VERDICT_MARK[test.verdict]} | {test.runs} | "
            f"{test.passes}/{test.failures} | {test.flips} | {divergence} | "
            f"{cause} | `{test.test_id}` |"
        )

    if len(rows) > limit:
        lines.append(f"| | | | | | | | _and {len(rows) - limit} more_ |")

    lines.append("")
    lines.extend(_diagnosis(rows))
    lines.extend(_clusters(report))
    lines.extend(_caveats(report))

    return "\n".join(lines).rstrip() + "\n"


def _diagnosis(tests: list[TestAnalysis], limit: int = 5) -> list[str]:
    interesting = [
        t
        for t in tests
        if t.verdict is Verdict.FLAKY
        and t.cause is not None
        and (t.order is not None or t.cause.cause is not Cause.ASSERTION)
    ][:limit]

    if not interesting:
        return []

    lines = ["### Diagnosis", ""]
    for test in interesting:
        assert test.cause is not None
        lines.append(f"**`{test.test_id}`**")
        lines.append("")

        if test.order is not None:
            if test.order.likely_polluter:
                lines.append(
                    f"- **Order dependent**: fails after "
                    f"`{test.order.likely_polluter}` in "
                    f"{test.order.polluter_failure_share:.0%} of its failures."
                )
            if test.order.separation >= POSITION_DETAIL_THRESHOLD:
                lines.append(
                    f"- Also runs later when it fails: position "
                    f"{test.order.mean_position_on_fail:.0f} on average versus "
                    f"{test.order.mean_position_on_pass:.0f} when passing."
                )
        elif test.cause.matched:
            lines.append(
                f"- Likely **{test.cause.cause}** "
                f"(matched: {', '.join(f'`{m}`' for m in test.cause.matched)})."
            )

        lines.append(f"- {test.cause.remediation}")
        if test.representative_message:
            lines.append(f"- Example failure: `{_one_line(test.representative_message, 160)}`")
        lines.append("")

    return lines


def _clusters(report: AnalysisReport, limit: int = 5) -> list[str]:
    shared = [c for c in report.clusters if c.test_count > 1][:limit]
    if not shared:
        return []

    lines = [
        "### Shared failure signatures",
        "",
        "One cause, several tests. Fixing these is the cheapest win available.",
        "",
        "| Tests | Failures | Cause | Signature |",
        "|------:|---------:|-------|-----------|",
    ]
    for cluster in shared:
        cause = cluster.cause.cause if cluster.cause else ""
        lines.append(
            f"| {cluster.test_count} | {cluster.failure_count} | {cause} | "
            f"`{_one_line(cluster.signature, 120)}` |"
        )
    lines.append("")
    return lines


def _caveats(report: AnalysisReport) -> list[str]:
    if not report.has_commit_data:
        return [
            "",
            "> **Weak evidence.** No run carried a commit SHA, so same-commit "
            "divergence could not be measured. These scores rest on flip rate "
            "alone. Run inside a git repo, or pass `--commit`, for the primary "
            "signal.",
        ]

    thin = [t for t in report.flaky if t.confidence < 1.0]
    if thin:
        return [
            "",
            f"> {len(thin)} of {len(report.flaky)} flaky verdicts rest on fewer runs "
            "than the confidence threshold. Their scores are damped and will move as "
            "more runs accumulate.",
        ]
    return []


def render_triage(result: TriageReport) -> str:
    """Render the triage answer as Markdown, for posting on a red build."""
    lines: list[str] = ["## Flaky test triage", ""]

    if result.total_failures == 0:
        lines.append("No failures in this run.")
        return "\n".join(lines) + "\n"

    if result.all_known_flaky:
        lines.append(
            f"**All {_plural(result.total_failures, 'failure')} are known flakes.** "
            "No new breakage in this run."
        )
    else:
        count = len(result.actionable)
        lines.append(
            f"**{_plural(count, 'failure')} {'needs' if count == 1 else 'need'} "
            f"attention.** {len(result.known_flakes)} known "
            f"{'flake' if len(result.known_flakes) == 1 else 'flakes'} set aside."
        )
    lines.append("")

    if result.regressions:
        lines.extend(["### Known regressions or broken tests", ""])
        for entry in result.regressions:
            verdict = entry.history.verdict if entry.history else "unknown"
            lines.append(f"- **{verdict}** `{entry.test_id}`")
            if entry.message:
                lines.append(f"  - `{_one_line(entry.message, 160)}`")
        lines.append("")

    if result.new_failures:
        lines.extend(["### New failures, no flaky history", ""])
        for entry in result.new_failures:
            lines.append(f"- `{entry.test_id}`")
            if entry.message:
                lines.append(f"  - `{_one_line(entry.message, 160)}`")
        lines.append("")

    if result.known_flakes:
        lines.extend(
            [
                "<details>",
                f"<summary>{len(result.known_flakes)} known flakes</summary>",
                "",
                "| Score | Failed/Runs | Test |",
                "|------:|------------:|------|",
            ]
        )
        for entry in result.known_flakes:
            history = entry.history
            ratio = f"{history.failures}/{history.runs}" if history else "-"
            lines.append(f"| {entry.score:.2f} | {ratio} | `{entry.test_id}` |")
        lines.extend(["", "</details>", ""])

    return "\n".join(lines).rstrip() + "\n"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _one_line(text: str, width: int = 100) -> str:
    collapsed = " ".join(text.split()).replace("`", "'")
    return collapsed if len(collapsed) <= width else collapsed[: width - 3] + "..."
