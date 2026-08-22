"""Command-line interface.

Argument handling, wiring, and exit codes. No analysis logic lives here.

Exit codes are the contract with CI:

    0  clean
    1  flaky tests found, nothing else needing a human
    2  regression or broken test found
    3  usage or input error

Commands that are primarily interactive (`analyze`, `report`) default to
`--fail-on none` so that reading a report never fails a shell. `triage` is the CI
gate and defaults to failing, because that is the entire point of it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__, runner, web
from . import report as report_module
from .analysis import analyze as analyze_outcomes
from .analysis import analyze_one
from .analysis import compare as compare_histories
from .analysis import triage as triage_run
from .analysis.attribution import blame as blame_test
from .benchmark import realworld, run_benchmark
from .benchmark import sweep as run_sweep
from .config import EXAMPLE_CONFIG, Config, load_config
from .environment import detect
from .ingest import ingest_paths, junit
from .models import AnalysisReport, TestOutcome, Verdict
from .quarantine import EXPORT_FORMATS, Quarantine, recommend, verify
from .quarantine import export as export_quarantine
from .report import benchmark_report
from .report import comparison as comparison_report
from .report import console as console_report
from .report import issue as issue_report
from .report import validation as validation_report
from .runner import HuntError
from .storage import Storage, StorageError

EXIT_OK = 0
EXIT_FLAKY = 1
EXIT_REGRESSION = 2
EXIT_USAGE = 3

app = typer.Typer(
    name="flaky",
    help=(
        "Find and diagnose flaky tests from the JUnit XML your test runner already "
        "produces.\n\n"
        "Start with:  flaky hunt -- pytest tests/\n"
        "Then:        flaky analyze"
    ),
    no_args_is_help=True,
    add_completion=False,
)

quarantine_app = typer.Typer(
    help="Manage quarantined flaky tests. Every entry expires and must be re-verified.",
    no_args_is_help=True,
)
app.add_typer(quarantine_app, name="quarantine")

stdout = Console()
stderr = Console(stderr=True)

DEFAULT_HEIGHT = 50
"""Rich only honours an explicit width when a height is set alongside it."""


@app.callback()
def _configure() -> None:
    """Runs before every command.

    Honours COLUMNS so that output width can be pinned. Rich otherwise measures the
    attached terminal, which makes report width depend on whoever happened to run
    the command -- unhelpful for CI artifacts, and a reliable way to write a test
    that passes in one shell and fails in another.
    """
    width = os.environ.get("COLUMNS", "")
    if width.isdigit() and int(width) > 0:
        for console in (stdout, stderr):
            console.width = int(width)
            console.height = DEFAULT_HEIGHT


DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="Path to the history database. Default: .flaky.db"),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to .flaky.toml. Default: discovered upwards."),
]
SinceOption = Annotated[
    str | None,
    typer.Option("--since", help="Only consider runs on or after this ISO date."),
]
BranchOption = Annotated[
    str | None, typer.Option("--branch", help="Only consider runs from this branch.")
]
LastOption = Annotated[
    int | None, typer.Option("--last", help="Only consider the most recent N runs.")
]
ThresholdOption = Annotated[
    float | None,
    typer.Option("--threshold", help="Score above which a test is called flaky."),
]
FailOnOption = Annotated[
    str,
    typer.Option(
        "--fail-on",
        help="Exit non-zero on: none, flaky, or regression.",
    ),
]


def main() -> None:
    """Console script entry point."""
    app()


@app.command()
def version() -> None:
    """Print the version."""
    stdout.print(f"flaky-test-detective {__version__}")


@app.command()
def init(
    db: DbOption = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config.")] = False,
) -> None:
    """Write a commented .flaky.toml and create the database."""
    target = Path(".flaky.toml")
    if target.exists() and not force:
        stderr.print(f"{target} already exists. Use --force to overwrite.")
        raise typer.Exit(EXIT_USAGE)

    target.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    settings = _settings(None, db)

    with Storage(settings.db_path):
        pass

    stdout.print(f"Wrote {target}")
    stdout.print(f"Created {settings.db_path}")
    stdout.print()
    stdout.print("Next: run your suite a few times and feed the reports in.")
    stdout.print("  flaky hunt -- pytest tests/", style="dim")
    stdout.print("  flaky ingest 'reports/*.xml'", style="dim")


@app.command()
def ingest(
    paths: Annotated[
        list[str],
        typer.Argument(help="JUnit XML files, directories, or glob patterns."),
    ],
    db: DbOption = None,
    config: ConfigOption = None,
    commit: Annotated[
        str | None, typer.Option("--commit", help="Commit SHA. Overrides detection.")
    ] = None,
    branch: Annotated[
        str | None, typer.Option("--branch", help="Branch name. Overrides detection.")
    ] = None,
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="CI run identifier. Overrides detection.")
    ] = None,
) -> None:
    """Parse JUnit XML and add it to the history.

    Re-ingesting the same report is safe: runs are identified by content, so
    duplicates are skipped rather than double-counted.
    """
    settings = _settings(config, db)
    env = detect().merged_with(commit_sha=commit, branch=branch, ci_run_id=run_id)

    with _storage(settings) as store:
        result = ingest_paths(
            store,
            paths,
            commit_sha=env.commit_sha,
            branch=env.branch,
            ci_run_id=env.ci_run_id,
        )

    stdout.print(
        f"Added {result.runs_added} runs ({result.results_added} results), "
        f"skipped {result.runs_skipped} already present."
    )
    if env.commit_sha:
        stdout.print(
            f"Commit {env.commit_sha[:12]} on {env.branch or 'unknown branch'}", style="dim"
        )
    else:
        stdout.print(
            "No commit SHA detected. Same-commit divergence, the strongest signal, "
            "will be unavailable. Pass --commit to supply one.",
            style="yellow",
        )

    for path, reason in result.failures:
        stderr.print(f"skipped {path}: {reason}", style="yellow")

    if result.runs_added == 0 and result.had_failures:
        raise typer.Exit(EXIT_USAGE)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def hunt(
    ctx: typer.Context,
    iterations: Annotated[
        int, typer.Option("-n", "--iterations", help="How many times to run the suite.")
    ] = 10,
    shuffle: Annotated[
        bool,
        typer.Option("--shuffle/--no-shuffle", help="Randomize test order between runs."),
    ] = True,
    report_path: Annotated[
        Path | None,
        typer.Option(
            "--report-path",
            help="Where the runner writes JUnit XML. Required for unrecognized runners.",
        ),
    ] = None,
    stop_after: Annotated[
        int | None,
        typer.Option("--stop-after", help="Stop once this many distinct flakes are found."),
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", help="Base seed, so a hunt can be replayed.")
    ] = None,
    timeout: Annotated[
        int, typer.Option("--timeout", help="Seconds allowed per iteration.")
    ] = 1800,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Run a test command repeatedly and record what happens.

    Put the command after a double dash:

        flaky hunt -n 20 -- pytest tests/
        flaky hunt -n 20 -- npx jest
        flaky hunt --report-path target/surefire-reports -- mvn test
    """
    settings = _settings(config, db)
    command = [arg for arg in ctx.args if arg != "--"]

    try:
        plan = runner.plan_hunt(
            command,
            iterations=iterations,
            shuffle=shuffle,
            report_path=report_path,
            cwd=Path.cwd(),
            base_seed=seed,
            timeout=timeout,
        )
    except HuntError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    stdout.print(
        f"Hunting with {plan.runner}: {iterations} iterations, "
        f"order randomization {'on' if plan.shuffle_effective else 'off'}."
    )
    for note in plan.notes:
        stderr.print(f"Note: {note}", style="yellow")

    def progress(result: runner.IterationResult) -> None:
        if result.run is not None:
            stdout.print(
                f"  {result.iteration:>3}/{iterations}  {result.duration:>5.1f}s  "
                f"{result.run.failed:>3} failed  "
                f"{len(result.new_flakes)} flaky so far",
                style="dim",
            )
        else:
            stderr.print(f"  {result.iteration:>3}/{iterations}  {result.error}", style="yellow")

    env = detect()
    with _storage(settings) as store:
        summary = runner.run_hunt(
            plan,
            store,
            settings,
            environment=env,
            progress=progress,
            stop_after_flakes=stop_after,
        )

    stdout.print()
    if summary.stopped_early:
        stdout.print(f"Stopped early: {stop_after} flakes found.", style="yellow")

    stdout.print(
        f"Collected {summary.collected} of {len(summary.iterations)} iterations "
        f"in {summary.total_duration:.1f}s."
    )

    if summary.failed_to_collect:
        stderr.print(
            f"{len(summary.failed_to_collect)} iterations produced no usable report. "
            "The first error was:",
            style="yellow",
        )
        stderr.print(f"  {summary.failed_to_collect[0].error}", style="yellow")

    if summary.collected == 0:
        raise typer.Exit(EXIT_USAGE)

    stdout.print(
        f"Found {len(summary.flaky_test_ids)} flaky tests. Run `flaky analyze` for detail."
    )


