"""Tests for branch comparison: did this change introduce flakiness, or inherit it?

The cases that matter are the ones where a careless implementation blames the wrong
person:

- a flake that was already there must not block a merge;
- a test failing identically on both sides must be reported as unchanged, not as
  something needing investigation;
- "stable before, failed once now" must not be attributed to the change, because with a
  small baseline that happens constantly;
- a short baseline must produce "cannot tell", not a verdict.

The statistical helpers are tested against values that can be checked by hand, because
the whole gate rests on them.
"""

from __future__ import annotations

import json

import pytest

from flaky_detective.analysis import analyze
from flaky_detective.analysis import compare as compare_reports
from flaky_detective.analysis.comparison import MIN_HEAD_RUNS
from flaky_detective.config import Config
from flaky_detective.models import Change, ComparisonReport
from flaky_detective.report import comparison as comparison_report

from conftest import sequence


def report_for(*specs: tuple[str, str, list[str] | None]):
    outcomes = []
    for test_id, pattern, commits in specs:
        outcomes += sequence(test_id, pattern, commits=commits)
    return analyze(outcomes, Config())


def compare(baseline_specs, head_specs) -> ComparisonReport:
    return compare_reports(
        report_for(*baseline_specs),
        report_for(*head_specs),
        baseline_label="main",
        head_label="pr",
    )


def one(result: ComparisonReport, fragment: str):
    matching = [e for e in result.entries if fragment in e.test_id]
    assert len(matching) == 1, f"expected one entry matching {fragment!r}, got {len(matching)}"
    return matching[0]


COMMITS_20 = ["c1"] * 20
COMMITS_40 = ["c1"] * 40


