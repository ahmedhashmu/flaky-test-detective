"""JUnit XML parsing across runner dialects.

Driven by the fixture files in tests/fixtures/. Two of those are captured from real
runners and the rest are written to documented output shapes; see
tests/fixtures/README.md for which is which, because it matters when a test here
fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flaky_detective.ingest import junit
from flaky_detective.ingest.junit import ParseError, build_test_id
from flaky_detective.models import Status


class TestPytestDialect:
    """Captured from pytest 9.1.1 with the default junit_family=xunit2."""

    @pytest.fixture
    def run(self, fixtures: Path):
        return junit.parse_file(fixtures / "pytest.xml")

    def test_detects_runner(self, run) -> None:
        assert run.runner == "pytest"

    def test_counts(self, run) -> None:
        assert (run.total, run.failed, run.skipped) == (9, 4, 1)

    def test_reconstructs_module_nodeid(self, run) -> None:
        """xunit2 drops the file attribute, so this is rebuilt from classname."""
        ids = [o.test_id for o in run.outcomes]
        assert "tests/test_sample.py::test_passes" in ids

    def test_reconstructs_class_nodeid(self, run) -> None:
        ids = [o.test_id for o in run.outcomes]
        assert "tests/test_sample.py::TestGrouped::test_inside_class_fails" in ids

    def test_keeps_parametrized_ids_distinct(self, run) -> None:
        ids = [o.test_id for o in run.outcomes]
        assert "tests/test_sample.py::test_parametrized[1]" in ids
        assert "tests/test_sample.py::test_parametrized[3]" in ids

    def test_reads_timestamp_from_the_report(self, run) -> None:
        assert run.started_at.startswith("2026-08-18T13:40:32")

    def test_assigns_positions_in_document_order(self, run) -> None:
        assert [o.position for o in run.outcomes] == list(range(9))

    def test_extracts_failure_message(self, run) -> None:
        failure = next(o for o in run.outcomes if o.test_id.endswith("test_raises_error"))
        assert failure.status is Status.FAILED
        assert failure.signature == "ConnectionRefusedError: connection refused to localhost:<PORT>"


class TestJestDialect:
    """Captured from jest 29.7.0 with jest-junit 16.0.0."""

    @pytest.fixture
    def run(self, fixtures: Path):
        return junit.parse_file(fixtures / "jest.xml")

    def test_detects_runner(self, run) -> None:
        assert run.runner == "jest"

    def test_counts(self, run) -> None:
        assert (run.total, run.failed, run.skipped) == (6, 3, 1)

    def test_does_not_double_the_describe_path(self, run) -> None:
        """jest-junit writes the same string into classname and name."""
        ids = [o.test_id for o in run.outcomes]
        assert "cart totals sums line items" in ids
        assert not any(o.test_id.count("cart totals") > 1 for o in run.outcomes)

    def test_reads_message_from_element_text(self, run) -> None:
        """jest's <failure> carries no message attribute, only body text."""
        failure = next(o for o in run.outcomes if "applies discount" in o.test_id)
        assert failure.message is not None
        assert failure.message.startswith("Error: expect(received)")

    def test_prefers_the_error_line_over_the_last_stack_frame(self, run) -> None:
        failure = next(o for o in run.outcomes if "pricing service" in o.test_id)
        assert failure.signature == "Error: connect ECONNREFUSED <IP>:<PORT>"

    def test_skipped_element_without_message(self, run) -> None:
        skipped = next(o for o in run.outcomes if o.status is Status.SKIPPED)
        assert skipped.message is None


class TestGoDialect:
    def test_detects_runner(self, fixtures: Path) -> None:
        assert junit.parse_file(fixtures / "go.xml").runner == "go"

    def test_ignores_the_constant_failed_message(self, fixtures: Path) -> None:
        """go-junit-report writes message="Failed" on every failure.

        Trusting it would give every Go failure the same signature and collapse
        unrelated bugs into one cluster.
        """
        run = junit.parse_file(fixtures / "go.xml")
        failures = [o for o in run.outcomes if o.status.is_failure]
        signatures = {o.signature for o in failures}
        assert "Failed" not in signatures
        assert len(signatures) == len(failures)

    def test_extracts_detail_from_element_text(self, fixtures: Path) -> None:
        run = junit.parse_file(fixtures / "go.xml")
        race = next(o for o in run.outcomes if "ConcurrentWrite" in o.test_id)
        assert race.signature is not None
        assert "race detected" in race.signature

    def test_walks_multiple_suites(self, fixtures: Path) -> None:
        run = junit.parse_file(fixtures / "go.xml")
        assert (run.total, run.failed, run.skipped) == (6, 2, 1)