@app.command()
def analyze(
    db: DbOption = None,
    config: ConfigOption = None,
    since: SinceOption = None,
    branch: BranchOption = None,
    last: LastOption = None,
    threshold: ThresholdOption = None,
    limit: Annotated[int, typer.Option("--limit", help="How many tests to show.")] = 25,
    show_stable: Annotated[
        bool, typer.Option("--show-stable", help="Include tests scored 0.")
    ] = False,
    clusters: Annotated[
        bool, typer.Option("--clusters/--no-clusters", help="Show shared failure signatures.")
    ] = True,
    fail_on: FailOnOption = "none",
) -> None:
    """Rank tests by flakiness and explain the worst offenders."""
    settings = _settings(config, db, threshold)
    result = _analyze(settings, since=since, branch=branch, last=last)

    console_report.render_report(
        result, stdout, limit=limit, show_clusters=clusters, show_stable=show_stable
    )
    raise typer.Exit(_exit_code(result, fail_on))


@app.command(name="report")
def report_command(
    fmt: Annotated[str, typer.Option("--format", "-f", help="md, json, or html.")] = "md",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
    db: DbOption = None,
    config: ConfigOption = None,
    since: SinceOption = None,
    branch: BranchOption = None,
    last: LastOption = None,
    threshold: ThresholdOption = None,
    fail_on: FailOnOption = "none",
) -> None:
    """Render the report as Markdown, JSON, or a standalone HTML page."""
    settings = _settings(config, db, threshold)
    result = _analyze(settings, since=since, branch=branch, last=last)

    try:
        rendered = report_module.render(result, fmt)
    except ValueError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        stderr.print(f"Wrote {output}", style="dim")
    else:
        stdout.file.write(rendered)

    raise typer.Exit(_exit_code(result, fail_on))


