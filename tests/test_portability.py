"""Cross-platform behaviour, asserted on every platform.

These tests exist because a green Windows CI run would not have caught any of them.

pytest replaces `sys.stdout` with a UTF-8 buffer and typer's `CliRunner` does the same,
so the encoding failures that make `flaky verify > verify.log` raise on a Windows console
are invisible from inside a test suite. Windows file locking is similarly invisible until
something tries to delete a file that is still open. And a case-insensitive filesystem
changes the answer to "are these the same file" without changing any code path.

So each test here reconstructs the platform condition explicitly -- a cp1252 stream, a
patched `os.name`, a genuinely aborted merge -- rather than waiting for the right runner.
That means they fail on the developer's machine when the behaviour regresses, which is
where a failure is cheap.
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from flaky_detective import reproduce
from flaky_detective.cli import _force_utf8_streams
from flaky_detective.models import Status
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.storage import Storage, StorageError, _same_file

BLOCK = "\u2588\u2591"
"""The characters in the fix-verification bars. Absent from cp1252."""

EMOJI = "\U0001f534"
"""The status marker in the Slack payload. Absent from every Windows codepage."""


def cp1252_stream() -> io.TextIOWrapper:
    """A text stream that encodes like a redirected Windows console."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


class TestStdoutEncoding:
    def test_cp1252_really_cannot_hold_our_output(self) -> None:
        """The control. Without it the rest of this class proves nothing."""
        stream = cp1252_stream()
        with pytest.raises(UnicodeEncodeError):
            stream.write(BLOCK)
            stream.flush()

        stream = cp1252_stream()
        with pytest.raises(UnicodeEncodeError):
            stream.write(EMOJI)
            stream.flush()

    def test_a_non_utf8_stdout_is_reconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """This is what makes `flaky verify > verify.log` work on Windows."""
        stream = cp1252_stream()
        monkeypatch.setattr(sys, "stdout", stream)

        _force_utf8_streams()

        assert (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
        sys.stdout.write(BLOCK + EMOJI)
        sys.stdout.flush()

    def test_newlines_are_pinned_to_lf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A report written on Windows must byte-match the same report on Linux."""
        raw = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", newline="\r\n"))

        _force_utf8_streams()
        sys.stdout.write("one\ntwo\n")
        sys.stdout.flush()

        assert b"\r\n" not in raw.getvalue()

    def test_an_already_utf8_stream_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The narrow gate that keeps this off the POSIX path.

        An earlier version reconfigured unconditionally at import time and detached the
        buffer pytest was writing to: the entire suite produced no output and exited
        non-zero with nothing to read. Doing nothing when there is nothing to fix is what
        prevents a repeat.
        """
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")
        calls: list[object] = []

        def refuse(**kwargs: object) -> None:
            calls.append(kwargs)
            raise AssertionError("an already-UTF-8 stream must not be reconfigured")

        monkeypatch.setattr(stream, "reconfigure", refuse, raising=False)
        monkeypatch.setattr(sys, "stdout", stream)

        _force_utf8_streams()
        assert not calls

    def test_a_stream_without_reconfigure_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Callers do substitute plain objects for stdout. That must not crash."""

        class Bare:
            encoding = "cp1252"

            def write(self, text: str) -> int:
                return len(text)

        monkeypatch.setattr(sys, "stdout", Bare())
        _force_utf8_streams()

    def test_a_stream_that_refuses_is_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = cp1252_stream()

        def refuse(**kwargs: object) -> None:
            raise ValueError("underlying buffer detached")

        monkeypatch.setattr(stream, "reconfigure", refuse, raising=False)
        monkeypatch.setattr(sys, "stdout", stream)
        _force_utf8_streams()


class TestSameFile:
    def test_identical_paths(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        target.write_bytes(b"")
        assert _same_file(target, target)

    def test_different_files(self, tmp_path: Path) -> None:
        left, right = tmp_path / "a.db", tmp_path / "b.db"
        left.write_bytes(b"")
        right.write_bytes(b"")
        assert not _same_file(left, right)

    def test_a_missing_path_does_not_raise(self, tmp_path: Path) -> None:
        """`samefile` needs both paths to exist; the fallback covers the rest."""
        assert not _same_file(tmp_path / "gone.db", tmp_path / "also-gone.db")

    def test_case_differences_on_a_case_insensitive_filesystem(self, tmp_path: Path) -> None:
        """macOS and Windows fold case; `resolve()` equality does not.

        Skipped where the filesystem is genuinely case-sensitive, because there the two
        names are two different files and saying so is correct.
        """
        lower = tmp_path / "history.db"
        lower.write_bytes(b"")
        upper = tmp_path / "HISTORY.DB"
        if not upper.exists():
            pytest.skip("case-sensitive filesystem: the two names are different files")

        assert _same_file(lower, upper)


class TestMergeReleasesTheSourceFile:
    """Windows cannot delete a file while a handle is open, so an aborted merge that
    left its source ATTACHed would turn a failed merge into an undeletable database."""

    @staticmethod
    def _seed(path: Path, uid: str) -> None:
        with Storage(path) as store:
            store.add_run(
                Run(
                    run_uid=uid,
                    started_at="2026-08-01T00:00:00+00:00",
                    runner="pytest",
                    outcomes=(
                        Outcome(test_id="t::one", name="one", status=Status.PASSED, position=0),
                    ),
                )
            )

    def test_a_failed_merge_still_detaches(self, tmp_path: Path) -> None:
        base, other = tmp_path / "a.db", tmp_path / "b.db"
        self._seed(base, "run-a")
        self._seed(other, "run-b")

        with Storage(base) as store:
            # Break the copy midway, with the implicit transaction open. SQLite refuses
            # DETACH inside a transaction, which is exactly the state the rollback in the
            # finally block exists to clear.
            store._conn.execute("DROP TABLE main.results")

            with pytest.raises(sqlite3.DatabaseError):
                store.merge_from(other)

            attached = [row[1] for row in store._conn.execute("PRAGMA database_list")]
            assert "src" not in attached, "the source database is still attached"

    def test_a_successful_merge_detaches(self, tmp_path: Path) -> None:
        base, other = tmp_path / "a.db", tmp_path / "b.db"
        self._seed(base, "run-a")
        self._seed(other, "run-b")

        with Storage(base) as store:
            assert store.merge_from(other).runs_added == 1
            attached = [row[1] for row in store._conn.execute("PRAGMA database_list")]
            assert "src" not in attached

    def test_merging_a_file_into_itself_is_refused(self, tmp_path: Path) -> None:
        base = tmp_path / "a.db"
        self._seed(base, "run-a")
        with Storage(base) as store, pytest.raises(StorageError, match="into itself"):
            store.merge_from(base)


class TestReproducerQuoting:
    """The printed command is the reproducer's whole product, so it has to be paste-able
    in the shell the reader is actually standing in."""

    def test_posix_uses_single_quotes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        assert reproduce._quote("a b") == "'a b'"

    def test_windows_uses_double_quotes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd.exe treats a single quote as a literal character."""
        monkeypatch.setattr(os, "name", "nt")
        assert reproduce._quote("a b") == '"a b"'

    @pytest.mark.parametrize("platform", ["posix", "nt"])
    def test_arguments_without_spaces_are_never_quoted(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        monkeypatch.setattr(os, "name", platform)
        assert reproduce._quote("tests/test_a.py::test_b") == "tests/test_a.py::test_b"


class TestSelectionDetectionIsPlatformAware:
    @pytest.mark.parametrize(
        "argument",
        ["tests/", "tests/test_a.py", "tests/test_a.py::test_b", "test_a.py"],
    )
    def test_posix_style_selection_is_recognized(self, argument: str) -> None:
        assert reproduce._is_selection(argument)

    def test_windows_style_selection_is_recognized(self) -> None:
        """pytest node ids always use `/`, but a user typing a path on Windows uses `\\`."""
        assert reproduce._is_selection("tests\\test_a.py") or os.sep != "\\"

    @pytest.mark.parametrize("argument", ["-x", "--tb=short", "-p", "no:randomly", "-q"])
    def test_flags_are_kept(self, argument: str) -> None:
        assert not reproduce._is_selection(argument)
