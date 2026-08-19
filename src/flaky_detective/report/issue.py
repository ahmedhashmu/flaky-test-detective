"""Turn a diagnosis into something you can paste into an issue tracker or a chat.

The point is the contrast. A generic "fix flaky test" ticket gets triaged into oblivion
because it contains no information. A ticket that opens with "fails after
test_registers_session in 100% of observed failures, same-commit divergence confirmed at
4 of 7 commits" is a ticket someone can pick up.

Formatting only. Everything here is read off an existing analysis, and no request is
made anywhere: the output goes to stdout for the user to paste or pipe. That keeps the
tool credential-free, which is worth more than a built-in API client.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Attribution, BlameResult, TestAnalysis, Verdict

FORMATS = ("markdown", "github", "jira", "slack", "json")

_SEVERITY = {
    Verdict.REGRESSION: "High",
    Verdict.BROKEN: "High",
    Verdict.FLAKY: "Medium",
    Verdict.FIXED: "Low",
    Verdict.STABLE: "Low",
}


def render(
    test: TestAnalysis,
    blame: BlameResult | None = None,
    *,
    fmt: str = "markdown",
    repository: str | None = None,
) -> str:
    """Render an issue body or chat message for one test."""
    if fmt in ("markdown", "github"):
        return _markdown(test, blame, repository=repository)
    if fmt == "jira":
        return _jira(test, blame)
    if fmt == "slack":
        return json.dumps(slack_blocks(test, blame, repository=repository), indent=2) + "\n"
    if fmt == "json":
        return json.dumps(_as_dict(test, blame), indent=2) + "\n"
    raise ValueError(f"Unknown format {fmt!r}. Available: {', '.join(FORMATS)}")


def title(test: TestAnalysis) -> str:
    """A title that says what is wrong, not just that something is.

    Leads with the diagnosed cause so a reader scanning a backlog can tell an
    order-dependence bug from a timeout without opening anything.
    """
    cause = str(test.cause.cause).replace("_", " ") if test.cause else "flaky"
    leaf = test.test_id.rsplit("::", 1)[-1]

    if test.verdict is Verdict.REGRESSION:
        return f"Regression: {leaf} has been failing consistently"
    if test.verdict is Verdict.BROKEN:
        return f"Broken: {leaf} has never passed"
    return f"{cause.capitalize()} flaky test: {leaf}"


def _evidence_lines(test: TestAnalysis) -> list[str]:
    """The proof, as bullets. Same hierarchy the rest of the tool uses."""
    lines: list[str] = []

    if test.divergent_commits:
        lines.append(
            f"**Same-commit divergence confirmed** at {test.divergent_commits} of "
            f"{test.observed_commits} commits where it ran more than once. The code was "
            "identical between a pass and a fail, so the code is not the variable."
        )
    if test.retries:
        lines.append(
            f"**Runner-recorded retry** x{test.retries}: the test runner itself saw this "
            "fail then pass inside one run."
        )
    if test.order and test.order.likely_polluter:
        lines.append(
            f"**Order dependent**: fails after `{test.order.likely_polluter}` in "
            f"{test.order.polluter_failure_share:.0%} of its failures, more often than its "
            "own base failure rate explains. Retrying will not help — the state is already "
            "polluted."
        )
    if not test.has_divergence_data:
        lines.append(
            "_No commit SHAs in the recorded runs, so same-commit divergence could not be "
            "measured and this rests on the weaker flip-rate signal._"
        )
    return lines


def _markdown(test: TestAnalysis, blame: BlameResult | None, *, repository: str | None) -> str:
    lines = [
        f"## {title(test)}",
        "",
        f"`{test.test_id}`",
        "",
        "| | |",
        "|---|---|",
        f"| Verdict | **{test.verdict}** |",
        f"| Flakiness score | {test.score:.2f} (confidence {test.confidence:.0%}) |",
        f"| Failure rate | {test.failures}/{test.runs} runs ({test.failure_rate:.0%}) |",
        f"| Pass / fail flips | {test.flips} |",
        f"| Same-commit divergence | {test.divergent_commits}/{test.observed_commits} |",
    ]
    if test.cause:
        lines.append(f"| Likely cause | {test.cause.cause} |")
    if test.first_seen:
        lines.append(f"| First seen | {test.first_seen[:10]} |")
    if test.last_seen:
        lines.append(f"| Last seen | {test.last_seen[:10]} |")

    evidence = _evidence_lines(test)
    if evidence:
        lines.extend(["", "### Evidence", ""])
        lines.extend(f"- {line}" for line in evidence)

    if blame is not None:
        lines.extend(["", "### When it started", "", blame.explanation])
        if blame.attribution is Attribution.INTRODUCED:
            lines.extend(
                [
                    "",
                    f"- First diverged at `{blame.commit_sha}`",
                    f"- Last clean commit `{blame.previous_clean_sha}`",
                ]
            )
            if repository:
                lines.append(
                    f"- Compare: {repository.rstrip('/')}/compare/"
                    f"{blame.previous_clean_sha}...{blame.commit_sha}"
                )

    if test.cause:
        lines.extend(["", "### Suggested fix", "", test.cause.remediation])
        if test.cause.matched and not (test.order and test.order.likely_polluter):
            terms = ", ".join(f"`{term}`" for term in test.cause.matched)
            lines.extend(
                [
                    "",
                    f"_Cause is a heuristic, matched on: {terms}. Check it rather than "
                    "trusting it._",
                ]
            )

    if test.representative_message:
        lines.extend(
            ["", "### Example failure", "", "```", test.representative_message[:1200], "```"]
        )

    if len(test.signatures) > 1:
        lines.extend(
            [
                "",
                f"> {len(test.signatures)} distinct failure signatures were recorded, so "
                "this is probably more than one bug.",
            ]
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "<sub>Generated by [flaky-test-detective]"
            "(https://github.com/ahmedhashmu/flaky-test-detective). "
            f'Reproduce with `flaky history "{test.test_id}"`.</sub>',
        ]
    )
    return "\n".join(lines) + "\n"


def _jira(test: TestAnalysis, blame: BlameResult | None) -> str:
    """Jira wiki markup, which is not Markdown and will not render if you pretend it is."""
    lines = [
        f"h2. {title(test)}",
        "",
        f"{{{{{test.test_id}}}}}",
        "",
        "|| Field || Value ||",
        f"| Verdict | *{test.verdict}* |",
        f"| Flakiness score | {test.score:.2f} (confidence {test.confidence:.0%}) |",
        f"| Failure rate | {test.failures}/{test.runs} ({test.failure_rate:.0%}) |",
        f"| Same-commit divergence | {test.divergent_commits}/{test.observed_commits} |",
        f"| Severity | {_SEVERITY.get(test.verdict, 'Medium')} |",
    ]
    if test.cause:
        lines.append(f"| Likely cause | {test.cause.cause} |")

    evidence = _evidence_lines(test)
    if evidence:
        lines.extend(["", "h3. Evidence", ""])
        lines.extend("* " + line.replace("**", "*").replace("_", "") for line in evidence)

    if blame is not None:
        lines.extend(["", "h3. When it started", "", blame.explanation])

    if test.cause:
        lines.extend(["", "h3. Suggested fix", "", test.cause.remediation])

    if test.representative_message:
        lines.extend(["", "{code}", test.representative_message[:1200], "{code}"])

    return "\n".join(lines) + "\n"


def slack_blocks(
    test: TestAnalysis, blame: BlameResult | None, *, repository: str | None = None
) -> dict[str, Any]:
    """A Slack Block Kit payload, ready to POST to an incoming webhook.

    Emitted rather than sent. Posting it would mean holding a webhook URL, and staying
    credential-free is worth more than saving the user a `curl`.
    """
    emoji = "🔴" if test.verdict in (Verdict.REGRESSION, Verdict.BROKEN) else "🟠"
    leaf = test.test_id.rsplit("::", 1)[-1]

    fields = [
        {"type": "mrkdwn", "text": f"*Verdict*\n{test.verdict}"},
        {"type": "mrkdwn", "text": f"*Score*\n{test.score:.2f}"},
        {
            "type": "mrkdwn",
            "text": f"*Failure rate*\n{test.failures}/{test.runs} ({test.failure_rate:.0%})",
        },
        {
            "type": "mrkdwn",
            "text": f"*Same-commit*\n{test.divergent_commits}/{test.observed_commits}",
        },
    ]
    if test.cause:
        fields.append({"type": "mrkdwn", "text": f"*Likely cause*\n{test.cause.cause}"})
    if blame and blame.attribution is Attribution.INTRODUCED:
        fields.append({"type": "mrkdwn", "text": f"*Introduced near*\n`{blame.commit_sha}`"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {title(test)}"[:150]},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"`{test.test_id}`"},
        },
        {"type": "section", "fields": fields[:10]},
    ]

    evidence = _evidence_lines(test)
    if evidence:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Evidence*\n" + "\n".join(f"• {line}" for line in evidence[:3]),
                },
            }
        )

    if test.cause:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Suggested fix*\n{test.cause.remediation}"},
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f'flaky-test-detective · `flaky history "{leaf}"`',
                }
            ],
        }
    )

    return {"text": f"{emoji} {title(test)}", "blocks": blocks}


def _as_dict(test: TestAnalysis, blame: BlameResult | None) -> dict[str, Any]:
    return {
        "title": title(test),
        "test_id": test.test_id,
        "verdict": str(test.verdict),
        "severity": _SEVERITY.get(test.verdict, "Medium"),
        "score": test.score,
        "confidence": test.confidence,
        "failures": test.failures,
        "runs": test.runs,
        "failure_rate": round(test.failure_rate, 4),
        "flips": test.flips,
        "divergent_commits": test.divergent_commits,
        "observed_commits": test.observed_commits,
        "cause": str(test.cause.cause) if test.cause else None,
        "remediation": test.cause.remediation if test.cause else None,
        "polluter": test.order.likely_polluter if test.order else None,
        "evidence": _evidence_lines(test),
        "blame": (
            {
                "attribution": str(blame.attribution),
                "commit_sha": blame.commit_sha,
                "previous_clean_sha": blame.previous_clean_sha,
                "explanation": blame.explanation,
            }
            if blame
            else None
        ),
        "example_failure": test.representative_message,
    }