@app.command()
def triage(
    report_file: Annotated[
        Path, typer.Argument(help="The JUnit XML for the run you want triaged.")
    ],
    db: DbOption = None,
    config: ConfigOption = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="console, md, or json.")] = "console",
    store_run: Annotated[
        bool,
        typer.Option("--ingest/--no-ingest", help="Also add this run to the history."),
    ] = False,
    fail_on: FailOnOption = "regression",
) -> None:
    """Answer the red-build question: is this new breakage, or known flakes?

    History is evaluated with this run excluded, so a first-time failure cannot use
    the evidence of itself to look flaky.
    """
    settings = _settings(config, db)

    try:
        run = junit.parse_file(report_file)
    except junit.ParseError as exc:
        stderr.print(f"Could not read {report_file}: {exc}")
        raise typer.Exit(EXIT_USAGE) from exc

    with _storage(settings) as store:
        history = [o for o in store.outcomes() if o.run_uid != run.run_uid]
        if store_run:
            store.add_run(run)

    baseline = analyze_outcomes(history, settings)
    result = triage_run(list(run.outcomes), baseline, source=str(report_file))

    if fmt == "console":
        console_report.render_triage(result, stdout)
    else:
        try:
            stdout.file.write(report_module.render_triage(result, fmt))
        except ValueError as exc:
            stderr.print(str(exc))
            raise typer.Exit(EXIT_USAGE) from exc

    if fail_on == "none":
        raise typer.Exit(EXIT_OK)
    if result.regressions:
        raise typer.Exit(EXIT_REGRESSION)
    if result.new_failures:
        raise typer.Exit(EXIT_REGRESSION if fail_on == "regression" else EXIT_FLAKY)
    raise typer.Exit(EXIT_OK)