class TestIntroduced:
    def test_stable_then_flaky_is_a_new_flake(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.NEW_FLAKE
        assert entry.blocks
        assert entry.confidence == "high"
        assert result.new_flakes and not result.clean

    def test_new_flake_explanation_names_the_proof(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        explanation = one(result, "t.py::a").explanation
        assert "same commit" in explanation
        assert "identical" in explanation

    def test_passing_then_consistently_failing_is_a_new_break(self) -> None:
        """Breakage, not flakiness. Re-running will not help and the wording says so."""
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", "F" * 20, COMMITS_20)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.NEW_BREAK
        assert entry.blocks
        assert "re-running will not help" in entry.explanation.lower()

    def test_a_new_test_arriving_flaky_counts_as_introduced(self) -> None:
        result = compare(
            [("t.py::old", "." * 40, COMMITS_40)],
            [("t.py::old", "." * 40, COMMITS_40), ("t.py::new", ".F" * 20, COMMITS_40)],
        )
        entry = one(result, "t.py::new")
        assert entry.change is Change.NEW_FLAKE
        assert entry.baseline is None
        assert entry.baseline_summary == "not on the baseline"

    def test_a_new_test_arriving_broken_is_a_new_break(self) -> None:
        result = compare(
            [("t.py::old", "." * 40, COMMITS_40)],
            [("t.py::old", "." * 40, COMMITS_40), ("t.py::new", "F" * 20, COMMITS_20)],
        )
        assert one(result, "t.py::new").change is Change.NEW_BREAK


class TestNeverBlamingTheWrongChange:
    def test_a_pre_existing_flake_does_not_block(self) -> None:
        """The rule that decides whether anyone keeps the gate switched on."""
        result = compare(
            [("t.py::a", ".F" * 20, COMMITS_40)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.KNOWN_FLAKE
        assert not entry.blocks
        assert result.clean
        assert "pre-existing" in entry.explanation.lower()

    def test_identical_failures_on_both_sides_is_unchanged_not_unproven(self) -> None:
        """A test broken before and after did not change.

        Reporting that as "cannot attribute" would put it in a list of things to
        investigate, which is noise on every pull request touching the repository.
        """
        result = compare(
            [("t.py::broken", "F" * 20, COMMITS_20)],
            [("t.py::broken", "F" * 20, COMMITS_20)],
        )
        entry = one(result, "t.py::broken")
        assert entry.change is Change.UNCHANGED
        assert not entry.blocks
        assert "nothing was introduced" in entry.explanation

    def test_getting_better_is_never_blamed(self) -> None:
        result = compare(
            [("t.py::a", "FF." * 14, ["c1"] * 42)],
            [("t.py::a", "F" + "." * 40, ["c1"] * 41)],
        )
        assert one(result, "t.py::a").change is not Change.NEW_FLAKE

    def test_one_extra_failure_is_not_attributed(self) -> None:
        """Stable baseline, a single failure now. That happens; it is not a finding."""
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", "." * 19 + "F", COMMITS_20)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is not Change.NEW_FLAKE
        assert not entry.blocks

    def test_a_short_baseline_cannot_establish_stability(self) -> None:
        result = compare(
            [("t.py::a", "...", ["c1"] * 3)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.UNPROVEN
        assert not entry.blocks
        assert "not enough to say the test was stable" in entry.explanation

    def test_a_short_head_window_is_not_enough_to_attribute(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", ".F", ["c1", "c1"])],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.UNPROVEN
        assert str(MIN_HEAD_RUNS) in entry.explanation

    def test_report_flags_an_insufficient_baseline(self) -> None:
        result = compare(
            [("t.py::a", "....", ["c1"] * 4)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        assert not result.enough_baseline


class TestImproved:
    def test_flaky_then_clean_is_reported_as_improved(self) -> None:
        result = compare(
            [("t.py::a", ".F" * 20, COMMITS_40)],
            [("t.py::a", "." * 40, COMMITS_40)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.IMPROVED
        assert not entry.blocks
        assert "flaky verify" in entry.explanation

    def test_stable_then_stable_is_unchanged(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", "." * 40, COMMITS_40)],
        )
        assert one(result, "t.py::a").change is Change.UNCHANGED
        assert result.clean


class TestUnprovenIsDescribedUsefully:
    def test_proven_flaky_here_but_baseline_too_small_says_so(self) -> None:
        """Two different "cannot tell"s must not share one sentence.

        Demonstrably flaky now with an unresolvable baseline is worth attention; a
        slightly higher failure count is not.
        """
        result = compare(
            [("t.py::a", "." * 20, COMMITS_20)],
            [("t.py::a", "...F...F...F...F....", COMMITS_20)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.UNPROVEN
        assert "direct evidence" in entry.explanation
        assert "Hunt more iterations" in entry.explanation

    def test_more_baseline_history_resolves_an_unproven_case(self) -> None:
        """The remedy the explanation recommends has to actually work.

        Same head runs both times; only the baseline grows. A wider baseline tightens the
        bound, and the same behaviour crosses from unattributable to attributed.
        """
        # Four failures in twenty. Against a 20-run baseline that is p=0.30; against a
        # 120-run baseline the bound tightens from 13.9% to 2.5% and the same behaviour
        # becomes p=0.001.
        head = [("t.py::a", "....F....F....F....F", COMMITS_20)]

        thin = compare([("t.py::a", "." * 20, COMMITS_20)], head)
        wide = compare([("t.py::a", "." * 120, ["c1"] * 120)], head)

        assert one(thin, "t.py::a").change is Change.UNPROVEN
        assert one(wide, "t.py::a").change is Change.NEW_FLAKE
        assert one(wide, "t.py::a").baseline_rate_bound < one(thin, "t.py::a").baseline_rate_bound


class TestReportShape:
    def test_blocking_entries_sort_first(self) -> None:
        result = compare(
            [
                ("t.py::known", ".F" * 20, COMMITS_40),
                ("t.py::clean", "." * 40, COMMITS_40),
            ],
            [
                ("t.py::known", ".F" * 20, COMMITS_40),
                ("t.py::clean", ".F" * 20, COMMITS_40),
            ],
        )
        assert result.entries[0].test_id == "t.py::clean"
        assert result.entries[0].blocks

    def test_ordering_is_deterministic(self) -> None:
        specs_baseline = [(f"t.py::t{i}", "." * 40, COMMITS_40) for i in range(6)]
        specs_head = [(f"t.py::t{i}", ".F" * 20, COMMITS_40) for i in range(6)]
        first = compare(specs_baseline, specs_head)
        second = compare(specs_baseline, specs_head)
        assert [e.test_id for e in first.entries] == [e.test_id for e in second.entries]

    def test_counts_and_labels_are_carried(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", ".F" * 20, COMMITS_40)],
        )
        assert result.baseline_label == "main"
        assert result.head_label == "pr"
        assert result.baseline_runs == 40
        assert result.head_runs == 40
        assert result.baseline_tests == 1
        assert result.head_tests == 1

    def test_confidence_is_none_for_uninteresting_entries(self) -> None:
        result = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", "." * 40, COMMITS_40)],
        )
        assert one(result, "t.py::a").confidence == "none"

    def test_flip_only_evidence_is_reported_as_weaker(self) -> None:
        """No commit SHAs means no proof, and the confidence has to say so."""
        result = compare(
            [("t.py::a", "." * 40, None)],
            [("t.py::a", ".F" * 20, None)],
        )
        entry = one(result, "t.py::a")
        assert entry.change is Change.NEW_FLAKE
        assert entry.confidence == "moderate"
        assert "weaker signal" in entry.explanation


class TestRendering:
    @pytest.fixture
    def result(self) -> ComparisonReport:
        return compare(
            [
                ("t.py::introduced", "." * 40, COMMITS_40),
                ("t.py::known", ".F" * 20, COMMITS_40),
                ("t.py::fixed", ".F" * 20, COMMITS_40),
                ("t.py::brokeit", "." * 40, COMMITS_40),
            ],
            [
                ("t.py::introduced", ".F" * 20, COMMITS_40),
                ("t.py::known", ".F" * 20, COMMITS_40),
                ("t.py::fixed", "." * 40, COMMITS_40),
                ("t.py::brokeit", "F" * 20, COMMITS_20),
            ],
        )

    def test_console_renders(self, result: ComparisonReport) -> None:
        import io

        from rich.console import Console

        console = Console(file=io.StringIO(), width=140)
        comparison_report.render_console(result, console)
        text = console.file.getvalue()  # type: ignore[union-attr]
        assert "introduced by this change" in text
        assert "do not merge" in text.lower()
        assert "Pre-existing flakes" in text

    def test_markdown_separates_introduced_from_inherited(self, result: ComparisonReport) -> None:
        rendered = comparison_report.render_markdown(result)
        assert "Flakiness introduced here" in rendered
        assert "Breakage introduced here" in rendered
        assert "not blocking" in rendered
        # The pre-existing flake must not appear in a blocking section.
        introduced_section = rendered.split("Flakiness introduced here")[1].split("###")[0]
        assert "t.py::known" not in introduced_section

    def test_markdown_offers_the_follow_up_commands(self, result: ComparisonReport) -> None:
        rendered = comparison_report.render_markdown(result)
        assert "flaky history" in rendered
        assert "flaky blame" in rendered
        assert "flaky issue" in rendered

    def test_clean_markdown_says_so_plainly(self) -> None:
        clean = compare(
            [("t.py::a", "." * 40, COMMITS_40)],
            [("t.py::a", "." * 40, COMMITS_40)],
        )
        rendered = comparison_report.render_markdown(clean)
        assert "No flakiness or breakage introduced" in rendered

    def test_json_carries_both_sides_and_the_probability(self, result: ComparisonReport) -> None:
        payload = json.loads(comparison_report.render_json(result))
        assert payload["summary"]["new_flakes"] == 1
        assert payload["summary"]["new_breaks"] == 1
        assert payload["summary"]["known_flakes"] == 1
        assert payload["summary"]["improved"] == 1
        assert payload["summary"]["blocking"] == 2

        introduced = next(e for e in payload["entries"] if e["test_id"] == "t.py::introduced")
        assert introduced["blocks"] is True
        assert introduced["baseline"]["failures"] == 0
        assert introduced["head"]["failures"] == 20
        assert introduced["probability"] < 0.05
        assert 0.0 < introduced["baseline_rate_bound"] < 0.2

    def test_json_records_a_missing_baseline_as_null(self) -> None:
        result = compare(
            [("t.py::old", "." * 40, COMMITS_40)],
            [("t.py::old", "." * 40, COMMITS_40), ("t.py::new", ".F" * 20, COMMITS_40)],
        )
        payload = json.loads(comparison_report.render_json(result))
        added = next(e for e in payload["entries"] if e["test_id"] == "t.py::new")
        assert added["baseline"] is None

    def test_unknown_format_is_rejected(self, result: ComparisonReport) -> None:
        with pytest.raises(ValueError, match="Unknown format"):
            comparison_report.render(result, "yaml")
