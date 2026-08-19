"""SQLite persistence.

One file, no server. A tool for a side-concern like test health gets one chance
to be easy to adopt, and "run this service first" ends that conversation.

All schema DDL lives here. Bump SCHEMA_VERSION on any change.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .models import DatabaseStats, Status, TestOutcome, TestRun

SCHEMA_VERSION = 1
DEFAULT_DB_NAME = ".flaky.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    run_uid     TEXT UNIQUE NOT NULL,
    commit_sha  TEXT,
    branch      TEXT,
    ci_run_id   TEXT,
    started_at  TEXT NOT NULL,
    source_path TEXT,
    runner      TEXT NOT NULL DEFAULT 'unknown',
    iteration   INTEGER,
    seed        TEXT,
    total       INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    duration    REAL
);

CREATE TABLE IF NOT EXISTS results (
    id        INTEGER PRIMARY KEY,
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id   TEXT NOT NULL,
    suite     TEXT,
    name      TEXT NOT NULL,
    status    TEXT NOT NULL,
    duration  REAL,
    message   TEXT,
    detail    TEXT,
    signature TEXT,
    position  INTEGER,
    retried   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_results_test    ON results(test_id);
CREATE INDEX IF NOT EXISTS idx_results_run     ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_sig     ON results(signature);
CREATE INDEX IF NOT EXISTS idx_results_status  ON results(status);
CREATE INDEX IF NOT EXISTS idx_runs_commit     ON runs(commit_sha);
CREATE INDEX IF NOT EXISTS idx_runs_started    ON runs(started_at);
"""

_OUTCOME_COLUMNS = """
    r.test_id, r.name, r.status, r.suite, r.duration, r.message, r.detail,
    r.signature, r.position, r.retried, u.run_uid, u.commit_sha, u.branch,
    u.started_at, u.iteration
"""


class StorageError(RuntimeError):
    """Raised for unusable database state, which is a usage error not a bug."""


@dataclass(frozen=True, slots=True)
class MergeResult:
    """What one source contributed to a merge."""

    source: str
    runs_available: int
    runs_added: int
    runs_skipped: int
    results_added: int