@app.command()
def compare(
    baseline_db: Annotated[
        Path | None,
        typer.Option("--baseline", help="History for the branch you branched from."),
    ] = None,
    head_db: Annotated[
        Path | None,
        typer.Option("--head", help="History for this change. Defaults to --db."),
    ] = None,
    base_branch: Annotated[
        str | None,
        typer.Option("--base-branch", help="Use one database, taking the baseline from here."),
    ] = None,
    head_branch: Annotated[
        str | None,
        typer.Option("--head-branch", help="Use one database, taking this change from here."),
    ] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="console, md, or json.")] = "console",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
    fail_on: Annotated[
        str,
        typer.Option("--fail-on", help="Exit non-zero on: none, introduced, or any."),
    ] = "introduced",
    db: DbOption = None,
    config: ConfigOption = None,
    threshold: ThresholdOption = None,
) -> None:
    """Ask whether this change introduced flakiness, or merely inherited it.

    `triage` judges one run. This judges a *branch*, which is the question a pull
    request actually raises: a test being flaky is not the author's problem, a test
    becoming flaky is.

    Two ways to say what to compare:

        flaky compare --baseline main.db --head pr.db
        flaky compare --db .flaky.db --base-branch main --head-branch my-feature

    A flake has to beat the baseline's own uncertainty before it is attributed here.
    Zero failures in 40 baseline runs is consistent with a true failure rate near 7%, so
    that is the bar, not zero -- otherwise the gate fires on luck, and a gate that fires
    on luck gets ignored.
    """
    settings = _settings(config, db, threshold)

    if fail_on not in ("none", "introduced", "any"):
        stderr.print(f"--fail-on must be none, introduced, or any, not {fail_on!r}")
        raise typer.Exit(EXIT_USAGE)

    using_branches = bool(base_branch or head_branch)
    using_databases = bool(baseline_db or head_db)

    if using_branches and using_databases:
        stderr.print(
            "Choose one comparison source: either --baseline/--head databases, or "
            "--base-branch/--head-branch within a single database."
        )
        raise typer.Exit(EXIT_USAGE)

    if using_branches:
        if not (base_branch and head_branch):
            stderr.print("--base-branch and --head-branch must be given together.")
            raise typer.Exit(EXIT_USAGE)
        baseline_outcomes, head_outcomes = _branch_windows(settings, base_branch, head_branch)
        labels = (base_branch, head_branch)
    else:
        if not baseline_db:
            stderr.print(
                "Nothing to compare against. Give --baseline <db>, or use\n"
                "  --base-branch main --head-branch <yours>  to split one database."
            )
            raise typer.Exit(EXIT_USAGE)
        head_path = head_db or settings.db_path
        baseline_outcomes = _outcomes_from(baseline_db)
        head_outcomes = _outcomes_from(head_path)
        labels = (baseline_db.name, head_path.name)

    baseline = analyze_outcomes(baseline_outcomes, settings)
    head = analyze_outcomes(head_outcomes, settings)
    result = compare_histories(baseline, head, baseline_label=labels[0], head_label=labels[1])

    if fmt == "console":
        comparison_report.render_console(result, stdout)
    else:
        try:
            _emit(comparison_report.render(result, fmt), output)
        except ValueError as exc:
            stderr.print(str(exc))
            raise typer.Exit(EXIT_USAGE) from exc

    if fail_on == "none":
        raise typer.Exit(EXIT_OK)
    if result.new_breaks:
        raise typer.Exit(EXIT_REGRESSION)
    if result.new_flakes:
        raise typer.Exit(EXIT_FLAKY)
    if fail_on == "any" and (result.worse or result.known_flakes):
        raise typer.Exit(EXIT_FLAKY)
    raise typer.Exit(EXIT_OK)


def _outcomes_from(path: Path) -> list[TestOutcome]:
    """Read every outcome from one database, or exit saying which one was missing."""
    if not path.is_file():
        stderr.print(f"No database at {path}.")
        raise typer.Exit(EXIT_USAGE)
    try:
        with Storage(path) as store:
            return store.outcomes()
    except StorageError as exc:
        stderr.print(f"Could not read {path}: {exc}")
        raise typer.Exit(EXIT_USAGE) from exc


def _branch_windows(
    settings: Config, base_branch: str, head_branch: str
) -> tuple[list[TestOutcome], list[TestOutcome]]:
    """Split a single database into two windows by branch.

    The common CI shape: one cached database accumulating `main` while pull-request runs
    land alongside it. Both queries exclude the other branch outright rather than
    slicing by date, so a long-running branch cannot leak its own runs into the baseline
    it is being judged against.
    """
    with _storage(settings) as store:
        baseline = store.outcomes(branch=base_branch)
        head = store.outcomes(branch=head_branch)

    if not baseline:
        stderr.print(
            f"No runs recorded on branch {base_branch!r} in {settings.db_path}.\n"
            "Record the base branch first, or compare two databases with --baseline."
        )
        raise typer.Exit(EXIT_USAGE)
    if not head:
        stderr.print(f"No runs recorded on branch {head_branch!r} in {settings.db_path}.")
        raise typer.Exit(EXIT_USAGE)

    return baseline, head


