"""The hunt: run a real test command repeatedly and collect what happens.

Waiting for CI to expose a flake is slow and expensive. Running the suite twenty
times locally is neither. This module drives that, feeding every iteration through
the same parsers CI ingestion uses, so there is only one code path to keep correct.

Two things it deliberately does not do:

- **Parse stdout.** Runner output formats change between minor versions; JUnit XML
  does not. The XML path is required, and injected automatically for runners where
  that is possible.
- **Pretend to randomize.** Order randomization needs runner support, and not
  every runner or plugin combination has it. Where it is unavailable, the hunt says
  so loudly rather than running N identical iterations and letting the user believe
  they tested for order dependence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .analysis import analyze
from .config import Config
from .environment import Environment
from .ingest import junit
from .ingest.junit import ParseError
from .models import TestOutcome, TestRun, Verdict
from .storage import Storage

HELP_PROBE_TIMEOUT = 20
DEFAULT_ITERATION_TIMEOUT = 1800


@dataclass(frozen=True, slots=True)
class _RunnerSpec:
    """How to make one runner emit JUnit XML and shuffle its order."""

    name: str
    argv_markers: tuple[str, ...]
    report_flag: str | None = None
    report_env: str | None = None
    extra_args: tuple[str, ...] = ()
    shuffle_flags: tuple[tuple[str, str], ...] = ()
    """(help_token, flag_template) pairs, tried in order. The help text of the
    actual command decides which is available, since these come from optional
    plugins as often as from the runner itself."""
    report_is_directory: bool = False


_SPECS: tuple[_RunnerSpec, ...] = (
    _RunnerSpec(
        name="pytest",
        argv_markers=("pytest", "py.test"),
        report_flag="--junitxml={path}",
        shuffle_flags=(
            ("--randomly-seed", "--randomly-seed={seed}"),
            ("--random-order-seed", "--random-order --random-order-seed={seed}"),
        ),
    ),
    _RunnerSpec(
        name="jest",
        argv_markers=("jest",),
        report_env="JEST_JUNIT_OUTPUT_FILE",
        extra_args=("--reporters=default", "--reporters=jest-junit"),
        shuffle_flags=(("--shuffle", "--shuffle --seed={seed}"),),
    ),
    _RunnerSpec(
        name="vitest",
        argv_markers=("vitest",),
        report_flag="--outputFile={path}",
        extra_args=("--reporter=junit",),
        shuffle_flags=(("--sequence.shuffle", "--sequence.shuffle"),),
    ),
    _RunnerSpec(
        name="go",
        argv_markers=("gotestsum",),
        report_flag="--junitfile={path}",
        shuffle_flags=(("-shuffle", "--  -shuffle=on"),),
    ),
)


@dataclass(frozen=True, slots=True)
class HuntPlan:
    """A resolved, ready-to-execute hunt."""

    command: tuple[str, ...]
    runner: str
    iterations: int
    report_path: Path
    report_is_directory: bool
    shuffle: bool
    shuffle_template: str | None
    report_env: str | None = None
    base_seed: int = 0
    cwd: Path | None = None
    timeout: int = DEFAULT_ITERATION_TIMEOUT
    notes: tuple[str, ...] = ()
    """Things the user needs to know, such as randomization being unavailable."""

    @property
    def shuffle_effective(self) -> bool:
        return self.shuffle and self.shuffle_template is not None


@dataclass(frozen=True, slots=True)
class IterationResult:
    """What one iteration produced."""

    iteration: int
    exit_code: int
    duration: float
    seed: str | None = None
    run: TestRun | None = None
    error: str | None = None
    new_flakes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether results were collected.

        A non-zero exit code is expected and fine: failing tests are the point.
        Only a missing or unparseable report is a problem.
        """
        return self.run is not None


@dataclass
class HuntSummary:
    """Aggregate of a completed hunt."""

    plan: HuntPlan
    iterations: list[IterationResult] = field(default_factory=list)
    stopped_early: bool = False
    flaky_test_ids: tuple[str, ...] = ()

    @property
    def collected(self) -> int:
        return sum(1 for i in self.iterations if i.ok)

    @property
    def failed_to_collect(self) -> list[IterationResult]:
        return [i for i in self.iterations if not i.ok]

    @property
    def total_duration(self) -> float:
        return sum(i.duration for i in self.iterations)


class HuntError(RuntimeError):
    """The hunt cannot start. A usage error, reported before anything runs."""


