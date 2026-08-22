"""Issue and chat export.

The value of this feature is entirely in the contrast with what people write by hand. A
ticket saying "fix flaky test" gets triaged into oblivion. These assertions check that
the diagnosis actually reaches the ticket -- the polluter, the counts, the evidence and
the suggested fix.
"""

from __future__ import annotations

import json

import pytest

from flaky_detective.analysis import analyze, analyze_one
from flaky_detective.analysis.attribution import blame
from flaky_detective.analysis.ordering import build_ordering_index
from flaky_detective.config import Config
from flaky_detective.models import Status
from flaky_detective.report import issue

from conftest import outcome, sequence


def flaky_analysis(message: str = "TimeoutError: timed out after 30s"):
    outcomes = sequence("tests/test_a.py::test_x", ".F" * 8, commits=["c1"] * 16, message=message)
    report = analyze(outcomes, Config())
    return next(t for t in report.tests if t.test_id == "tests/test_a.py::test_x"), outcomes


def order_dependent_analysis():
    """A victim that fails only when a named polluter ran immediately before."""
    outcomes = []
    for index in range(12):
        polluter_first = index % 2 == 0
        layout = (
            [("tests/t.py::polluter", 0), ("tests/t.py::victim", 1)]
            if polluter_first
            else [("tests/t.py::victim", 2), ("tests/t.py::polluter", 8)]
        )
        for test_id, position in layout:
            failed = test_id.endswith("victim") and polluter_first
            outcomes.append(
                outcome(
                    test_id,
                    Status.FAILED if failed else Status.PASSED,
                    run=f"r{index}",
                    commit="c1",
                    position=position,
                    message="KeyError: 'session' already exists" if failed else None,
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                )
            )

    analysis = analyze_one(
        "tests/t.py::victim",
        [o for o in outcomes if o.test_id == "tests/t.py::victim"],
        Config(),
        ordering=build_ordering_index(outcomes),
    )
    return analysis, outcomes


class TestTitle:
    def test_leads_with_the_cause(self) -> None:
        """So a backlog can be scanned without opening anything."""
        analysis, _ = flaky_analysis()
        assert "timeout" in issue.title(analysis).lower()
        assert "test_x" in issue.title(analysis)

    def test_regression_is_not_called_flaky(self) -> None:
        outcomes = sequence("t.py::test_r", "........FFFF", commits=["c1"] * 8 + ["c2"] * 4)
        analysis = next(t for t in analyze(outcomes, Config()).tests)
        assert "Regression" in issue.title(analysis)
        assert "flaky" not in issue.title(analysis).lower()

    def test_broken_says_never_passed(self) -> None:
        outcomes = sequence("t.py::test_b", "F" * 12, commits=["c1"] * 12)
        analysis = next(t for t in analyze(outcomes, Config()).tests)
        assert "never passed" in issue.title(analysis)


class TestMarkdown:
    def test_includes_the_counts(self) -> None:
        analysis, _ = flaky_analysis()
        body = issue.render(analysis)
        assert "8/16 runs" in body
        assert "0.2" in body or "1.00" in body  # the score, whatever it is
        assert "Same-commit divergence" in body

    def test_includes_the_evidence_section(self) -> None:
        analysis, _ = flaky_analysis()
        body = issue.render(analysis)
        assert "### Evidence" in body
        assert "the code is not the variable" in body

    def test_includes_the_suggested_fix(self) -> None:
        analysis, _ = flaky_analysis()
        assert "### Suggested fix" in issue.render(analysis)

    def test_names_the_polluter(self) -> None:
        """The single most actionable thing the tool knows."""
        analysis, _ = order_dependent_analysis()
        body = issue.render(analysis)
        assert "polluter" in body.lower() or "Order dependent" in body
        assert "tests/t.py::polluter" in body
        assert "Retrying will not help" in body

    def test_labels_a_heuristic_cause_as_one(self) -> None:
        analysis, _ = flaky_analysis()
        body = issue.render(analysis)
        assert "heuristic" in body.lower()

    def test_includes_blame_when_available(self) -> None:
        analysis, outcomes = flaky_analysis()
        body = issue.render(analysis, blame("tests/test_a.py::test_x", outcomes))
        assert "### When it started" in body

    def test_compare_link_when_a_repository_is_given(self) -> None:
        outcomes = sequence(
            "t.py::test_x",
            "....F.",
            commits=["c1", "c1", "c2", "c2", "c3", "c3"],
            message="boom",
        )
        analysis = next(t for t in analyze(outcomes, Config()).tests)
        attribution = blame("t.py::test_x", outcomes)
        body = issue.render(analysis, attribution, repository="https://github.com/acme/widget")
        if attribution.attribution.value == "introduced":
            assert "/compare/" in body

    def test_warns_when_there_is_more_than_one_bug(self) -> None:
        outcomes = [
            outcome(
                "t.py::test_x",
                Status.FAILED if index % 2 else Status.PASSED,
                run=f"r{index}",
                commit="c1",
                message=f"Error number {index}" if index % 2 else None,
                started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
            )
            for index in range(12)
        ]
        analysis = next(t for t in analyze(outcomes, Config()).tests)
        body = issue.render(analysis)
        if len(analysis.signatures) > 1:
            assert "more than one bug" in body

    def test_credits_the_tool_and_shows_how_to_reproduce(self) -> None:
        analysis, _ = flaky_analysis()
        body = issue.render(analysis)
        assert "flaky-test-detective" in body
        assert "flaky history" in body


