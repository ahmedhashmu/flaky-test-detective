"""Turn a flaky test into a command that fails on demand.

Detection is where most tools stop. It is not where the work stops. "`test_upload` is
order dependent, 11 of 14 failures had `test_seeds_cache` somewhere before them" is a
lead, and acting on it still means guessing which of the forty tests that ran before it
mattered, in what combination.

This module closes that gap by *experiment* rather than by inference. It runs the real
suite against candidate subsets and reduces them, so the output is not a correlation but
a reproduction: a literal shell command, and the measured rate at which it fails.

Two pieces, deliberately separated:

- `ddmin` is Zeller's delta-debugging minimization, pure, taking an oracle callable. It
  knows nothing about tests or subprocesses and is tested with a fake oracle, so its
  behaviour is pinned without spending thousands of suite runs.
- `run_trials` is the real oracle: it executes the suite with an explicit test list and
  reads JUnit XML to see what happened, the same XML path ingestion uses.

Everything here compares against a **measured control**. A test that fails on its own
one time in five will "reproduce" under any prefix you hand it, and a search that does
not first establish how often it fails alone will happily blame whichever subset it
happened to be holding. So the victim is run by itself first, and every later result has
to beat that rate on an exact binomial tail before it counts as reproduction.

Scope, stated plainly: this is pytest-only. Reproduction needs the runner to accept an
ordered, explicit list of tests, and to honour that order. pytest does. Support for
others is a matter of one spec each, but claiming it before measuring it would be exactly
the kind of unbacked claim this project avoids.
"""

from __future__ import annotations

import atexit
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .analysis.statistics import tail_at_least, upper_bound
from .ingest import junit
from .ingest.junit import ParseError
from .models import ReproduceOutcome, Reproduction, Status

DEFAULT_TRIALS = 20
"""Trials used to confirm the final sequence.

The number that appears in the output, so it should be large enough that the rate means
something. Twenty gives a 5% resolution.
"""

DEFAULT_SEARCH_TRIALS = 6
"""Trials used for each oracle call *during* the search.

Delta debugging makes O(n log n) oracle calls. At twenty trials each, minimizing forty
candidates would run the suite thousands of times and nobody would wait for it. So the
search runs cheap batches and the final sequence is re-measured at full `trials`, which
is what stops a lucky reduction becoming a published rate.

Six rather than three, and the reason is measured rather than preferred. Because a batch
has to beat the *upper bound* on the control rate (see `_beats_control`), the number of
trials sets a floor on how often a sequence must fail to be detectable at all. Against a
clean control of 0/20, whose bound is 13.9%:

| Search trials | Failures needed | Which is a rate of |
|---|---:|---:|
| 3 | 3 | **100%** |
| 4 | 3 | 75% |
| 5 | 3 | 60% |
| **6** | **3** | **50%** |
| 8 | 4 | 50% |

At three, the search can only ever clear on a sequence that fails *every single time* --
so the tool would find deterministic order dependence and nothing else, while appearing
to search for the general case. Six reaches 50% for the same three failures, which is
the cheapest point on that table where a merely-frequent dependence is findable.

The threshold moves with the control size, which is correct: fewer control runs means a
wider bound and a higher bar. At `-n 12` the same six trials need 4 failures, not 3.
"""

DEFAULT_CANDIDATES = 40
"""Cap on predecessors fed into the search.

Not an accuracy limit so much as a patience limit: cost is roughly linear in this number.
The candidates are ordered nearest-first by the ordering index, so the cap keeps the
tests most likely to matter.
"""

DEFAULT_BUDGET = 400
"""Ceiling on oracle calls, so a pathological search cannot run until morning."""

DEFAULT_TRIAL_TIMEOUT = 600

ALPHA = 0.05
"""Significance level a candidate sequence must clear against the control rate."""

_ORDER_PRESERVING_ARGS = ("-p", "no:randomly", "-p", "no:random_order")
"""Randomization plugins are disabled for reproduction runs.

Not a preference. The entire output is an ordered sequence of tests, and pytest-randomly
installs itself as active-by-default, so leaving it on would shuffle the very sequence
being measured and the printed command would not reproduce anything.
"""

_PYTEST_MARKERS = ("pytest", "py.test")