@app.command()
def history(
    test_id: Annotated[str, typer.Argument(help="Full test id, or any part of one.")],
    db: DbOption = None,
    config: ConfigOption = None,
    limit: Annotated[int, typer.Option("--limit", help="How many runs to show.")] = 40,
) -> None:
    """Show one test's timeline, run by run."""
    settings = _settings(config, db)

    with _storage(settings) as store:
        resolved = _resolve_test_id(store, test_id)
        outcomes = store.outcomes_for_test(resolved)
        all_outcomes = store.outcomes()

    from .analysis.ordering import build_predecessor_index

    analysis = analyze_one(
        resolved, outcomes, settings, predecessors=build_predecessor_index(all_outcomes)
    )
    timeline = [(o.started_at or "", str(o.status), o.message) for o in outcomes[-limit:]]
    console_report.render_history(resolved, analysis, timeline, stdout)


@app.command()
def stats(db: DbOption = None, config: ConfigOption = None) -> None:
    """Summarize what is in the database."""
    settings = _settings(config, db)
    with _storage(settings) as store:
        console_report.render_stats(store.stats(), stdout)


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Port to listen on.")] = 8420,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Interface to bind. Defaults to loopback; anything else exposes your "
            "test data to the network with no authentication.",
        ),
    ] = web.LOOPBACK,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open a browser window.")
    ] = True,
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress request logging.")] = False,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Open the dashboard: a local, read-only view of your test history.

    Answers "can I trust my CI right now" with a trust score, a ranked worklist, and a
    per-test investigation page showing the evidence behind each verdict.
    """
    settings = _settings(config, db)

    if not settings.db_path.is_file():
        stderr.print(
            f"No database at {settings.db_path}.\n"
            "Record some runs first:\n"
            "  flaky hunt -n 20 -- pytest tests/\n"
            "  flaky ingest 'reports/**/*.xml'"
        )
        raise typer.Exit(EXIT_USAGE)

    if not web.is_built():
        stderr.print(
            "The compiled dashboard is missing from this install.\n"
            "Build it with:  cd web && npm ci && npm run build\n"
            "Or use:         flaky report --format html --output flaky.html",
            style="yellow",
        )

    try:
        server = web.serve(settings, host=host, port=port, quiet=quiet)
    except web.DashboardError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    url = f"http://{host}:{port}"
    stdout.print(f"Dashboard on {url}", style="bold")
    stdout.print(f"Reading {settings.db_path}  (read-only)", style="dim")

    if host != web.LOOPBACK:
        stderr.print(
            f"\nWarning: bound to {host}, not loopback. There is no authentication, so "
            "every test name, failure message and commit SHA in this database is now "
            "readable by anyone who can reach this port.",
            style="yellow",
        )

    stdout.print("Press Ctrl+C to stop.", style="dim")

    if open_browser:
        import webbrowser

        # Threaded so a browser that blocks does not hold up the server.
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stdout.print("\nStopped.", style="dim")
    finally:
        server.server_close()


@app.command()
def blame(
    test_id: Annotated[str, typer.Argument(help="Full test id, or any part of one.")],
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Find the commit where a test started being flaky.

    Reports honestly when the answer is not in the data. Naming the oldest commit in
    the window would be an accusation the history does not support, and a good way to
    send someone reverting an innocent change.
    """
    settings = _settings(config, db)

    with _storage(settings) as store:
        resolved = _resolve_test_id(store, test_id)
        outcomes = store.outcomes_for_test(resolved)

    console_report.render_blame(blame_test(resolved, outcomes), stdout)


@app.command(name="issue")
def issue_command(
    test_id: Annotated[str, typer.Argument(help="Full test id, or any part of one.")],
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help=f"One of: {', '.join(issue_report.FORMATS)}"),
    ] = "markdown",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option("--repository", help="Repository URL, to build a compare link."),
    ] = None,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Write an issue body or chat message for one test, from its real diagnosis.

    Nothing is sent anywhere: the output goes to stdout for you to paste or pipe. That
    keeps the tool free of credentials, which is worth more than a built-in API client.

        flaky issue test_expects_clean_registry | gh issue create --body-file -
        flaky issue test_expects_clean_registry -f slack | curl -d @- "$SLACK_WEBHOOK"
    """
    settings = _settings(config, db)

    with _storage(settings) as store:
        resolved = _resolve_test_id(store, test_id)
        outcomes = store.outcomes_for_test(resolved)
        all_outcomes = store.outcomes()

    from .analysis.ordering import build_predecessor_index

    analysis = analyze_one(
        resolved, outcomes, settings, predecessors=build_predecessor_index(all_outcomes)
    )
    attribution = blame_test(resolved, outcomes)

    try:
        rendered = issue_report.render(analysis, attribution, fmt=fmt, repository=repository)
    except ValueError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    _emit(rendered, output)


@app.command()
def merge(
    sources: Annotated[
        list[Path],
        typer.Argument(help="Databases to merge in. Directories are searched for *.db."),
    ],
    into: Annotated[
        Path | None, typer.Option("--into", help="Target database. Defaults to the configured one.")
    ] = None,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Combine history from other machines or CI shards.

    Safe to repeat. Runs are identified by a content hash, so merging the same source
    twice adds nothing and merge order does not affect the result.
    """
    settings = _settings(config, db if db else into)
    found = _expand_databases(sources)

    if not found:
        stderr.print(f"No databases found in: {', '.join(str(s) for s in sources)}")
        raise typer.Exit(EXIT_USAGE)

    added = skipped = results = 0
    with _storage(settings) as store:
        for source in found:
            try:
                outcome = store.merge_from(source)
            except StorageError as exc:
                stderr.print(f"skipped {source}: {exc}", style="yellow")
                continue

            added += outcome.runs_added
            skipped += outcome.runs_skipped
            results += outcome.results_added
            stdout.print(
                f"  {source.name}: +{outcome.runs_added} runs "
                f"({outcome.runs_skipped} already present)",
                style="dim",
            )

        total_runs = store.run_count()

    stdout.print()
    stdout.print(
        f"Merged {added} runs and {results} results from {len(found)} "
        f"{'source' if len(found) == 1 else 'sources'}. "
        f"Skipped {skipped} duplicates. {settings.db_path.name} now holds {total_runs} runs."
    )


