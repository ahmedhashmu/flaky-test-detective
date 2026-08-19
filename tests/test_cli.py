"""CLI surface and exit codes.

Exit codes are the contract with CI, so they get asserted explicitly rather than
inferred from output text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flaky_detective.cli import EXIT_FLAKY, EXIT_OK, EXIT_REGRESSION, EXIT_USAGE, app

from conftest import FIXTURES

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "history.db"


def invoke(*args: str):
    return runner.invoke(app, list(args))


def seed(db: Path, *, iterations: int = 6, commit: str = "c1", uid_prefix: str = "run") -> None:
    """Populate a database with a genuinely flaky history.

    Built through the real storage layer rather than the CLI, so a CLI test failure
    points at the CLI.

    `uid_prefix` matters for the merge tests. Run ids are content hashes, so two
    databases seeded with the same prefix hold the *same* runs and merging them is
    correctly a no-op. Distinct prefixes model what two real machines produce.
    """
    from flaky_detective.models import Status, TestOutcome, TestRun
    from flaky_detective.storage import Storage

    with Storage(db) as store:
        for index in range(iterations):
            flaky_status = Status.FAILED if index % 2 else Status.PASSED
            outcomes = (
                TestOutcome(
                    test_id="tests/test_x.py::test_flaky",
                    name="test_flaky",
                    status=flaky_status,
                    message="TimeoutError: timed out after 30s" if index % 2 else None,
                    signature="TimeoutError: timed out after <DURATION>" if index % 2 else None,
                    position=0,
                ),
                TestOutcome(
                    test_id="tests/test_x.py::test_stable",
                    name="test_stable",
                    status=Status.PASSED,
                    position=1,
                ),
            )
            store.add_run(
                TestRun(
                    run_uid=f"{uid_prefix}-{index}",
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                    outcomes=outcomes,
                    commit_sha=commit,
                    branch="main",
                    runner="pytest",
                )
            )


class TestTopLevel:
    def test_help(self) -> None:
        result = invoke("--help")
        assert result.exit_code == EXIT_OK
        for command in ("ingest", "hunt", "analyze", "triage", "quarantine"):
            assert command in result.output

    def test_version(self) -> None:
        result = invoke("version")
        assert result.exit_code == EXIT_OK
        assert "flaky-test-detective" in result.output

    @pytest.mark.parametrize(
        "command",
        ["ingest", "hunt", "analyze", "report", "triage", "history", "stats", "quarantine"],
    )
    def test_every_command_has_help(self, command: str) -> None:
        """--help has to stand alone; nobody reads the README first."""
        result = invoke(command, "--help")
        assert result.exit_code == EXIT_OK


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run inside an empty directory.

    Commands that write .flaky.toml or .flaky-quarantine.json resolve them
    relative to the working directory, so these tests must not run in the repo.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestInit:
    def test_writes_config_and_database(self, workdir: Path) -> None:
        result = invoke("init")
        assert result.exit_code == EXIT_OK
        assert (workdir / ".flaky.toml").is_file()

    def test_refuses_to_overwrite(self, workdir: Path) -> None:
        (workdir / ".flaky.toml").write_text("# mine\n")
        assert invoke("init").exit_code == EXIT_USAGE

    def test_force_overwrites(self, workdir: Path) -> None:
        (workdir / ".flaky.toml").write_text("# mine\n")
        assert invoke("init", "--force").exit_code == EXIT_OK
        assert "# mine" not in (workdir / ".flaky.toml").read_text()


class TestIngest:
    def test_stores_a_report(self, db: Path) -> None:
        result = invoke("ingest", str(FIXTURES / "pytest.xml"), "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "Added 1 runs" in result.output

    def test_second_ingest_is_a_no_op(self, db: Path) -> None:
        invoke("ingest", str(FIXTURES / "pytest.xml"), "--db", str(db))
        result = invoke("ingest", str(FIXTURES / "pytest.xml"), "--db", str(db))
        assert "Added 0 runs" in result.output
        assert "skipped 1" in result.output

    def test_directory_ingest_skips_bad_files_and_continues(self, db: Path) -> None:
        """The fixtures directory deliberately contains unusable reports."""
        result = invoke("ingest", str(FIXTURES), "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "Added 6 runs" in result.output

    def test_reports_why_a_file_was_skipped(self, db: Path) -> None:
        result = invoke("ingest", str(FIXTURES / "entity.xml"), "--db", str(db))
        assert "DOCTYPE or ENTITY" in result.output

    def test_no_matching_files(self, db: Path, tmp_path: Path) -> None:
        result = invoke("ingest", str(tmp_path / "nothing"), "--db", str(db))
        assert result.exit_code == EXIT_USAGE

    def test_explicit_commit_is_recorded(self, db: Path) -> None:
        result = invoke(
            "ingest", str(FIXTURES / "pytest.xml"), "--db", str(db), "--commit", "abc123def456"
        )
        assert "abc123def456" in result.output


class TestAnalyze:
    def test_empty_database_explains_what_to_do(self, db: Path) -> None:
        result = invoke("analyze", "--db", str(db))
        assert result.exit_code == EXIT_USAGE
        assert "flaky hunt" in result.output

    def test_ranks_the_flaky_test(self, db: Path) -> None:
        seed(db)
        result = invoke("analyze", "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "test_flaky" in result.output
        assert "flaky" in result.output

    def test_hides_stable_tests_by_default(self, db: Path) -> None:
        seed(db)
        assert "test_stable" not in invoke("analyze", "--db", str(db)).output

    def test_show_stable(self, db: Path) -> None:
        seed(db)
        assert "test_stable" in invoke("analyze", "--db", str(db), "--show-stable").output

    def test_threshold_can_suppress_a_verdict(self, db: Path) -> None:
        seed(db)
        result = invoke("analyze", "--db", str(db), "--threshold", "0.99")
        assert "No flaky tests" in result.output

    def test_filters_that_match_nothing(self, db: Path) -> None:
        seed(db)
        result = invoke("analyze", "--db", str(db), "--branch", "absent")
        assert result.exit_code == EXIT_USAGE


class TestExitCodes:
    def test_default_never_fails_the_shell(self, db: Path) -> None:
        seed(db)
        assert invoke("analyze", "--db", str(db)).exit_code == EXIT_OK

    def test_fail_on_flaky(self, db: Path) -> None:
        seed(db)
        result = invoke("analyze", "--db", str(db), "--fail-on", "flaky")
        assert result.exit_code == EXIT_FLAKY

    def test_regression_outranks_flaky(self, db: Path, tmp_path: Path) -> None:
        """A real break must be reported as more urgent than a flake."""
        from flaky_detective.models import Status, TestOutcome, TestRun
        from flaky_detective.storage import Storage

        seed(db)
        with Storage(db) as store:
            for index in range(4):
                store.add_run(
                    TestRun(
                        run_uid=f"reg-{index}",
                        started_at=f"2026-09-{index + 1:02d}T00:00:00+00:00",
                        outcomes=(
                            TestOutcome(
                                test_id="tests/test_x.py::test_broke",
                                name="test_broke",
                                status=Status.PASSED if index == 0 else Status.FAILED,
                                message=None if index == 0 else "AssertionError: no",
                                position=0,
                            ),
                        ),
                        commit_sha="c1" if index == 0 else "c2",
                    )
                )

        result = invoke("analyze", "--db", str(db), "--fail-on", "flaky")
        assert result.exit_code == EXIT_REGRESSION

    def test_invalid_fail_on(self, db: Path) -> None:
        seed(db)
        result = invoke("analyze", "--db", str(db), "--fail-on", "sometimes")
        assert result.exit_code == EXIT_USAGE


class TestReport:
    def test_markdown(self, db: Path) -> None:
        seed(db)
        result = invoke("report", "--db", str(db), "--format", "md")
        assert result.exit_code == EXIT_OK
        assert "## Flaky test report" in result.output
        assert "| Score |" in result.output

    def test_json_is_parseable_and_versioned(self, db: Path) -> None:
        seed(db)
        result = invoke("report", "--db", str(db), "--format", "json")
        payload = json.loads(result.output)
        assert payload["schema_version"] == 1
        assert payload["summary"]["flaky"] == 1

    def test_json_carries_the_counts_behind_each_score(self, db: Path) -> None:
        """Every derived number must be checkable by a consumer."""
        seed(db)
        payload = json.loads(invoke("report", "--db", str(db), "--format", "json").output)
        evidence = payload["tests"][0]["evidence"]
        for key in ("runs", "passes", "failures", "flips", "divergent_commits", "confidence"):
            assert key in evidence

    def test_html_is_self_contained(self, db: Path) -> None:
        """A CI artifact opened offline must still render."""
        seed(db)
        output = invoke("report", "--db", str(db), "--format", "html").output
        assert output.startswith("<!DOCTYPE html>")
        assert "<style>" in output
        assert 'src="http' not in output
        assert "cdn" not in output.lower()

    def test_writes_to_a_file(self, db: Path, tmp_path: Path) -> None:
        seed(db)
        target = tmp_path / "nested" / "report.md"
        result = invoke("report", "--db", str(db), "-o", str(target))
        assert result.exit_code == EXIT_OK
        assert target.is_file()

    def test_unknown_format(self, db: Path) -> None:
        seed(db)
        assert invoke("report", "--db", str(db), "-f", "pdf").exit_code == EXIT_USAGE


class TestTriage:
    @pytest.fixture
    def report_xml(self, tmp_path: Path) -> Path:
        """A run where the known flake fails and so does a brand-new test."""
        path = tmp_path / "run.xml"
        path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites name="pytest tests">'
            '<testsuite name="pytest" tests="3" failures="2" errors="0" skipped="0"'
            ' timestamp="2026-09-01T00:00:00+00:00">'
            '<testcase classname="tests.test_x" name="test_flaky">'
            '<failure message="TimeoutError: timed out after 30s">trace</failure>'
            "</testcase>"
            '<testcase classname="tests.test_x" name="test_brand_new">'
            '<failure message="AssertionError: genuinely broken">trace</failure>'
            "</testcase>"
            '<testcase classname="tests.test_x" name="test_stable" />'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        return path

    def test_separates_known_flakes_from_new_failures(self, db: Path, report_xml: Path) -> None:
        seed(db)
        result = invoke("triage", str(report_xml), "--db", str(db))
        assert "test_brand_new" in result.output
        assert "New failures" in result.output
        assert "Known flakes" in result.output

    def test_new_failure_fails_the_build(self, db: Path, report_xml: Path) -> None:
        seed(db)
        assert invoke("triage", str(report_xml), "--db", str(db)).exit_code == EXIT_REGRESSION

    def test_all_known_flakes_passes_the_build(self, db: Path, tmp_path: Path) -> None:
        """The whole point: a red build of only known flakes should not block."""
        seed(db)
        path = tmp_path / "flakes-only.xml"
        path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<testsuites name="pytest tests">'
            '<testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0"'
            ' timestamp="2026-09-01T00:00:00+00:00">'
            '<testcase classname="tests.test_x" name="test_flaky">'
            '<failure message="TimeoutError: timed out after 30s">trace</failure>'
            "</testcase>"
            '<testcase classname="tests.test_x" name="test_stable" />'
            "</testsuite></testsuites>",
            encoding="utf-8",
        )
        result = invoke("triage", str(path), "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "known flakes" in result.output.lower()

    def test_fail_on_none_always_succeeds(self, db: Path, report_xml: Path) -> None:
        seed(db)
        result = invoke("triage", str(report_xml), "--db", str(db), "--fail-on", "none")
        assert result.exit_code == EXIT_OK

    def test_json_output(self, db: Path, report_xml: Path) -> None:
        seed(db)
        result = invoke("triage", str(report_xml), "--db", str(db), "-f", "json")
        payload = json.loads(result.output)
        assert payload["summary"]["known_flakes"] == 1
        assert payload["summary"]["new_failures"] == 1

    def test_does_not_store_the_run_by_default(self, db: Path, report_xml: Path) -> None:
        from flaky_detective.storage import Storage

        seed(db)
        invoke("triage", str(report_xml), "--db", str(db))
        with Storage(db) as store:
            assert store.run_count() == 6

    def test_ingest_flag_stores_the_run(self, db: Path, report_xml: Path) -> None:
        from flaky_detective.storage import Storage

        seed(db)
        invoke("triage", str(report_xml), "--db", str(db), "--ingest")
        with Storage(db) as store:
            assert store.run_count() == 7

    def test_unreadable_report(self, db: Path) -> None:
        seed(db)
        result = invoke("triage", str(FIXTURES / "truncated.xml"), "--db", str(db))
        assert result.exit_code == EXIT_USAGE


class TestHistory:
    def test_shows_a_timeline(self, db: Path) -> None:
        seed(db)
        result = invoke("history", "tests/test_x.py::test_flaky", "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "passed" in result.output
        assert "failed" in result.output

    def test_partial_match_resolves(self, db: Path) -> None:
        seed(db)
        assert invoke("history", "test_flaky", "--db", str(db)).exit_code == EXIT_OK

    def test_ambiguous_match_lists_candidates(self, db: Path) -> None:
        seed(db)
        result = invoke("history", "test_", "--db", str(db))
        assert result.exit_code == EXIT_USAGE
        assert "tests match" in result.output

    def test_unknown_test(self, db: Path) -> None:
        seed(db)
        assert invoke("history", "nope", "--db", str(db)).exit_code == EXIT_USAGE


class TestStats:
    def test_summary(self, db: Path) -> None:
        seed(db)
        result = invoke("stats", "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "runs" in result.output


class TestQuarantineCommands:
    def test_empty_list(self, db: Path, workdir: Path) -> None:
        result = invoke("quarantine", "list", "--db", str(db))
        assert "Nothing quarantined" in result.output

    def test_recommend_is_a_dry_run_by_default(self, db: Path, workdir: Path) -> None:
        seed(db)
        dry = invoke("quarantine", "recommend", "--db", str(db))
        assert "--apply" in dry.output
        assert not (workdir / ".flaky-quarantine.json").exists()

    def test_recommend_apply_writes_the_list(self, db: Path, workdir: Path) -> None:
        seed(db)
        result = invoke("quarantine", "recommend", "--db", str(db), "--apply")
        assert result.exit_code == EXIT_OK
        assert (workdir / ".flaky-quarantine.json").is_file()

    def test_add_list_remove(self, db: Path, workdir: Path) -> None:
        assert invoke("quarantine", "add", "t.py::test_a", "--db", str(db)).exit_code == EXIT_OK
        assert "t.py::test_a" in invoke("quarantine", "list", "--db", str(db)).output
        assert invoke("quarantine", "remove", "t.py::test_a", "--db", str(db)).exit_code == EXIT_OK
        assert "Nothing quarantined" in invoke("quarantine", "list", "--db", str(db)).output

    def test_remove_unknown(self, db: Path, workdir: Path) -> None:
        assert invoke("quarantine", "remove", "absent", "--db", str(db)).exit_code == EXIT_USAGE

    def test_export_produces_usable_python(self, db: Path, workdir: Path) -> None:
        invoke("quarantine", "add", "tests/test_a.py::test_x", "--db", str(db))
        result = invoke("quarantine", "export", "--format", "pytest-conftest", "--db", str(db))
        compile(result.output, "generated.py", "exec")

    def test_export_unknown_format(self, db: Path, workdir: Path) -> None:
        result = invoke("quarantine", "export", "--format", "yaml", "--db", str(db))
        assert result.exit_code == EXIT_USAGE

    def test_verify_with_nothing_expired(self, db: Path, workdir: Path) -> None:
        invoke("quarantine", "add", "t.py::test_a", "--db", str(db), "--days", "30")
        result = invoke("quarantine", "verify", "--db", str(db))
        assert "No expired entries" in result.output


class TestHuntUsageErrors:
    def test_no_command(self, db: Path) -> None:
        result = invoke("hunt", "--db", str(db))
        assert result.exit_code == EXIT_USAGE
        assert "No command given" in result.output

    def test_single_iteration_is_rejected(self, db: Path) -> None:
        """One run cannot show a test behaving two ways."""
        result = invoke("hunt", "-n", "1", "--db", str(db), "--", "echo", "hi")
        assert result.exit_code == EXIT_USAGE
        assert "at least 2" in result.output

    def test_unrecognized_runner_needs_a_report_path(self, db: Path) -> None:
        result = invoke("hunt", "-n", "3", "--db", str(db), "--", "echo", "hi")
        assert result.exit_code == EXIT_USAGE
        assert "--report-path" in result.output


class TestBlameCommand:
    def test_reports_when_no_commit_can_be_blamed(self, db: Path) -> None:
        """seed() puts every run on one commit, so the honest answer is that the
        flakiness predates the recorded window."""
        seed(db)
        result = invoke("blame", "tests/test_x.py::test_flaky", "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "predates_history" in result.output or "No commit can be blamed" in result.output

    def test_names_the_introducing_commit(self, db: Path) -> None:
        from flaky_detective.models import Status, TestOutcome, TestRun
        from flaky_detective.storage import Storage

        with Storage(db) as store:
            # Two clean commits with two runs each, then divergence at c3.
            plan = [
                ("c1", Status.PASSED),
                ("c1", Status.PASSED),
                ("c2", Status.PASSED),
                ("c2", Status.PASSED),
                ("c3", Status.PASSED),
                ("c3", Status.FAILED),
            ]
            for index, (commit, status) in enumerate(plan):
                store.add_run(
                    TestRun(
                        run_uid=f"b{index}",
                        started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                        outcomes=(
                            TestOutcome(
                                test_id="t.py::test_x",
                                name="test_x",
                                status=status,
                                message="boom" if status.is_failure else None,
                                position=0,
                            ),
                        ),
                        commit_sha=commit,
                    )
                )

        result = invoke("blame", "t.py::test_x", "--db", str(db))
        assert result.exit_code == EXIT_OK
        assert "c3" in result.output
        assert "c2" in result.output

    def test_unknown_test(self, db: Path) -> None:
        seed(db)
        assert invoke("blame", "nope", "--db", str(db)).exit_code == EXIT_USAGE

    def test_partial_match_resolves(self, db: Path) -> None:
        seed(db)
        assert invoke("blame", "test_flaky", "--db", str(db)).exit_code == EXIT_OK


class TestMergeCommand:
    def test_merges_and_reports_counts(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        seed(target, iterations=4, commit="c1", uid_prefix="a")
        seed(source, iterations=4, commit="c2", uid_prefix="b")

        result = invoke("merge", str(source), "--into", str(target))
        assert result.exit_code == EXIT_OK
        assert "Merged 4 runs" in result.output
        assert "8 runs" in result.output

    def test_second_merge_is_a_no_op(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        seed(target, iterations=3, commit="c1", uid_prefix="a")
        seed(source, iterations=3, commit="c2", uid_prefix="b")

        invoke("merge", str(source), "--into", str(target))
        result = invoke("merge", str(source), "--into", str(target))
        assert "Merged 0 runs" in result.output
        assert "Skipped 3 duplicates" in result.output

    def test_merges_every_database_in_a_directory(self, tmp_path: Path) -> None:
        """The sharded-CI case: download every shard's artifact, merge the folder."""
        shards = tmp_path / "shards"
        shards.mkdir()
        for index in range(3):
            seed(
                shards / f"shard{index}.db",
                iterations=2,
                commit=f"c{index}",
                uid_prefix=f"s{index}",
            )

        target = tmp_path / "combined.db"
        seed(target, iterations=0, commit="c9", uid_prefix="target")

        result = invoke("merge", str(shards), "--into", str(target))
        assert result.exit_code == EXIT_OK
        assert "from 3 sources" in result.output

    def test_no_databases_found(self, tmp_path: Path) -> None:
        result = invoke("merge", str(tmp_path / "nothing"), "--into", str(tmp_path / "a.db"))
        assert result.exit_code == EXIT_USAGE

    def test_unusable_source_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        seed(target, iterations=2)
        junk = tmp_path / "junk.db"
        junk.write_text("not sqlite")

        result = invoke("merge", str(junk), "--into", str(target))
        assert result.exit_code == EXIT_OK
        assert "skipped" in result.output.lower()