class TestJira:
    def test_uses_wiki_markup_not_markdown(self) -> None:
        """Jira will render Markdown literally, which looks broken."""
        analysis, _ = flaky_analysis()
        body = issue.render(analysis, fmt="jira")
        assert body.startswith("h2. ")
        assert "|| Field || Value ||" in body
        assert "## " not in body

    def test_includes_a_severity(self) -> None:
        analysis, _ = flaky_analysis()
        assert "Severity" in issue.render(analysis, fmt="jira")

    def test_uses_a_code_block(self) -> None:
        analysis, _ = flaky_analysis()
        assert "{code}" in issue.render(analysis, fmt="jira")


class TestSlack:
    def test_emits_valid_block_kit(self) -> None:
        analysis, _ = flaky_analysis()
        payload = json.loads(issue.render(analysis, fmt="slack"))
        assert "text" in payload
        assert payload["blocks"][0]["type"] == "header"
        for block in payload["blocks"]:
            assert block["type"] in {"header", "section", "context", "divider"}

    def test_header_stays_within_slack_limits(self) -> None:
        """Slack rejects a plain_text header over 150 characters."""
        analysis, _ = flaky_analysis()
        payload = json.loads(issue.render(analysis, fmt="slack"))
        assert len(payload["blocks"][0]["text"]["text"]) <= 150

    def test_fields_stay_within_slack_limits(self) -> None:
        """A section may hold at most ten fields."""
        analysis, _ = order_dependent_analysis()
        payload = json.loads(issue.render(analysis, fmt="slack"))
        for block in payload["blocks"]:
            if "fields" in block:
                assert len(block["fields"]) <= 10

    def test_breaks_are_marked_more_urgently_than_flakes(self) -> None:
        flaky, _ = flaky_analysis()
        broken_outcomes = sequence("t.py::test_b", "F" * 12, commits=["c1"] * 12)
        broken = next(t for t in analyze(broken_outcomes, Config()).tests)

        assert "🟠" in json.loads(issue.render(flaky, fmt="slack"))["text"]
        assert "🔴" in json.loads(issue.render(broken, fmt="slack"))["text"]

    def test_carries_the_evidence(self) -> None:
        analysis, _ = flaky_analysis()
        payload = json.loads(issue.render(analysis, fmt="slack"))
        text = json.dumps(payload)
        assert "Evidence" in text


class TestJson:
    def test_is_parseable_and_complete(self) -> None:
        analysis, outcomes = flaky_analysis()
        payload = json.loads(
            issue.render(analysis, blame("tests/test_a.py::test_x", outcomes), fmt="json")
        )
        for key in ("title", "test_id", "verdict", "score", "evidence", "blame"):
            assert key in payload

    def test_records_the_polluter(self) -> None:
        analysis, _ = order_dependent_analysis()
        payload = json.loads(issue.render(analysis, fmt="json"))
        assert payload["polluter"] == "tests/t.py::polluter"


class TestFormats:
    @pytest.mark.parametrize("fmt", issue.FORMATS)
    def test_every_format_produces_output(self, fmt: str) -> None:
        analysis, _ = flaky_analysis()
        assert issue.render(analysis, fmt=fmt).strip()

    def test_unknown_format_is_an_error(self) -> None:
        analysis, _ = flaky_analysis()
        with pytest.raises(ValueError, match="Unknown format"):
            issue.render(analysis, fmt="confluence")

    def test_github_is_markdown(self) -> None:
        analysis, _ = flaky_analysis()
        assert issue.render(analysis, fmt="github") == issue.render(analysis, fmt="markdown")