class ReproduceError(RuntimeError):
    """The search cannot start. A usage error, raised before anything runs."""


@dataclass(frozen=True, slots=True)
class TrialBatch:
    """The result of running one candidate sequence some number of times."""

    failures: int
    trials: int
    missing: int = 0
    """Trials where the victim did not run at all.

    Counted separately and never as a pass. A node id that no longer exists, or a
    collection error caused by the subset, would otherwise look like evidence of
    stability and end the search with a confident wrong answer.
    """

    error: str | None = None


Oracle = Callable[[Sequence[str]], bool]
"""Given tests to run before the victim, did the victim fail more than control explains?"""


@dataclass(frozen=True, slots=True)
class DeltaResult:
    """What minimization achieved."""

    subset: tuple[str, ...]
    calls: int
    exhausted: bool = False


def ddmin(
    candidates: Sequence[str],
    oracle: Oracle,
    *,
    budget: int = DEFAULT_BUDGET,
) -> DeltaResult:
    """Reduce `candidates` to a locally minimal subset the oracle still accepts.

    Zeller and Hildebrandt's ddmin. The caller must have already established that the
    full set is accepted; ddmin only ever shrinks.

    "Locally minimal" is the honest guarantee and worth stating: removing any single
    chunk at the granularity reached fails, but the result is not proven to be the
    smallest reproducing set in the universe. Proving that costs exponential time. In
        practice the reduction is from tens of tests to one or two, which is the
    difference between a lead and an answer.

    Deterministic: chunks are examined in index order, so the same inputs and the same
    oracle always produce the same subset.
    """
    subset = list(candidates)
    calls = 0
    granularity = 2

    while len(subset) >= 2:
        chunks = _split(subset, min(granularity, len(subset)))
        reduced = False

        for chunk in chunks:
            if calls >= budget:
                return DeltaResult(tuple(subset), calls, exhausted=True)
            calls += 1
            if oracle(chunk):
                subset = chunk
                granularity = 2
                reduced = True
                break

        if not reduced and len(chunks) > 2:
            for chunk in chunks:
                complement = [item for item in subset if item not in chunk]
                if not complement:
                    continue
                if calls >= budget:
                    return DeltaResult(tuple(subset), calls, exhausted=True)
                calls += 1
                if oracle(complement):
                    subset = complement
                    granularity = max(granularity - 1, 2)
                    reduced = True
                    break

        if not reduced:
            if granularity >= len(subset):
                break
            granularity = min(len(subset), granularity * 2)

    return DeltaResult(tuple(subset), calls)


