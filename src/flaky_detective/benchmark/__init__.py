"""Measure the detector against data whose correct answer is known.

The point of this module is to replace "trust me" with a number. Before it existed,
every accuracy claim about this tool rested on one hand-built demo suite of sixteen
tests inspected by eye, and the thresholds in `analysis/` had been tuned by looking
at that same suite -- a sample size of one.

It runs the **real** analysis pipeline. If the harness and the tool ever disagree
about the population, that raises rather than being quietly scored, because a
benchmark measuring something other than the shipped code is worse than no
benchmark.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ..analysis import analyze
from ..config import Config
from .generate import FLAKE_RATES, GroundTruth, Population, Truth, generate_population
from .score import BenchmarkResult, LabelScore, score_predictions

if TYPE_CHECKING:
    from .score import LabelledTest

__all__ = [
    "FLAKE_RATES",
    "BenchmarkResult",
    "GroundTruth",
    "LabelScore",
    "Population",
    "Truth",
    "generate_population",
    "run_benchmark",
    "score_predictions",
    "sweep",
]

DEFAULT_SWEEP_RUNS = (5, 10, 20, 30, 50)
DEFAULT_SWEEP_COVERAGE = (0.0, 0.25, 0.5, 1.0)


def run_benchmark(
    *,
    seed: int = 1234,
    runs: int = 30,
    commit_coverage: float = 1.0,
    runs_per_commit: int = 2,
    config: Config | None = None,
    **population: int,
) -> BenchmarkResult:
    """Generate a labelled population, analyze it, and score the result."""
    settings = config or Config()
    generated = generate_population(
        seed=seed,
        runs=runs,
        commit_coverage=commit_coverage,
        runs_per_commit=runs_per_commit,
        **population,
    )

    report = analyze(generated.outcomes, settings)

    predictions = {test.test_id: test.verdict for test in report.tests}
    causes = {
        test.test_id: str(test.cause.cause) for test in report.tests if test.cause is not None
    }
    polluters = {
        test.test_id: test.order.likely_polluter for test in report.tests if test.order is not None
    }

    # cast: GroundTruth satisfies LabelledTest structurally, but mypy needs telling
    # because the Protocol lives in score.py and cannot import the generator.
    return score_predictions(
        cast("dict[str, LabelledTest]", generated.truths),
        predictions,
        causes=causes,
        polluters=polluters,
        runs=runs,
        commit_coverage=commit_coverage,
        seed=seed,
    )


def sweep(
    *,
    seed: int = 1234,
    over: str = "runs",
    values: tuple[float, ...] | None = None,
    config: Config | None = None,
    **kwargs: object,
) -> list[BenchmarkResult]:
    """Measure how accuracy responds to the amount of evidence available.

    Two axes are worth sweeping, and they answer different questions:

    - `runs`: how many runs before the tool is useful? Anyone adopting it wants to
      know how long until the numbers mean something.
    - `coverage`: how much worse is it without commit SHAs? The design claims
      same-commit divergence is the load-bearing signal, and this is where that claim
      either holds up or does not.
    """
    if over == "runs":
        chosen = values or DEFAULT_SWEEP_RUNS
        return [
            run_benchmark(seed=seed, runs=int(value), config=config, **kwargs)  # type: ignore[arg-type]
            for value in chosen
        ]

    if over == "coverage":
        chosen = values or DEFAULT_SWEEP_COVERAGE
        return [
            run_benchmark(seed=seed, commit_coverage=float(value), config=config, **kwargs)  # type: ignore[arg-type]
            for value in chosen
        ]

    raise ValueError(f"Cannot sweep over {over!r}. Use 'runs' or 'coverage'.")