@app.command()
def benchmark(
    seed: Annotated[int, typer.Option("--seed", help="Makes the measurement reproducible.")] = 1234,
    runs: Annotated[int, typer.Option("--runs", help="Runs recorded per generated test.")] = 30,
    coverage: Annotated[
        float, typer.Option("--coverage", help="Fraction of runs carrying a commit SHA.")
    ] = 1.0,
    sweep_over: Annotated[
        str | None,
        typer.Option("--sweep", help="Sweep accuracy over 'runs' or 'coverage'."),
    ] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="console, md, or json.")] = "console",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
    confusion: Annotated[
        bool, typer.Option("--confusion/--no-confusion", help="Show the confusion matrix.")
    ] = True,
) -> None:
    """Measure this tool's accuracy against data whose answer is known.

    Generates test histories with known labels, runs the real analysis over them, and
    reports precision and recall per verdict. The two headline figures are the rate at
    which a genuine break is misreported as flaky, and the reverse.

    Everything is reproducible from --seed.
    """
    if sweep_over:
        try:
            results = run_sweep(seed=seed, over=sweep_over)
        except ValueError as exc:
            stderr.print(str(exc))
            raise typer.Exit(EXIT_USAGE) from exc

        rendered = benchmark_report.render_sweep_markdown(results, sweep_over)
        _emit(rendered, output)
        return

    result = run_benchmark(seed=seed, runs=runs, commit_coverage=coverage)

    if fmt == "console":
        benchmark_report.render_console(result, stdout, show_confusion=confusion)
        return
    if fmt in ("md", "markdown"):
        _emit(benchmark_report.render_markdown(result), output)
        return
    if fmt == "json":
        _emit(benchmark_report.render_json(result), output)
        return

    stderr.print(f"Unknown format {fmt!r}. Use console, md, or json.")
    raise typer.Exit(EXIT_USAGE)


@app.command()
def validate(
    results_dir: Annotated[
        Path,
        typer.Argument(help="Directory of recorded validation results."),
    ] = Path("validation/results"),
    fmt: Annotated[str, typer.Option("--format", "-f", help="console, md, or json.")] = "console",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
) -> None:
    """Score this tool against published flaky-test labels from real repositories.

    Reads the recorded results in `validation/` and recomputes every number in
    docs/real-world.md. Seconds, not hours: the raw output of each project's run is
    committed, so the claim is checkable without cloning a dozen repositories.

    Labels come from IDoFT, the Illinois dataset of flaky tests, so the answer key is
    not ours. Re-run the underlying evaluation with `python validation/run.py`.
    """
    try:
        raw, skipped = realworld.load_results(results_dir)
    except FileNotFoundError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    result = realworld.score_all(raw, skipped=skipped)

    if fmt == "console":
        validation_report.render_console(result, stdout)
        return

    try:
        rendered = validation_report.render(result, fmt)
    except ValueError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    _emit(rendered, output)