def plan_hunt(
    command: Sequence[str],
    *,
    iterations: int,
    shuffle: bool = True,
    report_path: str | Path | None = None,
    cwd: str | Path | None = None,
    base_seed: int | None = None,
    timeout: int = DEFAULT_ITERATION_TIMEOUT,
    workdir: Path | None = None,
) -> HuntPlan:
    """Work out how to run the command N times and where the XML will land.

    Everything that can fail is resolved here, before any test runs, so a
    misconfigured hunt fails in a second rather than twenty minutes in.
    """
    argv = [str(part) for part in command if str(part)]
    if not argv:
        raise HuntError("No command given. Try: flaky hunt -- pytest tests/")
    if iterations < 2:
        raise HuntError(
            f"Iterations must be at least 2 to observe a flake, got {iterations}. "
            "A single run cannot show a test behaving two ways."
        )

    spec = _match_spec(argv)
    notes: list[str] = []

    explicit_report = Path(report_path).expanduser().resolve() if report_path else None
    if explicit_report is None:
        if spec is None or (spec.report_flag is None and spec.report_env is None):
            raise HuntError(
                f"Cannot work out where {argv[0]!r} writes JUnit XML.\n"
                "Point at it explicitly with --report-path, for example:\n"
                "  flaky hunt --report-path target/surefire-reports -- mvn test\n"
                "  flaky hunt --report-path report.xml -- ./run-tests.sh"
            )
        base = workdir or Path(tempfile.mkdtemp(prefix="flaky-hunt-"))
        resolved_report = base / "iteration.xml"
        report_is_directory = False
    else:
        resolved_report = explicit_report
        report_is_directory = explicit_report.is_dir() or explicit_report.suffix == ""

    shuffle_template: str | None = None
    if shuffle:
        if spec is None:
            notes.append(
                "Order randomization is unavailable: the runner was not recognized. "
                "Iterations will run in the suite's natural order, so "
                "order-dependent flakes will not be provoked."
            )
        else:
            shuffle_template = _probe_shuffle(spec, argv, cwd)
            if shuffle_template is None:
                notes.append(
                    f"Order randomization is unavailable for {spec.name}: none of "
                    f"{', '.join(t for t, _ in spec.shuffle_flags) or 'its flags'} "
                    "appeared in the command's --help output. " + _shuffle_hint(spec.name)
                )

    return HuntPlan(
        command=tuple(argv),
        runner=spec.name if spec else "unknown",
        iterations=iterations,
        report_path=resolved_report,
        report_is_directory=report_is_directory,
        shuffle=shuffle,
        shuffle_template=shuffle_template,
        report_env=spec.report_env if spec else None,
        base_seed=base_seed if base_seed is not None else int(time.time()) % 100_000,
        cwd=Path(cwd).expanduser().resolve() if cwd else None,
        timeout=timeout,
        notes=tuple(notes),
    )


def run_hunt(
    plan: HuntPlan,
    store: Storage,
    config: Config | None = None,
    *,
    environment: Environment | None = None,
    progress: Callable[[IterationResult], None] | None = None,
    stop_after_flakes: int | None = None,
) -> HuntSummary:
    """Execute the plan, ingesting each iteration as it completes.

    Results are stored incrementally rather than at the end, so an interrupted
    hunt still leaves usable history behind.
    """
    settings = config or Config()
    env = environment or Environment()
    summary = HuntSummary(plan=plan)
    collected: list[TestOutcome] = []

    for index in range(1, plan.iterations + 1):
        result = _run_iteration(plan, index, env)

        if result.run is not None:
            store.add_run(result.run)
            collected.extend(
                _with_run_context(outcome, result.run) for outcome in result.run.outcomes
            )
            found = _flaky_ids(collected, settings)
            result = _replace_flakes(result, found)

        summary.iterations.append(result)
        if progress is not None:
            progress(result)

        if stop_after_flakes is not None and len(result.new_flakes) >= stop_after_flakes:
            summary.stopped_early = True
            summary.flaky_test_ids = result.new_flakes
            return summary

    summary.flaky_test_ids = _flaky_ids(collected, settings)
    return summary


