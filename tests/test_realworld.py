"""Tests for scoring against real-world labels.

The scorer decides what the project's most quotable number means, so the cases that
matter here are the ones that could quietly inflate it:

- a label whose flake did not reproduce must not count as a miss, and must not count as
  a hit either;
- a labelled test that failed every single run must be reported as broken, and the
  scorer must record the refusal to call it flaky as a *success*, since that is the
  false alarm this tool exists not to raise;
- a detection absent from the dataset must not be silently counted as correct;
- categories the tool makes no claim about must be excluded from recall rather than
  dragging it down.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flaky_detective.benchmark import realworld
from flaky_detective.report import validation as validation_report


def row(
    test_id: str,
    verdict: str,
    *,
    runs: int = 10,
    passes: int = 5,
    failures: int = 5,
    divergent: int = 1,
    retries: int = 0,
    cause: str | None = None,
    polluter: str | None = None,
) -> dict:
    return {
        "test_id": test_id,
        "verdict": verdict,
        "score": 0.8,
        "evidence": {
            "runs": runs,
            "passes": passes,
            "failures": failures,
            "divergent_commits": divergent,
            "retries": retries,
        },
        "cause": {"category": cause} if cause else None,
        "order_dependence": {"likely_polluter": polluter} if polluter else None,
    }


def raw(
    tests: list[dict],
    labels: dict[str, str],
    *,
    repo: str = "acme/widget",
    runs: int = 10,
) -> dict:
    return {
        "repo": repo,
        "sha": "a" * 40,
        "iterations": runs,
        "collected": len(tests),
        "dataset_sha": "d" * 40,
        "labels": labels,
        "report": {
            "summary": {"runs": runs, "results": runs * len(tests)},
            "tests": tests,
        },
    }


class TestRecall:
    def test_detected_label_counts_as_a_hit(self) -> None:
        score = realworld.score_project(raw([row("t.py::a", "flaky")], {"t.py::a": "OD-Vic"}))
        assert score.reproduced == 1
        assert score.detected == 1
        assert score.recall == 1.0

    def test_a_label_that_never_varied_is_neither_hit_nor_miss(self) -> None:
        """The flake did not happen here, so there was nothing to find.

        Counting it as a miss would blame the detector for the weather. Counting it as
        a hit would be worse.
        """
        score = realworld.score_project(
            raw(
                [row("t.py::a", "stable", passes=10, failures=0, divergent=0)],
                {"t.py::a": "OD-Vic"},
            )
        )
        assert score.executed == 1
        assert score.reproduced == 0
        assert score.detected == 0
        assert score.not_reproducible_passed == 1
        assert score.recall == 0.0

    def test_missed_label_is_recorded_by_name(self) -> None:
        score = realworld.score_project(raw([row("t.py::a", "regression")], {"t.py::a": "NOD"}))
        assert score.reproduced == 1
        assert score.detected == 0
        assert score.misses and "t.py::a" in score.misses[0]
        assert "NOD" in score.misses[0]

    def test_a_label_for_a_test_that_did_not_run_is_ignored(self) -> None:
        score = realworld.score_project(raw([], {"t.py::gone": "OD-Vic"}))
        assert score.labelled == 1
        assert score.executed == 0
        assert score.reproduced == 0

    def test_retry_alone_counts_as_divergence(self) -> None:
        """A runner-recorded retry is the same proof by another route."""
        score = realworld.score_project(
            raw(
                [row("t.py::a", "flaky", divergent=0, retries=2)],
                {"t.py::a": "NOD"},
            )
        )
        assert score.reproduced == 1
        assert score.detected == 1


class TestNeverCryingWolf:
    def test_consistently_failing_label_withheld_is_a_success(self) -> None:
        """The most important row in the whole evaluation.

        The dataset says this test is flaky somewhere. Here it failed every run, so it
        is broken here, and reporting it as flaky would teach the user to re-run a real
        failure. Refusing is correct and is counted as such.
        """
        score = realworld.score_project(
            raw(
                [row("t.py::a", "broken", passes=0, failures=10, divergent=0)],
                {"t.py::a": "OD-Vic"},
            )
        )
        assert score.not_reproducible_failed == 1
        assert score.correctly_withheld == 1
        assert score.wrongly_called_flaky == 0

    def test_consistently_failing_label_called_flaky_is_counted_against_us(self) -> None:
        score = realworld.score_project(
            raw(
                [row("t.py::a", "flaky", passes=0, failures=10, divergent=0)],
                {"t.py::a": "OD-Vic"},
            )
        )
        assert score.wrongly_called_flaky == 1
        assert score.correctly_withheld == 0


class TestPrecision:
    def test_precision_is_measured_against_observed_divergence(self) -> None:
        """Not against the dataset, which is not exhaustive.

        One flagged test diverged, one did not. Precision is one half, regardless of
        what the dataset happens to list.
        """
        score = realworld.score_project(
            raw(
                [
                    row("t.py::a", "flaky", divergent=1),
                    row("t.py::b", "flaky", divergent=0, retries=0),
                ],
                {},
            )
        )
        assert score.flagged == 2
        assert score.flagged_with_divergence == 1
        assert score.precision == 0.5

    def test_flagged_without_divergence_is_listed_for_inspection(self) -> None:
        score = realworld.score_project(raw([row("t.py::b", "flaky", divergent=0)], {}))
        assert score.suspect == ("t.py::b",)

    def test_unlabelled_detection_is_reported_not_assumed_correct(self) -> None:
        score = realworld.score_project(raw([row("t.py::unlisted", "flaky", divergent=1)], {}))
        assert score.unlabelled_flagged == 1
        assert score.unlabelled_flagged_with_divergence == 1
        # It contributed nothing to recall, because there is no label to have found.
        assert score.reproduced == 0


class TestCategoryHandling:
    def test_non_idempotent_labels_are_excluded_from_recall(self) -> None:
        """NIO tests fail only when re-run inside one process. We cannot see that.

        Folding them into recall would hide the limitation behind an average.
        """
        score = realworld.score_project(
            raw(
                [row("t.py::a", "stable", passes=10, failures=0, divergent=0)],
                {"t.py::a": "NIO"},
            )
        )
        assert score.labelled == 1
        assert score.labelled_scored == 0
        assert score.reproduced == 0

    @pytest.mark.parametrize("category", ["ID", "UD"])
    def test_categories_the_tool_makes_no_claim_about_are_excluded(self, category: str) -> None:
        score = realworld.score_project(raw([row("t.py::a", "flaky")], {"t.py::a": category}))
        assert score.labelled_scored == 0

    def test_order_diagnosis_is_tracked_separately_from_detection(self) -> None:
        """Detecting flakiness and explaining it are different claims.

        Kept apart because the real-world run found the tool detecting order-dependent
        tests reliably while diagnosing almost none of them, and a combined number
        would have concealed that.
        """
        detected_only = realworld.score_project(
            raw([row("t.py::a", "flaky")], {"t.py::a": "OD-Vic"})
        )
        assert detected_only.order_labels_detected == 1
        assert detected_only.order_diagnosed == 0
        assert detected_only.order_polluter_named == 0

        explained = realworld.score_project(
            raw(
                [row("t.py::a", "flaky", cause="order_dependence", polluter="t.py::p")],
                {"t.py::a": "OD-Vic"},
            )
        )
        assert explained.order_diagnosed == 1
        assert explained.order_polluter_named == 1


class TestAggregation:
    def test_totals_sum_across_projects(self) -> None:
        result = realworld.score_all(
            [
                raw([row("t.py::a", "flaky")], {"t.py::a": "OD-Vic"}, repo="acme/one"),
                raw([row("t.py::b", "flaky")], {"t.py::b": "NOD"}, repo="acme/two"),
            ]
        )
        assert result.repositories == 2
        assert result.reproduced == 2
        assert result.detected == 2
        assert result.recall == 1.0
        assert result.category_totals() == {"NOD": (1, 1), "OD-Vic": (1, 1)}

    def test_projects_are_ordered_deterministically(self) -> None:
        result = realworld.score_all(
            [
                raw([], {}, repo="zeta/z"),
                raw([], {}, repo="alpha/a"),
            ]
        )
        assert [p.repo for p in result.projects] == ["alpha/a", "zeta/z"]

    def test_dataset_sha_reported_only_when_unambiguous(self) -> None:
        one = raw([], {}, repo="a/a")
        other = raw([], {}, repo="b/b")
        other["dataset_sha"] = "e" * 40
        assert realworld.score_all([one]).dataset_sha == "d" * 40
        assert realworld.score_all([one, other]).dataset_sha == ""

    def test_skipped_projects_are_carried_through(self) -> None:
        result = realworld.score_all(
            [raw([], {}, repo="a/a")], skipped=[{"repo": "x/y", "reason": "install failed"}]
        )
        assert len(result.skipped) == 1


class TestLoading:
    def test_reads_results_and_skips(self, tmp_path: Path) -> None:
        (tmp_path / "one.json").write_text(json.dumps(raw([], {}, repo="a/a")), encoding="utf-8")
        (tmp_path / "skipped.json").write_text(
            json.dumps([{"repo": "x/y", "reason": "nope"}]), encoding="utf-8"
        )
        results, skipped = realworld.load_results(tmp_path)
        assert len(results) == 1
        assert skipped == [{"repo": "x/y", "reason": "nope"}]

    def test_missing_directory_is_a_usage_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            realworld.load_results(tmp_path / "nope")

    def test_empty_directory_says_what_to_run(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match=r"run\.py"):
            realworld.load_results(tmp_path)


class TestRendering:
    @pytest.fixture
    def result(self) -> realworld.RealWorldResult:
        return realworld.score_all(
            [
                raw(
                    [
                        row("t.py::a", "flaky", cause="order_dependence", polluter="t.py::p"),
                        row("t.py::b", "broken", passes=0, failures=10, divergent=0),
                        row("t.py::c", "flaky", divergent=0),
                    ],
                    {"t.py::a": "OD-Vic", "t.py::b": "OD-Vic", "t.py::d": "NIO"},
                )
            ],
            skipped=[{"repo": "x/y", "reason": "install failed"}],
        )

    def test_markdown_carries_both_headline_numbers(
        self, result: realworld.RealWorldResult
    ) -> None:
        rendered = validation_report.render_markdown(result)
        assert "Recall" in rendered
        assert "Precision" in rendered
        assert "IDoFT" in rendered
        # The counts behind each rate, not just the rate.
        assert "1/1" in rendered or "1/2" in rendered

    def test_markdown_states_the_unflattering_rows(self, result: realworld.RealWorldResult) -> None:
        rendered = validation_report.render_markdown(result)
        assert "wrongly called flaky" in rendered.lower()
        assert "never varied" in rendered.lower()
        assert "skipped.json" in rendered or "could not be evaluated" in rendered

    def test_json_round_trips(self, result: realworld.RealWorldResult) -> None:
        payload = json.loads(validation_report.render_json(result))
        assert payload["dataset"] == "IDoFT"
        assert payload["repositories"] == 1
        assert payload["projects"][0]["repo"] == "acme/widget"
        assert "recall" in payload and "precision" in payload

    def test_console_renders_without_error(self, result: realworld.RealWorldResult) -> None:
        import io

        from rich.console import Console

        console = Console(file=io.StringIO(), width=200)
        validation_report.render_console(result, console)
        assert console.file.getvalue()  # type: ignore[union-attr]

    def test_unknown_format_is_rejected(self, result: realworld.RealWorldResult) -> None:
        with pytest.raises(ValueError, match="Unknown format"):
            validation_report.render(result, "yaml")