@quarantine_app.command("list")
def quarantine_list(db: DbOption = None, config: ConfigOption = None) -> None:
    """Show current quarantine entries and when they expire."""
    settings = _settings(config, db)
    store = Quarantine(settings.quarantine_path)

    if not len(store):
        stdout.print("Nothing quarantined.")
        return

    for entry in store.entries:
        state = "EXPIRED" if entry.is_expired() else f"{entry.days_remaining()}d left"
        style = "yellow" if entry.is_expired() else "dim"
        stdout.print(f"{entry.score:.2f}  {entry.test_id}")
        stdout.print(f"      {entry.reason}  |  {state}", style=style)

    stdout.print()
    stdout.print(
        f"{len(store.active())} active, {len(store.expired())} expired. "
        "Run `flaky quarantine verify` to re-check the expired ones.",
        style="dim",
    )


@quarantine_app.command("recommend")
def quarantine_recommend(
    db: DbOption = None,
    config: ConfigOption = None,
    since: SinceOption = None,
    branch: BranchOption = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Add the recommendations to the quarantine list.")
    ] = False,
    days: Annotated[
        int | None, typer.Option("--days", help="Days until the entries expire.")
    ] = None,
) -> None:
    """List tests flaky enough to justify removing them from the suite.

    Regressions and broken tests are never recommended. Quarantining a real failure
    is how bugs reach production.
    """
    settings = _settings(config, db)
    result = _analyze(settings, since=since, branch=branch, last=None)
    candidates = recommend(result, settings)

    if not candidates:
        stdout.print(
            f"Nothing scores above the quarantine threshold of {settings.quarantine_threshold:.2f}."
        )
        return

    for test in candidates:
        cause = test.cause.cause if test.cause else "unknown"
        stdout.print(f"{test.score:.2f}  {test.test_id}")
        stdout.print(f"      {cause}, failed {test.failures} of {test.runs} runs", style="dim")

    if not apply:
        stdout.print()
        stdout.print("Re-run with --apply to quarantine these.", style="dim")
        return

    store = Quarantine(settings.quarantine_path)
    expiry_days = days if days is not None else settings.quarantine_days
    for test in candidates:
        cause = test.cause.cause if test.cause else "unknown"
        store.add(
            test.test_id,
            reason=f"{cause}, score {test.score:.2f}, failed {test.failures}/{test.runs} runs",
            score=test.score,
            days=expiry_days,
        )
    store.save()
    stdout.print()
    stdout.print(f"Quarantined {len(candidates)} tests for {expiry_days} days.")
    stdout.print(f"Wrote {settings.quarantine_path}", style="dim")


@quarantine_app.command("add")
def quarantine_add(
    test_id: Annotated[str, typer.Argument(help="Exact test id to quarantine.")],
    reason: Annotated[
        str, typer.Option("--reason", help="Why it is being quarantined.")
    ] = "manual",
    days: Annotated[int | None, typer.Option("--days", help="Days until expiry.")] = None,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Quarantine one test by hand."""
    settings = _settings(config, db)
    store = Quarantine(settings.quarantine_path)
    entry = store.add(
        test_id, reason=reason, days=days if days is not None else settings.quarantine_days
    )
    store.save()
    stdout.print(f"Quarantined {test_id} until {entry.expires_at[:10]}.")


@quarantine_app.command("remove")
def quarantine_remove(
    test_id: Annotated[str, typer.Argument(help="Exact test id to release.")],
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Release a test from quarantine."""
    settings = _settings(config, db)
    store = Quarantine(settings.quarantine_path)
    if not store.remove(test_id):
        stderr.print(f"{test_id} is not quarantined.")
        raise typer.Exit(EXIT_USAGE)
    store.save()
    stdout.print(f"Released {test_id}.")


@quarantine_app.command("export")
def quarantine_export(
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help=f"One of: {', '.join(EXPORT_FORMATS)}"),
    ] = "list",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write here instead of stdout.")
    ] = None,
    db: DbOption = None,
    config: ConfigOption = None,
) -> None:
    """Emit the quarantine list in a form your runner accepts.

    `pytest-conftest` is the one to prefer in CI: it skips the tests with a visible
    reason rather than removing them silently.
    """
    settings = _settings(config, db)
    store = Quarantine(settings.quarantine_path)

    try:
        rendered = export_quarantine(store.entries, fmt)
    except ValueError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        stderr.print(f"Wrote {output}", style="dim")
    else:
        stdout.file.write(rendered)