class TestBenchmarkCommand:
    def test_console_output(self) -> None:
        result = invoke("benchmark", "--seed", "7", "--runs", "10")
        assert result.exit_code == EXIT_OK
        assert "false alarm" in result.output
        assert "precision" in result.output

    def test_json_is_parseable(self) -> None:
        result = invoke("benchmark", "--seed", "7", "--runs", "10", "--format", "json")
        payload = json.loads(result.output)
        assert payload["schema_version"] == 1
        assert "false_alarm_rate" in payload["headline"]
        assert payload["setup"]["seed"] == 7

    def test_markdown_output(self) -> None:
        result = invoke("benchmark", "--seed", "7", "--runs", "10", "--format", "md")
        assert "### Measured accuracy" in result.output
        assert "| Label |" in result.output

    def test_reproducible_from_a_seed(self) -> None:
        """An accuracy figure nobody can re-derive is an anecdote."""
        first = invoke("benchmark", "--seed", "3", "--runs", "10", "-f", "json").output
        second = invoke("benchmark", "--seed", "3", "--runs", "10", "-f", "json").output
        assert json.loads(first) == json.loads(second)

    def test_sweep_over_runs(self) -> None:
        result = invoke("benchmark", "--sweep", "runs")
        assert result.exit_code == EXIT_OK
        assert "Runs recorded" in result.output

    def test_sweep_over_coverage(self) -> None:
        result = invoke("benchmark", "--sweep", "coverage")
        assert "Commit coverage" in result.output

    def test_unknown_sweep_axis(self) -> None:
        assert invoke("benchmark", "--sweep", "vibes").exit_code == EXIT_USAGE

    def test_unknown_format(self) -> None:
        assert invoke("benchmark", "-f", "pdf").exit_code == EXIT_USAGE

    def test_writes_to_a_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "bench.md"
        result = invoke("benchmark", "--seed", "7", "--runs", "10", "-f", "md", "-o", str(target))
        assert result.exit_code == EXIT_OK
        assert target.is_file()
        assert "Measured accuracy" in target.read_text()