def _run_iteration(plan: HuntPlan, index: int, env: Environment) -> IterationResult:
    seed = str(plan.base_seed + index)
    argv = _build_argv(plan, seed)
    process_env = _build_env(plan)

    _clear_report(plan)
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=str(plan.cwd) if plan.cwd else None,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=plan.timeout,
            check=False,
        )
        exit_code = completed.returncode
        stderr_tail = (completed.stderr or "").strip().splitlines()[-3:]
    except subprocess.TimeoutExpired:
        return IterationResult(
            iteration=index,
            exit_code=-1,
            duration=time.monotonic() - started,
            seed=seed,
            error=f"iteration exceeded the {plan.timeout}s timeout",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return IterationResult(
            iteration=index,
            exit_code=-1,
            duration=time.monotonic() - started,
            seed=seed,
            error=f"could not run {argv[0]!r}: {exc}",
        )

    duration = time.monotonic() - started
    run, error = _collect_report(plan, index, seed, env)

    if run is None and error:
        hint = f" Last stderr: {' | '.join(stderr_tail)}" if stderr_tail else ""
        error = f"{error}.{hint}"

    return IterationResult(
        iteration=index,
        exit_code=exit_code,
        duration=duration,
        seed=seed,
        run=run,
        error=error,
    )


def _build_argv(plan: HuntPlan, seed: str) -> list[str]:
    argv = list(plan.command)
    spec = _match_spec(argv)

    if spec is not None:
        for extra in spec.extra_args:
            if extra not in argv:
                argv.append(extra)

        if spec.report_flag and not _has_report_flag(argv, spec.report_flag):
            argv.append(spec.report_flag.format(path=plan.report_path))

    if plan.shuffle_effective and plan.shuffle_template:
        argv.extend(plan.shuffle_template.format(seed=seed).split())

    return argv


def _build_env(plan: HuntPlan) -> dict[str, str]:
    process_env = dict(os.environ)
    if plan.report_env:
        process_env[plan.report_env] = str(plan.report_path)
    # Marker so a suite can detect it is being hunted, which the demo suite in
    # examples/ uses to stay deterministic during this project's own CI.
    process_env["FLAKY_HUNT"] = "1"
    return process_env


def _has_report_flag(argv: list[str], template: str) -> bool:
    flag = template.split("=", 1)[0]
    return any(arg == flag or arg.startswith(flag + "=") for arg in argv)


def _clear_report(plan: HuntPlan) -> None:
    """Remove stale reports so an iteration cannot ingest the previous one's."""
    if plan.report_is_directory:
        if plan.report_path.is_dir():
            for child in plan.report_path.glob("*.xml"):
                child.unlink(missing_ok=True)
        else:
            plan.report_path.mkdir(parents=True, exist_ok=True)
        return

    plan.report_path.parent.mkdir(parents=True, exist_ok=True)
    plan.report_path.unlink(missing_ok=True)


def _collect_report(
    plan: HuntPlan, index: int, seed: str, env: Environment
) -> tuple[TestRun | None, str | None]:
    paths = _report_files(plan)
    if not paths:
        return None, f"no JUnit XML appeared at {plan.report_path}"

    runs: list[TestRun] = []
    errors: list[str] = []
    for path in paths:
        try:
            runs.append(
                junit.parse_file(
                    path,
                    iteration=index,
                    seed=seed,
                    commit_sha=env.commit_sha,
                    branch=env.branch,
                    ci_run_id=env.ci_run_id,
                )
            )
        except ParseError as exc:
            errors.append(f"{path.name}: {exc}")

    if not runs:
        return None, "; ".join(errors) or "no parseable report"

    # Labels are attached here rather than in the parser: `junit.parse_file` reads a file
    # and should not know what machine it is running on.
    merged = replace(_merge_runs(runs, iteration=index, seed=seed), labels=env.labels)
    return merged, "; ".join(errors) or None


def _report_files(plan: HuntPlan) -> list[Path]:
    if plan.report_is_directory:
        if not plan.report_path.is_dir():
            return []
        return sorted(plan.report_path.glob("*.xml"))
    return [plan.report_path] if plan.report_path.is_file() else []


def _merge_runs(runs: list[TestRun], *, iteration: int, seed: str) -> TestRun:
    """Fold several report files into one run.

    Maven and Gradle write one XML per test class, but that is still a single
    execution of the suite. Treating each file as its own run would multiply the
    run count and quietly deflate every rate the tool computes.

    Positions are renumbered across the merged sequence so order-dependence
    detection sees the real execution order rather than restarting at zero per file.
    """
    if len(runs) == 1:
        return runs[0]

    ordered = sorted(runs, key=lambda r: (r.started_at, r.source_path or ""))
    outcomes: list[TestOutcome] = []
    position = 0
    for run in ordered:
        for outcome in run.outcomes:
            outcomes.append(
                TestOutcome(
                    test_id=outcome.test_id,
                    name=outcome.name,
                    status=outcome.status,
                    suite=outcome.suite,
                    duration=outcome.duration,
                    message=outcome.message,
                    detail=outcome.detail,
                    signature=outcome.signature,
                    position=position,
                    retried=outcome.retried,
                )
            )
            position += 1

    first = ordered[0]
    durations = [r.duration for r in ordered if r.duration is not None]
    return TestRun(
        run_uid=f"{first.run_uid}-merged-{len(ordered)}",
        started_at=first.started_at,
        outcomes=tuple(outcomes),
        commit_sha=first.commit_sha,
        branch=first.branch,
        ci_run_id=first.ci_run_id,
        source_path=str(Path(first.source_path).parent) if first.source_path else None,
        runner=first.runner,
        iteration=iteration,
        seed=seed,
        duration=sum(durations) if durations else None,
    )


def _with_run_context(outcome: TestOutcome, run: TestRun) -> TestOutcome:
    """Denormalize run context onto an outcome, matching what storage returns.

    Without this the in-memory early-stop analysis would see no commit SHAs and
    disagree with the same analysis run against the database afterwards.
    """
    return TestOutcome(
        test_id=outcome.test_id,
        name=outcome.name,
        status=outcome.status,
        suite=outcome.suite,
        duration=outcome.duration,
        message=outcome.message,
        detail=outcome.detail,
        signature=outcome.signature,
        position=outcome.position,
        retried=outcome.retried,
        run_uid=run.run_uid,
        commit_sha=run.commit_sha,
        branch=run.branch,
        started_at=run.started_at,
        iteration=run.iteration,
    )


def _flaky_ids(outcomes: list[TestOutcome], config: Config) -> tuple[str, ...]:
    if not outcomes:
        return ()
    report = analyze(outcomes, config)
    return tuple(t.test_id for t in report.tests if t.verdict is Verdict.FLAKY)


def _replace_flakes(result: IterationResult, flakes: tuple[str, ...]) -> IterationResult:
    return IterationResult(
        iteration=result.iteration,
        exit_code=result.exit_code,
        duration=result.duration,
        seed=result.seed,
        run=result.run,
        error=result.error,
        new_flakes=flakes,
    )


def _match_spec(argv: Sequence[str]) -> _RunnerSpec | None:
    """Identify the runner from the command line.

    Only the tokens before the first `--` are considered, so that a marker
    appearing in the wrapped command's own arguments does not cause a mismatch.
    """
    head: list[str] = []
    for arg in argv:
        if arg == "--":
            break
        head.append(arg.lower())

    haystack = " ".join(head)
    for spec in _SPECS:
        if any(marker in haystack for marker in spec.argv_markers):
            return spec
    return None


def _probe_shuffle(spec: _RunnerSpec, argv: Sequence[str], cwd: str | Path | None) -> str | None:
    """Ask the command itself which randomization flags it supports.

    A real capability probe rather than a version guess, because order
    randomization usually arrives via an optional plugin (pytest-randomly,
    pytest-random-order) whose presence cannot be inferred from the runner name.
    """
    if not spec.shuffle_flags:
        return None

    help_text = _help_output(argv, cwd)
    if help_text is None:
        return None

    for token, template in spec.shuffle_flags:
        if token in help_text:
            return template
    return None


def _help_output(argv: Sequence[str], cwd: str | Path | None) -> str | None:
    """Run the command with --help and return whatever it prints.

    The *whole* command is used, not just its first token. `python -m pytest` is a
    very common invocation, and `python --help` says nothing about pytest's flags,
    so probing argv[0] alone would report that randomization is unavailable in one
    of the most common setups there is.
    """
    head: list[str] = []
    for arg in argv:
        if arg == "--":
            break
        head.append(arg)

    if not head:
        return None
    if shutil.which(head[0]) is None and not Path(head[0]).exists():
        return None

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            [*head, "--help"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=HELP_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return (completed.stdout or "") + (completed.stderr or "")


def _shuffle_hint(runner: str) -> str:
    hints = {
        "pytest": "Install pytest-randomly to enable it.",
        "jest": "Jest 29 or newer supports --shuffle.",
        "vitest": "Vitest supports --sequence.shuffle.",
        "go": "Go 1.17 or newer supports -shuffle=on.",
    }
    return hints.get(runner, "")