def _split(items: Sequence[str], parts: int) -> list[list[str]]:
    """Divide into `parts` near-equal contiguous chunks, preserving order.

    Order is preserved because the candidates are execution order. A reproduction that
    reorders them is describing a different experiment from the one that was observed.
    """
    if parts <= 1:
        return [list(items)]
    size = math.ceil(len(items) / parts)
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def reproduce(
    test_id: str,
    command: Sequence[str],
    candidates: Sequence[str],
    *,
    trials: int = DEFAULT_TRIALS,
    search_trials: int = DEFAULT_SEARCH_TRIALS,
    budget: int = DEFAULT_BUDGET,
    alpha: float = ALPHA,
    runner: Callable[[Sequence[str], int], TrialBatch] | None = None,
    cwd: Path | None = None,
    timeout: int = DEFAULT_TRIAL_TIMEOUT,
    progress: Callable[[str], None] | None = None,
) -> Reproduction:
    """Search for a sequence of tests that makes `test_id` fail, and measure it.

    `runner(sequence, trials)` executes the victim after `sequence` and reports how many
    times it failed. Injected rather than hardcoded so the search logic can be tested
    against a known-answer fake, which is the only affordable way to test it.
    """
    argv, stripped = _prepare_command(command)
    notes: list[str] = []
    if stripped:
        notes.append(
            "Test selection arguments were removed from the command so the sequence "
            f"could be set explicitly: {' '.join(stripped)}"
        )

    execute = runner or _subprocess_runner(argv, test_id, cwd=cwd, timeout=timeout)
    ordered = _dedupe(candidates, exclude=test_id)
    spent = 0

    def announce(message: str) -> None:
        if progress is not None:
            progress(message)

    announce(f"Measuring the control: {test_id} alone, {trials} times")
    control = execute((), trials)
    spent += control.trials
    if control.error and control.trials == 0:
        raise ReproduceError(f"Could not run the test command: {control.error}")
    if control.missing and control.missing == control.trials:
        raise ReproduceError(
            f"{test_id} did not run in any of {control.trials} attempts. "
            "Check the test id against the current source tree; it may have been "
            "renamed or removed since the history was recorded."
        )

    # The bound, not the observed rate. A clean control does not prove a zero rate, and
    # treating it as one is how an innocent test gets named. See `_beats_control`.
    control_bound = upper_bound(control.failures, control.trials, alpha)

    def accepts(sequence: Sequence[str]) -> bool:
        nonlocal spent
        batch = execute(sequence, search_trials)
        spent += batch.trials
        announce(f"  {len(sequence)} test(s) before it: {batch.failures}/{batch.trials} failed")
        return _beats_control(batch, control_bound, alpha)

    if not ordered:
        return _no_sequence(
            test_id,
            control,
            spent=spent,
            notes=notes,
            reason="No tests were recorded as running before it, so there is no "
            "ordering to isolate.",
        )

    announce(f"Checking all {len(ordered)} recorded predecessors together")
    if not accepts(ordered):
        return _no_sequence(
            test_id,
            control,
            spent=spent,
            notes=notes,
            candidates_started=len(ordered),
            oracle_calls=1,
            reason=(
                "Running every recorded predecessor before it did not make it fail more "
                "often than it does alone. Whatever makes it flaky is not the order of "
                "these tests."
            ),
        )

    announce(f"Reducing {len(ordered)} candidates by delta debugging")
    delta = ddmin(ordered, accepts, budget=budget)
    calls = delta.calls + 1

    announce(f"Confirming the reduced sequence over {trials} trials")
    confirm = execute(delta.subset, trials)
    spent += confirm.trials

    if not _beats_control(confirm, control_bound, alpha):
        return Reproduction(
            test_id=test_id,
            outcome=ReproduceOutcome.NOT_REPRODUCED,
            sequence=delta.subset,
            failures=confirm.failures,
            trials=confirm.trials,
            control_failures=control.failures,
            control_trials=control.trials,
            candidates_started=len(ordered),
            oracle_calls=calls,
            suite_runs=spent,
            command=_format_command(argv, delta.subset, test_id),
            explanation=_join(
                notes,
                f"A shorter sequence failed during the search but held up over "
                f"{confirm.trials} trials ({confirm.failures} failures against a control "
                f"of {control.failures}/{control.trials}). The reduction was luck, and "
                "reporting it as a reproducer would waste someone's afternoon.",
            ),
        )

    outcome = ReproduceOutcome.BUDGET_EXHAUSTED if delta.exhausted else ReproduceOutcome.REPRODUCED
    explanation = (
        f"{len(ordered)} candidates reduced to {len(delta.subset)} in {calls} suite "
        f"experiments. It fails {confirm.failures}/{confirm.trials} times in this order "
        f"and {control.failures}/{control.trials} times alone."
    )
    if delta.exhausted:
        explanation += (
            f" The {budget}-experiment budget ran out before reduction finished, so this "
            "sequence reproduces the failure but may not be minimal."
        )

    return Reproduction(
        test_id=test_id,
        outcome=outcome,
        sequence=delta.subset,
        failures=confirm.failures,
        trials=confirm.trials,
        control_failures=control.failures,
        control_trials=control.trials,
        candidates_started=len(ordered),
        oracle_calls=calls,
        suite_runs=spent,
        command=_format_command(argv, delta.subset, test_id),
        explanation=_join(notes, explanation),
    )


