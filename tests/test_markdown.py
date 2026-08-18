"""Markdown output.

Destined for a pull-request comment, so the shape matters: valid tables, no
unescaped backticks breaking code spans, and short enough that someone reads it.
"""

from __future__ import annotations

import pytest

from flaky_detective.analysis import analyze, triage
from flaky_detective.config import Config
from flaky_detective.models import Status
from flaky_detective.report import markdown

from conftest import outcome, sequence


def flaky_report(tests: int = 1, runs: int = 12):
    outcomes = []
    for index in range(tests):
        outcomes += sequence(
            f"tests/test_a.py::test_flaky_{index}",
            (".F" * (runs // 2)),
            commits=["c1"] * runs,
            message="TimeoutError: timed out after 30s waiting for lock",
        )
    return analyze(outcomes, Config())


def table_rows(text: str) -> list[str]:
    """Every table row in the document, separators excluded."""
    return [
        line for line in text.splitlines() if line.startswith("|") and not set(line) <= set("|-: ")
    ]


def tables(text: str) -> list[list[str]]:
    """Split the document into separate tables.

    A report contains more than one table, and they legitimately have different
    column counts, so consistency has to be checked within each rather than across
    all of them.
    """
    found: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("|"):
            if not set(line) <= set("|-: "):
                current.append(line)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


class TestReport:
    def test_has_a_heading_and_a_count(self) -> None:
        output = markdown.render_report(flaky_report())
        assert output.startswith("## Flaky test report")
        assert "**1 flaky**" in output

    def test_table_columns_are_consistent(self) -> None:
        """A ragged Markdown table renders as garbage on GitHub."""
        output = markdown.render_report(flaky_report(tests=3))
        found = tables(output)
        assert found
        for table in found:
            widths = {row.count("|") for row in table}
            assert len(widths) == 1, f"ragged table: {table}"

    def test_every_flaky_test_gets_a_row(self) -> None:
        output = markdown.render_report(flaky_report(tests=3))
        rows = table_rows(output)
        assert sum(1 for row in rows if "test_flaky_" in row) == 3

    def test_test_ids_are_in_code_spans(self) -> None:
        output = markdown.render_report(flaky_report())
        assert "`tests/test_a.py::test_flaky_0`" in output

    def test_backticks_in_messages_are_neutralized(self) -> None:
        """A backtick in test output would otherwise break the code span."""
        outcomes = sequence(
            "t.py::test_x",
            ".F.F.F",
            commits=["c1"] * 6,
            message="AssertionError: expected `foo` got `bar`",
        )
        output = markdown.render_report(analyze(outcomes, Config()))
        assert "`foo`" not in output

    def test_limit_is_respected_and_disclosed(self) -> None:
        output = markdown.render_report(flaky_report(tests=10), limit=3)
        assert "_and 7 more_" in output

    def test_clean_report_says_so(self) -> None:
        output = markdown.render_report(analyze(sequence("t.py::test_x", "...."), Config()))
        assert "No flaky tests" in output

    def test_diagnosis_section_appears_with_a_real_cause(self) -> None:
        output = markdown.render_report(flaky_report())
        assert "### Diagnosis" in output
        assert "timeout" in output

    def test_remediation_is_included(self) -> None:
        assert "Wait on the condition" in markdown.render_report(flaky_report())

    def test_missing_commit_data_is_disclosed(self) -> None:
        """The report must not present flip-rate-only scores as equally sound."""
        output = markdown.render_report(analyze(sequence("t.py::test_x", ".F.F.F"), Config()))
        assert "Weak evidence" in output

    def test_thin_evidence_is_disclosed(self) -> None:
        outcomes = sequence("t.py::test_x", ".F.", commits=["c1"] * 3)
        output = markdown.render_report(analyze(outcomes, Config()))
        assert "fewer runs than the confidence threshold" in output

    def test_shared_signature_section(self) -> None:
        outcomes = sequence(
            "t.py::test_a", ".F.F", commits=["c1"] * 4, message="ConnectionRefused: db"
        )
        outcomes += sequence(
            "t.py::test_b", ".F.F", commits=["c1"] * 4, message="ConnectionRefused: db"
        )
        output = markdown.render_report(analyze(outcomes, Config()))
        assert "### Shared failure signatures" in output

    def test_ends_with_a_single_newline(self) -> None:
        output = markdown.render_report(flaky_report())
        assert output.endswith("\n")
        assert not output.endswith("\n\n")


class TestTriage:
    def build(self, *, failing: list[str], history_pattern: str = ".F.F.F.F"):
        history = analyze(
            sequence(
                "tests/test_a.py::test_known",
                history_pattern,
                commits=["c1"] * len(history_pattern),
                message="TimeoutError: timed out",
            ),
            Config(),
        )
        run = [
            outcome(
                test_id,
                Status.FAILED,
                run="fresh",
                position=index,
                message="AssertionError: boom",
            )
            for index, test_id in enumerate(failing)
        ]
        return triage(run, history, source="run.xml")

    def test_no_failures(self) -> None:
        result = self.build(failing=[])
        assert "No failures in this run" in markdown.render_triage(result)

    def test_all_known_flakes_is_stated_plainly(self) -> None:
        result = self.build(failing=["tests/test_a.py::test_known"])
        output = markdown.render_triage(result)
        assert "All 1 failure are known flakes" in output or "known flakes" in output
        assert "No new breakage" in output

    def test_new_failure_is_called_out(self) -> None:
        result = self.build(failing=["tests/test_a.py::test_brand_new"])
        output = markdown.render_triage(result)
        assert "needs attention" in output
        assert "### New failures" in output
        assert "`tests/test_a.py::test_brand_new`" in output

    def test_known_flakes_are_collapsed_behind_a_summary(self) -> None:
        """A PR comment listing thirty known flakes is a PR comment nobody reads."""
        result = self.build(
            failing=["tests/test_a.py::test_known", "tests/test_a.py::test_brand_new"]
        )
        output = markdown.render_triage(result)
        assert "<details>" in output
        assert "</details>" in output

    def test_singular_and_plural_agree(self) -> None:
        one = markdown.render_triage(self.build(failing=["tests/test_a.py::test_new_one"]))
        assert "1 failure needs attention" in one

        two = markdown.render_triage(
            self.build(failing=["tests/test_a.py::test_new_one", "tests/test_a.py::test_new_two"])
        )
        assert "2 failures need attention" in two

    @pytest.mark.parametrize("count", [1, 2, 5])
    def test_every_failure_is_listed(self, count: int) -> None:
        failing = [f"tests/test_a.py::test_new_{i}" for i in range(count)]
        output = markdown.render_triage(self.build(failing=failing))
        for test_id in failing:
            assert test_id in output