class TestSurefireDialect:
    @pytest.fixture
    def run(self, fixtures: Path):
        return junit.parse_file(fixtures / "surefire.xml")

    def test_handles_testsuite_as_root(self, run) -> None:
        """Surefire has no <testsuites> wrapper."""
        assert run.total == 5

    def test_detects_runner(self, run) -> None:
        assert run.runner == "junit"

    def test_error_element_counts_as_failure(self, run) -> None:
        errored = next(o for o in run.outcomes if "PricingService" in o.test_id)
        assert errored.status is Status.ERROR
        assert errored.status.is_failure

    def test_flaky_failure_is_a_pass_marked_retried(self, run) -> None:
        """<flakyFailure> means the test failed then passed on retry."""
        retried = next(o for o in run.outcomes if o.retried)
        assert retried.status is Status.PASSED
        assert "reservesInventoryUnderLoad" in retried.test_id

    def test_retry_keeps_its_diagnostic_message(self, run) -> None:
        """The retry element holds the only description of what went wrong."""
        retried = next(o for o in run.outcomes if o.retried)
        assert retried.signature == "Timed out after <DURATION> waiting for inventory lock"


class TestNestedAndDotnetDialects:
    def test_nested_testsuites(self, fixtures: Path) -> None:
        run = junit.parse_file(fixtures / "gradle-nested.xml")
        assert (run.total, run.failed, run.skipped) == (4, 1, 1)

    def test_positions_span_nested_suites(self, fixtures: Path) -> None:
        run = junit.parse_file(fixtures / "gradle-nested.xml")
        assert [o.position for o in run.outcomes] == [0, 1, 2, 3]

    def test_dotnet_pascal_case_namespace(self, fixtures: Path) -> None:
        """.NET namespaces are PascalCase and fail the Java FQCN pattern."""
        assert junit.parse_file(fixtures / "trx.xml").runner == "dotnet"


class TestBadInput:
    """Bad input must produce a diagnostic, never an unhandled exception."""

    @pytest.mark.parametrize(
        ("name", "fragment"),
        [
            ("empty.xml", "empty"),
            ("truncated.xml", "malformed XML"),
            ("no-testcases.xml", "no <testcase>"),
            ("entity.xml", "DOCTYPE or ENTITY"),
        ],
    )
    def test_rejects_with_a_reason(self, fixtures: Path, name: str, fragment: str) -> None:
        with pytest.raises(ParseError, match=fragment):
            junit.parse_file(fixtures / name)

    def test_refuses_entity_expansion(self, fixtures: Path) -> None:
        """Reports arrive as CI artifacts, so entity expansion is a real risk."""
        with pytest.raises(ParseError, match="refusing to parse"):
            junit.parse_file(fixtures / "entity.xml")

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ParseError, match="not a file"):
            junit.parse_file(tmp_path / "absent.xml")


class TestRunIdentity:
    def test_same_file_gives_the_same_uid(self, fixtures: Path) -> None:
        """This is what makes ingest idempotent under CI retries."""
        first = junit.parse_file(fixtures / "pytest.xml")
        second = junit.parse_file(fixtures / "pytest.xml")
        assert first.run_uid == second.run_uid

    def test_different_content_gives_a_different_uid(self, fixtures: Path) -> None:
        assert (
            junit.parse_file(fixtures / "pytest.xml").run_uid
            != junit.parse_file(fixtures / "jest.xml").run_uid
        )

    def test_iteration_separates_identical_reports(self, fixtures: Path) -> None:
        """A deterministic suite writes identical XML every hunt iteration.

        Without the iteration in the hash, every iteration after the first would be
        discarded as a duplicate and a hunt would record one run.
        """
        first = junit.parse_file(fixtures / "pytest.xml", iteration=1)
        second = junit.parse_file(fixtures / "pytest.xml", iteration=2)
        assert first.run_uid != second.run_uid

    def test_metadata_is_attached(self, fixtures: Path) -> None:
        run = junit.parse_file(
            fixtures / "pytest.xml", commit_sha="abc123", branch="main", ci_run_id="42"
        )
        assert (run.commit_sha, run.branch, run.ci_run_id) == ("abc123", "main", "42")


class TestBuildTestId:
    @pytest.mark.parametrize(
        ("name", "classname", "file", "runner", "expected"),
        [
            ("test_a", "tests.test_mod", None, "pytest", "tests/test_mod.py::test_a"),
            (
                "test_a",
                "tests.test_mod.TestCls",
                None,
                "pytest",
                "tests/test_mod.py::TestCls::test_a",
            ),
            (
                "test_a",
                "tests.test_mod.TestOuter.TestInner",
                None,
                "pytest",
                "tests/test_mod.py::TestOuter::TestInner::test_a",
            ),
            (
                "test_a",
                "tests.test_mod",
                "tests/test_mod.py",
                "pytest",
                "tests/test_mod.py::test_a",
            ),
            ("TestFoo", "pkg/store", None, "go", "pkg/store::TestFoo"),
            ("a b c", "a b c", None, "jest", "a b c"),
            ("test_a", None, None, "unknown", "test_a"),
            ("test_a", None, "some/file.rb", "unknown", "some/file.rb::test_a"),
        ],
    )
    def test_cases(
        self, name: str, classname: str | None, file: str | None, runner: str, expected: str
    ) -> None:
        assert build_test_id(name, classname, file, runner) == expected