def _beats_control(batch: TrialBatch, control_bound: float, alpha: float) -> bool:
    """Whether this batch failed more than the victim's own solo rate explains.

    `control_bound` is the **upper** end of the confidence interval on the control rate,
    not the observed rate. That distinction is the whole point, and getting it wrong is
    the mistake [ADR-0012] already caught once in the branch comparison:

        A clean control is not proof of a zero rate. Zero failures in 20 runs still
        admits a true rate near 14%.

    Judging against the observed 0.0 meant any single failure was accepted as a
    reproduction, so a one-in-twenty flake could be attributed to whatever subset the
    search happened to be holding -- and the printed command would even appear to work
    when the reader ran it, because the test really does fail sometimes. Against the
    bound, one failure in twenty no longer clears the bar and the honest answer comes
    back instead.

    Consequence worth stating: at a small `search_trials` this requires the sequence to
    fail nearly every time during the search. That is why the default is 5 rather than 3
    -- see `DEFAULT_SEARCH_TRIALS`.
    """
    if batch.trials <= 0 or batch.failures <= 0:
        return False
    if control_bound <= 0.0:
        # Only reachable when the control was never run, which callers do not do.
        return True
    return tail_at_least(batch.failures, batch.trials, control_bound) <= alpha


def _no_sequence(
    test_id: str,
    control: TrialBatch,
    *,
    spent: int,
    notes: Sequence[str],
    reason: str,
    candidates_started: int = 0,
    oracle_calls: int = 0,
) -> Reproduction:
    """Report the two honest non-answers: it fails alone, or nothing tried made it fail."""
    fails_alone = control.failures > 0
    outcome = ReproduceOutcome.FAILS_ALONE if fails_alone else ReproduceOutcome.NOT_REPRODUCED
    if fails_alone:
        reason = (
            f"It fails {control.failures} times in {control.trials} runs on its own, so "
            "repetition is the reproducer and no ordering needs to be found. " + reason
        )
    return Reproduction(
        test_id=test_id,
        outcome=outcome,
        failures=control.failures,
        trials=control.trials,
        control_failures=control.failures,
        control_trials=control.trials,
        candidates_started=candidates_started,
        oracle_calls=oracle_calls,
        suite_runs=spent,
        command="",
        explanation=_join(notes, reason),
    )


def _join(notes: Sequence[str], tail: str) -> str:
    return " ".join([*notes, tail]).strip()


def _dedupe(candidates: Sequence[str], *, exclude: str) -> tuple[str, ...]:
    """Keep first occurrence, drop the victim, preserve order."""
    seen: set[str] = {exclude}
    ordered: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return tuple(ordered)