@quarantine_app.command("verify")
def quarantine_verify(
    db: DbOption = None,
    config: ConfigOption = None,
    since: SinceOption = None,
    release: Annotated[
        bool, typer.Option("--release", help="Remove entries that now look stable.")
    ] = False,
) -> None:
    """Re-check expired entries against current history."""
    settings = _settings(config, db)
    store = Quarantine(settings.quarantine_path)

    if not store.expired():
        stdout.print(f"No expired entries. {len(store.active())} still active.")
        return

    result = _analyze(settings, since=since, branch=None, last=None)
    outcome = verify(store, result, config=settings)

    if outcome.releasable:
        stdout.print("Now stable, safe to release:", style="green")
        for entry in outcome.releasable:
            stdout.print(f"  {entry.test_id}")

    if outcome.still_flaky:
        stdout.print()
        stdout.print("Still flaky, quarantine renewed:", style="yellow")
        for entry in outcome.still_flaky:
            stdout.print(f"  {entry.test_id}")

    if outcome.unknown:
        stdout.print()
        stdout.print(
            "Expired with no recent runs. A quarantined test stops producing "
            "evidence, so it can never prove itself stable. Release these and watch:",
            style="yellow",
        )
        for entry in outcome.unknown:
            stdout.print(f"  {entry.test_id}")

    if release and outcome.releasable:
        for entry in outcome.releasable:
            store.remove(entry.test_id)
        for entry in outcome.still_flaky:
            store.renew(entry.test_id, days=settings.quarantine_days)
        store.save()
        stdout.print()
        stdout.print(f"Released {len(outcome.releasable)}, renewed {len(outcome.still_flaky)}.")
    elif outcome.releasable:
        stdout.print()
        stdout.print("Re-run with --release to apply.", style="dim")


# -- helpers ------------------------------------------------------------------


def _emit(text: str, output: Path | None) -> None:
    """Write rendered text to a file or to stdout."""
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        stderr.print(f"Wrote {output}", style="dim")
    else:
        stdout.file.write(text)


def _expand_databases(sources: list[Path]) -> list[Path]:
    """Resolve arguments to database files, searching directories.

    Directories are the sharded-CI case: a job downloads every shard's artifact into
    one folder and merges the lot in a single command.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    for source in sources:
        candidates = sorted(source.glob("*.db")) if source.is_dir() else [source]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)

    return found


def _resolve_test_id(store: Storage, fragment: str) -> str:
    """Turn a partial test id into exactly one real one, or exit with guidance."""
    matches = store.find_test_ids(fragment)
    if not matches:
        stderr.print(f"No test matching {fragment!r}. Try `flaky analyze` to see what exists.")
        raise typer.Exit(EXIT_USAGE)
    if fragment in matches:
        return fragment
    if len(matches) > 1:
        stderr.print(f"{len(matches)} tests match {fragment!r}:")
        for candidate in matches[:20]:
            stderr.print(f"  {candidate}")
        raise typer.Exit(EXIT_USAGE)
    return matches[0]


def _settings(config: Path | None, db: Path | None, threshold: float | None = None) -> Config:
    try:
        settings = load_config(config)
    except ValueError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    return settings.with_overrides(
        db_path=db.expanduser().resolve() if db else None,
        flake_threshold=threshold,
    )


def _storage(settings: Config) -> Storage:
    try:
        return Storage(settings.db_path)
    except StorageError as exc:
        stderr.print(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc


def _analyze(
    settings: Config, *, since: str | None, branch: str | None, last: int | None
) -> AnalysisReport:
    with _storage(settings) as store:
        if store.run_count() == 0:
            stderr.print(
                f"No runs in {settings.db_path}.\n"
                "Record some first:\n"
                "  flaky hunt -- pytest tests/\n"
                "  flaky ingest 'reports/*.xml'"
            )
            raise typer.Exit(EXIT_USAGE)

        outcomes: list[TestOutcome] = store.outcomes(since=since, branch=branch, limit_runs=last)

    if not outcomes:
        stderr.print("No runs matched those filters.")
        raise typer.Exit(EXIT_USAGE)

    return analyze_outcomes(outcomes, settings)


def _exit_code(result: AnalysisReport, fail_on: str) -> int:
    """Translate an analysis into a process exit code.

    Regression outranks flaky: a real break is the more urgent thing to report, and
    conflating the two is what teaches people to ignore red builds.
    """
    if fail_on not in ("none", "flaky", "regression"):
        stderr.print(f"--fail-on must be none, flaky, or regression, not {fail_on!r}")
        return EXIT_USAGE

    if fail_on == "none":
        return EXIT_OK

    has_break = any(t.verdict in (Verdict.REGRESSION, Verdict.BROKEN) for t in result.tests)
    if has_break:
        return EXIT_REGRESSION
    if fail_on == "flaky" and result.flaky:
        return EXIT_FLAKY
    return EXIT_OK


if __name__ == "__main__":
    main()