class Storage:
    """Connection wrapper owning the schema.

    Use as a context manager so the connection is always closed:

        with Storage(path) as store:
            store.add_run(run)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL so a CI ingest writing does not block a concurrent read.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- schema ---------------------------------------------------------------

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
            return

        found = int(row["value"])
        if found > SCHEMA_VERSION:
            raise StorageError(
                f"Database at {self.path} uses schema version {found}, but this build "
                f"understands {SCHEMA_VERSION}. Upgrade flaky-test-detective."
            )

    # -- writes ---------------------------------------------------------------

    def add_run(self, run: TestRun) -> tuple[int, bool]:
        """Insert a run and its results.

        Returns (run_id, inserted). When run_uid already exists nothing is written
        and inserted is False. CI retries and local experimentation both re-present
        the same artifact, so double-counting would silently corrupt every rate we
        compute.
        """
        existing = self._conn.execute(
            "SELECT id FROM runs WHERE run_uid = ?", (run.run_uid,)
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False

        cursor = self._conn.execute(
            """
            INSERT INTO runs (run_uid, commit_sha, branch, ci_run_id, started_at,
                              source_path, runner, iteration, seed, total, failed,
                              skipped, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_uid,
                run.commit_sha,
                run.branch,
                run.ci_run_id,
                run.started_at,
                run.source_path,
                run.runner,
                run.iteration,
                run.seed,
                run.total,
                run.failed,
                run.skipped,
                run.duration,
            ),
        )
        run_id = int(cursor.lastrowid or 0)

        self._conn.executemany(
            """
            INSERT INTO results (run_id, test_id, suite, name, status, duration,
                                 message, detail, signature, position, retried)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    o.test_id,
                    o.suite,
                    o.name,
                    str(o.status),
                    o.duration,
                    o.message,
                    o.detail,
                    o.signature,
                    o.position,
                    int(o.retried),
                )
                for o in run.outcomes
            ],
        )
        self._conn.commit()
        return run_id, True

    def has_run(self, run_uid: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM runs WHERE run_uid = ?", (run_uid,)).fetchone()
        return row is not None

    def purge(self, before: str) -> int:
        """Delete runs started before an ISO timestamp. Returns rows removed."""
        cursor = self._conn.execute("DELETE FROM runs WHERE started_at < ?", (before,))
        self._conn.commit()
        return cursor.rowcount

    def merge_from(self, source: str | Path) -> MergeResult:
        """Copy every run from another database that this one does not already have.

        This works, rather than being an approximation, because `run_uid` is a content
        hash. Two databases built independently hold disjoint run sets except where
        they genuinely ingested the same artifact, and in that case the duplicate
        should collapse. So merging is a set union: idempotent, and independent of the
        order the sources are merged in.

        That property is what makes sharded CI and pooled local hunts work. Without
        it the tool only ever sees the history of one machine.
        """
        source_path = Path(source)
        if not source_path.is_file():
            raise StorageError(f"No database at {source_path}")
        if source_path.resolve() == self.path.resolve():
            raise StorageError(f"Cannot merge {source_path} into itself")

        self._check_source_schema(source_path)

        # ATTACH keeps the copy inside SQLite rather than pulling every row through
        # Python, and keeps the whole merge in one transaction.
        self._conn.execute("ATTACH DATABASE ? AS src", (str(source_path),))
        try:
            available = self._scalar("SELECT COUNT(*) FROM src.runs")
            new_uids = [
                str(row["run_uid"])
                for row in self._conn.execute(
                    """
                    SELECT run_uid FROM src.runs
                    WHERE run_uid NOT IN (SELECT run_uid FROM main.runs)
                    """
                ).fetchall()
            ]

            if not new_uids:
                self._conn.commit()
                return MergeResult(
                    source=str(source_path),
                    runs_available=available,
                    runs_added=0,
                    runs_skipped=available,
                    results_added=0,
                )

            self._conn.execute(
                """
                INSERT INTO main.runs (
                    run_uid, commit_sha, branch, ci_run_id, started_at, source_path,
                    runner, iteration, seed, total, failed, skipped, duration
                )
                SELECT run_uid, commit_sha, branch, ci_run_id, started_at, source_path,
                       runner, iteration, seed, total, failed, skipped, duration
                FROM src.runs
                WHERE run_uid NOT IN (SELECT run_uid FROM main.runs)
                """
            )

            # Row ids are per-database, so the foreign key has to be re-pointed. The
            # join through run_uid is what makes that correct rather than assuming
            # ids line up, which they do not.
            placeholders = ",".join("?" * len(new_uids))
            cursor = self._conn.execute(
                f"""
                INSERT INTO main.results (
                    run_id, test_id, suite, name, status, duration, message, detail,
                    signature, position, retried
                )
                SELECT target.id, r.test_id, r.suite, r.name, r.status, r.duration,
                       r.message, r.detail, r.signature, r.position, r.retried
                FROM src.results AS r
                JOIN src.runs AS s ON s.id = r.run_id
                JOIN main.runs AS target ON target.run_uid = s.run_uid
                WHERE s.run_uid IN ({placeholders})
                """,  # noqa: S608 - only a generated list of ? placeholders is interpolated
                new_uids,
            )
            results_added = cursor.rowcount
            self._conn.commit()
        finally:
            self._conn.execute("DETACH DATABASE src")

        return MergeResult(
            source=str(source_path),
            runs_available=available,
            runs_added=len(new_uids),
            runs_skipped=available - len(new_uids),
            results_added=max(0, results_added),
        )

    def _check_source_schema(self, source_path: Path) -> None:
        """Refuse a source written by a newer build rather than misreading it."""
        probe = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        probe.row_factory = sqlite3.Row
        try:
            row = probe.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise StorageError(
                f"{source_path} is not a flaky-test-detective database: {exc}"
            ) from exc
        finally:
            probe.close()

        if row is None:
            raise StorageError(f"{source_path} has no schema version recorded")
        found = int(row["value"])
        if found > SCHEMA_VERSION:
            raise StorageError(
                f"{source_path} uses schema version {found}, but this build understands "
                f"{SCHEMA_VERSION}. Upgrade flaky-test-detective."
            )

    def _scalar(self, sql: str) -> int:
        row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    # -- reads ----------------------------------------------------------------

    def outcomes(
        self,
        *,
        since: str | None = None,
        branch: str | None = None,
        limit_runs: int | None = None,
    ) -> list[TestOutcome]:
        """Load outcomes with run context denormalized onto each row.

        Analysis aggregates in memory in a single pass, so this returns everything
        in the window rather than being called per test. At 100k rows that is
        roughly 30 MB, which is well inside the performance budget and avoids an
        N+1 query per test.
        """
        clauses: list[str] = []
        params: list[object] = []

        if since:
            clauses.append("u.started_at >= ?")
            params.append(since)
        if branch:
            clauses.append("u.branch = ?")
            params.append(branch)

        if limit_runs is not None:
            # Restrict to the most recent N runs, then order those chronologically
            # so flip detection sees real sequence order.
            clauses.append(
                "u.id IN (SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT ?)"
            )
            params.append(limit_runs)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Only two things are interpolated: a module-level column list, and a WHERE
        # clause assembled from the fixed literal strings above. Every user-supplied
        # value travels in `params` as a bound parameter.
        sql = f"""
            SELECT {_OUTCOME_COLUMNS}
            FROM results r
            JOIN runs u ON u.id = r.run_id
            {where}
            ORDER BY u.started_at ASC, u.id ASC, r.position ASC, r.id ASC
        """  # noqa: S608 - interpolates only internal constants; values are bound
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_outcome(row) for row in rows]

    def outcomes_for_test(self, test_id: str) -> list[TestOutcome]:
        sql = f"""
            SELECT {_OUTCOME_COLUMNS}
            FROM results r
            JOIN runs u ON u.id = r.run_id
            WHERE r.test_id = ?
            ORDER BY u.started_at ASC, u.id ASC, r.id ASC
        """  # noqa: S608 - interpolates only a module constant; test_id is bound
        rows = self._conn.execute(sql, (test_id,)).fetchall()
        return [_row_to_outcome(row) for row in rows]

    def find_test_ids(self, fragment: str) -> list[str]:
        """Substring match on test id, for the history command's convenience."""
        rows = self._conn.execute(
            "SELECT DISTINCT test_id FROM results WHERE test_id LIKE ? ORDER BY test_id",
            (f"%{fragment}%",),
        ).fetchall()
        return [str(row["test_id"]) for row in rows]

    def run_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"])

    def result_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()
        return int(row["n"])

    def stats(self) -> DatabaseStats:
        runs = self._conn.execute(
            """
            SELECT COUNT(*) AS runs,
                   COUNT(DISTINCT commit_sha) AS commits,
                   COUNT(DISTINCT branch) AS branches,
                   MIN(started_at) AS first_run,
                   MAX(started_at) AS last_run
            FROM runs
            """
        ).fetchone()
        results = self._conn.execute(
            """
            SELECT COUNT(*) AS results,
                   COUNT(DISTINCT test_id) AS tests,
                   SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END) AS failures
            FROM results
            """
        ).fetchone()
        runners = self._conn.execute(
            "SELECT runner, COUNT(*) AS n FROM runs GROUP BY runner ORDER BY n DESC"
        ).fetchall()

        return DatabaseStats(
            path=str(self.path),
            runs=int(runs["runs"] or 0),
            commits=int(runs["commits"] or 0),
            branches=int(runs["branches"] or 0),
            first_run=runs["first_run"],
            last_run=runs["last_run"],
            results=int(results["results"] or 0),
            tests=int(results["tests"] or 0),
            failures=int(results["failures"] or 0),
            runners={str(r["runner"]): int(r["n"]) for r in runners},
        )

    def recent_runs(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT run_uid, commit_sha, branch, started_at, runner, iteration,
                   total, failed, skipped, duration
            FROM runs
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


@contextmanager
def open_storage(path: str | Path) -> Iterator[Storage]:
    store = Storage(path)
    try:
        yield store
    finally:
        store.close()


def _row_to_outcome(row: sqlite3.Row) -> TestOutcome:
    return TestOutcome(
        test_id=str(row["test_id"]),
        name=str(row["name"]),
        status=Status(str(row["status"])),
        suite=row["suite"],
        duration=row["duration"],
        message=row["message"],
        detail=row["detail"],
        signature=row["signature"],
        position=row["position"],
        retried=bool(row["retried"]),
        run_uid=row["run_uid"],
        commit_sha=row["commit_sha"],
        branch=row["branch"],
        started_at=row["started_at"],
        iteration=row["iteration"],
    )


def add_runs(store: Storage, runs: Iterable[TestRun]) -> tuple[int, int]:
    """Insert many runs. Returns (added, skipped_as_duplicate)."""
    added = skipped = 0
    for run in runs:
        _, inserted = store.add_run(run)
        if inserted:
            added += 1
        else:
            skipped += 1
    return added, skipped