def _prepare_command(command: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the command into flags to keep and test selection to discard.

    The selection has to go. `pytest tests/ tests/test_a.py::test_b` collects all of
    `tests/` *and* that node id, so the sequence being measured would be buried inside a
    full suite run in collection order. Stripping it is visible in the output rather than
    silent, because a removed `-k` expression or a dropped path would otherwise change
    what the printed command means.
    """
    argv = [str(part) for part in command if str(part)]
    if not argv:
        raise ReproduceError("No command given. Try: flaky reproduce <test> -- pytest")

    head = " ".join(argv).lower()
    if not any(marker in head for marker in _PYTEST_MARKERS):
        raise ReproduceError(
            f"Reproduction is pytest-only for now, and {argv[0]!r} was not recognized.\n"
            "It needs a runner that accepts an ordered list of tests and honours that "
            "order.\n"
            "  flaky reproduce <test-id> -- pytest\n"
            "For other runners, `flaky investigate <test-id>` reports the correlation "
            "evidence without running anything."
        )

    kept: list[str] = [argv[0]]
    stripped: list[str] = []
    for arg in argv[1:]:
        if _is_selection(arg):
            stripped.append(arg)
        else:
            kept.append(arg)

    return tuple(kept), tuple(stripped)


def _is_selection(arg: str) -> bool:
    """Whether an argument names tests to run rather than configuring the run."""
    if arg.startswith("-"):
        return False
    return "::" in arg or arg.endswith(".py") or "/" in arg or os.sep in arg


def _format_command(argv: Sequence[str], sequence: Sequence[str], test_id: str) -> str:
    """The command a human can paste. The whole point of the module."""
    parts = [*argv, *_ORDER_PRESERVING_ARGS, *sequence, test_id]
    return " ".join(_quote(part) for part in parts)


def _quote(part: str) -> str:
    """Quote one argument for the shell the user is actually standing in.

    The printed command is this module's entire product, so quoting it for the wrong
    shell makes the output useless in exactly the case where it matters -- a path with a
    space in it. cmd.exe treats a single quote as a literal character, so the POSIX form
    would produce an invocation that fails with a confusing error about a file named
    `'C:\\My`.
    """
    if " " not in part:
        return part
    return f'"{part}"' if os.name == "nt" else f"'{part}'"


def _subprocess_runner(
    argv: Sequence[str],
    test_id: str,
    *,
    cwd: Path | None,
    timeout: int,
) -> Callable[[Sequence[str], int], TrialBatch]:
    """Build the real oracle: run the suite, read the XML, report what the victim did.

    Closes over the command and the victim so `reproduce` never has to know how a test is
    executed, and a test of the search never has to execute one. The victim is appended
    here rather than by the caller, so every code path measures the same thing: this
    sequence, then the test under investigation, last.
    """
    workdir = Path(tempfile.mkdtemp(prefix="flaky-reproduce-"))
    # Registered rather than removed inline: the directory has to outlive this function,
    # since the closure below writes into it on every trial. Without this, one directory
    # is left behind per invocation, which on a developer machine is a slow leak and in a
    # container is noise in the layer.
    atexit.register(shutil.rmtree, workdir, ignore_errors=True)
    report = workdir / "reproduce.xml"

    def execute(sequence: Sequence[str], trials: int) -> TrialBatch:
        failures = 0
        missing = 0
        errors: list[str] = []

        for _ in range(max(0, trials)):
            report.unlink(missing_ok=True)
            full = [
                *argv,
                *_ORDER_PRESERVING_ARGS,
                f"--junitxml={report}",
                *sequence,
                test_id,
            ]
            try:
                subprocess.run(  # noqa: S603 - argv list, shell=False
                    full,
                    cwd=str(cwd) if cwd else None,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                missing += 1
                errors.append(f"a trial exceeded the {timeout}s timeout")
                continue
            except (OSError, subprocess.SubprocessError) as exc:
                missing += 1
                errors.append(f"could not run {argv[0]!r}: {exc}")
                continue

            status = _victim_status(report, test_id)
            if status is None:
                missing += 1
            elif status is Status.FAILED or status is Status.ERROR:
                failures += 1

        return TrialBatch(
            failures=failures,
            trials=max(0, trials),
            missing=missing,
            error="; ".join(dict.fromkeys(errors)) or None,
        )

    return execute


def _victim_status(report: Path, test_id: str) -> Status | None:
    """Read the victim's status out of the report, or None if it did not run."""
    if not report.is_file():
        return None
    try:
        run = junit.parse_file(report)
    except ParseError:
        return None
    for outcome in run.outcomes:
        if outcome.test_id == test_id:
            return outcome.status
    return None


def check_command(command: Sequence[str], *, cwd: Path | None = None) -> str | None:
    """Confirm the command runs at all, returning a diagnostic if it does not.

    Run before the search so a typo costs a second rather than the full control batch.
    """
    argv, _ = _prepare_command(command)
    if shutil.which(argv[0]) is None and not Path(argv[0]).exists():
        return f"{argv[0]!r} was not found on PATH"
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            [*argv, "--version"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run {argv[0]!r}: {exc}"
    if completed.returncode != 0:
        return f"{argv[0]!r} --version exited {completed.returncode}"
    return None


def estimate_cost(candidates: int, *, trials: int, search_trials: int) -> int:
    """Rough number of suite executions the search will need.

    Printed before starting. Delta debugging on n candidates is around 2*log2(n) oracle
    calls when reduction goes well, plus the control batch and the confirmation. An
    estimate a user can decide against is worth more than a progress bar they cannot.
    """
    if candidates <= 0:
        return trials
    calls = max(1, 2 * math.ceil(math.log2(max(2, candidates))))
    return trials + (calls * search_trials) + trials


__all__ = [
    "ALPHA",
    "DEFAULT_BUDGET",
    "DEFAULT_CANDIDATES",
    "DEFAULT_SEARCH_TRIALS",
    "DEFAULT_TRIALS",
    "DeltaResult",
    "Oracle",
    "ReproduceError",
    "TrialBatch",
    "check_command",
    "ddmin",
    "estimate_cost",
    "reproduce",
]
