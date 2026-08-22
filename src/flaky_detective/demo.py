"""Build a populated history in one command, so the tool can be seen working immediately.

The problem this solves is the first five minutes. Every interesting thing this tool does
needs history across many runs, and history takes time to accumulate, so the honest
default experience is: install it, run it, see an empty database, and read documentation.
Anyone evaluating the tool gives up before reaching the part that works.

## Where the data comes from, and why it is not a fixture

The obvious shortcut is a checked-in JSON file of pretty results. That would be a lie
about the tool: nothing would have been detected, and the verdicts on screen would be
whatever was typed into the fixture.

Instead this reuses `benchmark/generate.py` -- the same labelled populations the accuracy
benchmark scores itself against -- writes them into a real database as real runs, and lets
the real `analyze()` reach its own conclusions. Every verdict on the demo dashboard was
produced by the shipped detector from recorded outcomes. If the detector were broken, the
demo would show it.

What is synthetic is the *history*: these test executions did not happen, they were
generated from known labels. That distinction is material, so `DEMO_RUNNER` is stamped on
every run and the dashboard surfaces a caveat from it. A demo that reads as real results
and turns out not to be would cost more credibility than it buys attention.

Sits beside `runner.py` as a producer: it writes into storage and is not called by the
analysis pipeline.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, replace

from .benchmark.generate import Population, generate_population
from .models import DEMO_RUNNER, TestOutcome, TestRun
from .storage import Storage

DEFAULT_SEED = 4242
DEFAULT_RUNS = 24
"""Enough history for same-commit divergence to be observable and for the confidence
factor to reach 1.0 at the default `confidence_runs` of 10, without making the demo slow
to build or the timeline unreadable."""

DEMO_BRANCH = "main"

# A smaller, curated population than the benchmark's default hundred tests. The dashboard
# is an investigation interface, and a ranked table of a hundred rows is a spreadsheet.
# Every category the tool can report is present so nothing on the dashboard is empty:
# flakes across a range of rates, a real regression, a test that never passed, a test that
# was flaky and recovered, and an order-dependent victim with its polluter.
DEMO_POPULATION = {
    "flaky": 6,
    "stable": 12,
    "broken": 1,
    "regression": 1,
    "fixed": 1,
    "order_dependent": 2,
}

# The generator names tests after their label: `test_flaky_4_p70`, `test_victim_0`. That is
# right for a benchmark and wrong for a demo, for two reasons. It prints the answer key on
# the dashboard, so a reader sees the verdict next to the ground truth and learns nothing
# about whether the detector found it. And a suite where every test is called "flaky" makes
# the screen harder to read, not more honest -- the honesty lives in the caveat banner and
# in `flaky demo` saying plainly that the history is generated.
#
# So the ids are relabelled to something a real suite would contain. Deterministic, and it
# changes nothing about the outcomes: the same passes and failures at the same commits.
_STABLE_NAMES = (
    "tests/test_auth.py::test_token_round_trips",
    "tests/test_auth.py::test_expired_token_is_rejected",
    "tests/test_cart.py::test_empty_cart_totals_zero",
    "tests/test_cart.py::test_line_items_sum",
    "tests/test_catalog.py::test_search_by_sku",
    "tests/test_catalog.py::test_missing_sku_returns_404",
    "tests/test_invoice.py::test_renders_line_items",
    "tests/test_invoice.py::test_applies_discount",
    "tests/test_shipping.py::test_flat_rate_domestic",
    "tests/test_shipping.py::test_rejects_unsupported_country",
    "tests/test_users.py::test_create_and_fetch",
    "tests/test_users.py::test_email_is_unique",
    "tests/test_users.py::test_password_is_hashed",
    "tests/test_webhooks.py::test_signature_is_verified",
)

_FLAKY_NAMES = (
    "tests/test_worker.py::test_worker_finishes_within_deadline",
    "tests/test_payments.py::test_refund_settles",
    "tests/test_search.py::test_results_are_ranked",
    "tests/test_queue.py::test_consumer_drains_the_queue",
    "tests/test_upload.py::test_large_upload_completes",
    "tests/test_notify.py::test_email_is_delivered",
    "tests/test_report.py::test_export_finishes",
    "tests/test_sync.py::test_replica_catches_up",
)

_BROKEN_NAMES = ("tests/test_plugins.py::test_optional_backend_is_importable",)
_REGRESSION_NAMES = ("tests/test_checkout.py::test_total_includes_tax",)
_FIXED_NAMES = ("tests/test_cache.py::test_warm_cache_is_hit",)

# Named so the relationship is legible on the investigation page: a reader should be able
# to guess the shape of the bug from the two names before reading the evidence.
_ORDER_PAIRS = (
    (
        "tests/test_registry.py::test_expects_clean_registry",
        "tests/test_registry.py::test_registers_session",
    ),
    (
        "tests/test_settings.py::test_reads_default_currency",
        "tests/test_settings.py::test_sets_global_currency",
    ),
)

_MIN_RUN_SECONDS = 38.0
_MAX_RUN_SECONDS = 96.0
"""Plausible suite durations, so the wasted-CI-time estimate has something to work from.

Synthetic like the rest of the history. The estimate is labelled as an estimate wherever
it appears, and the demo caveat says the whole database is generated.
"""


@dataclass(frozen=True, slots=True)
class DemoSummary:
    """What was written, for the command to report back."""

    runs: int
    results: int
    tests: int
    seed: int
    path: str

    @property
    def is_empty(self) -> bool:
        return self.runs == 0


class DemoError(RuntimeError):
    """The target database is not a safe place to write demo data."""


def contains_real_history(store: Storage) -> bool:
    """Does this database hold runs that did not come from `flaky demo`?

    Checked before writing, because the alternative is a command that can quietly bury a
    team's accumulated history under generated data. `flaky demo` pointed at the wrong
    `--db` should refuse, not apologise afterwards.
    """
    stats = store.stats()
    return any(name != DEMO_RUNNER for name in stats.runners)


def build(
    store: Storage,
    *,
    seed: int = DEFAULT_SEED,
    runs: int = DEFAULT_RUNS,
    population: dict[str, int] | None = None,
) -> DemoSummary:
    """Generate a labelled population and record it as real runs.

    Deterministic from `seed`: the same seed produces the same database, so a screenshot
    or a walkthrough can be reproduced exactly.
    """
    generated = _relabel(
        generate_population(
            seed=seed,
            runs=runs,
            commit_coverage=1.0,
            runs_per_commit=2,
            **(population or DEMO_POPULATION),
        )
    )

    added = 0
    results = 0
    for run in _as_runs(generated, seed):
        _, inserted = store.add_run(run)
        if inserted:
            added += 1
            results += len(run.outcomes)

    return DemoSummary(
        runs=added,
        results=results,
        tests=len(generated.truths),
        seed=seed,
        path=str(store.path),
    )


def _relabel(generated: Population) -> Population:
    """Rename generated tests to something a real suite would contain.

    Only the identifiers change. Every status, commit, position and timestamp is untouched,
    so the detector sees exactly the population the generator built and reaches exactly the
    same verdicts. Deterministic: ids are mapped in sorted order.
    """
    mapping = _name_mapping(sorted(generated.truths))

    outcomes = [
        replace(
            outcome,
            test_id=mapping.get(outcome.test_id, outcome.test_id),
            name=mapping.get(outcome.test_id, outcome.test_id).rsplit("::", 1)[-1],
        )
        for outcome in generated.outcomes
    ]

    truths = {}
    for old_id, truth in generated.truths.items():
        new_id = mapping.get(old_id, old_id)
        truths[new_id] = replace(
            truth,
            test_id=new_id,
            polluter=mapping.get(truth.polluter, truth.polluter) if truth.polluter else None,
        )

    return replace(generated, outcomes=outcomes, truths=truths)


def _name_mapping(test_ids: list[str]) -> dict[str, str]:
    """Build old id -> plausible id, keeping victim and polluter pairs together."""
    pools: dict[str, tuple[str, ...]] = {
        "test_stable.py": _STABLE_NAMES,
        "test_flaky.py": _FLAKY_NAMES,
        "test_broken.py": _BROKEN_NAMES,
        "test_regression.py": _REGRESSION_NAMES,
        "test_fixed.py": _FIXED_NAMES,
    }
    used: dict[str, int] = dict.fromkeys(pools, 0)
    mapping: dict[str, str] = {}

    for test_id in test_ids:
        if "test_order.py" in test_id:
            continue
        for marker, pool in pools.items():
            if marker not in test_id:
                continue
            index = used[marker]
            used[marker] = index + 1
            # Past the end of a pool, fall back to a suffixed name rather than repeating
            # one: two tests sharing an id would silently merge in analysis.
            mapping[test_id] = (
                pool[index] if index < len(pool) else f"{pool[index % len(pool)]}_{index}"
            )
            break

    # Order-dependent tests come in victim/polluter pairs sharing an index, so they are
    # mapped together to keep the story on the investigation page coherent.
    victims = sorted(t for t in test_ids if "test_victim_" in t)
    for position, victim in enumerate(victims):
        suffix = victim.rsplit("_", 1)[-1]
        polluter = victim.replace(f"test_victim_{suffix}", f"test_polluter_{suffix}")
        victim_name, polluter_name = _ORDER_PAIRS[position % len(_ORDER_PAIRS)]
        if position >= len(_ORDER_PAIRS):
            victim_name = f"{victim_name}_{position}"
            polluter_name = f"{polluter_name}_{position}"
        mapping[victim] = victim_name
        mapping[polluter] = polluter_name

    return mapping


def _as_runs(generated: Population, seed: int) -> list[TestRun]:
    """Group generated outcomes into runs, in chronological order.

    The generator produces a flat list of outcomes carrying a `run_uid`, because analysis
    consumes them that way. Storage takes runs, so they are regrouped here rather than
    changing a generator the benchmark depends on.
    """
    grouped: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in generated.outcomes:
        grouped[outcome.run_uid or ""].append(outcome)

    # Durations are drawn from a separate stream seeded off the population seed, so adding
    # them cannot shift the outcomes themselves and a given seed keeps producing the same
    # database.
    rng = random.Random(seed + 1)  # noqa: S311 - reproducibility, not secrecy

    runs: list[TestRun] = []
    for iteration, run_uid in enumerate(sorted(grouped), start=1):
        outcomes = sorted(grouped[run_uid], key=lambda o: (o.position or 0, o.test_id))
        first = outcomes[0]
        runs.append(
            TestRun(
                run_uid=run_uid,
                started_at=first.started_at or "",
                outcomes=tuple(outcomes),
                commit_sha=first.commit_sha,
                branch=DEMO_BRANCH,
                runner=DEMO_RUNNER,
                iteration=iteration,
                seed=str(seed),
                duration=round(rng.uniform(_MIN_RUN_SECONDS, _MAX_RUN_SECONDS), 1),
            )
        )

    return runs


__all__ = [
    "DEFAULT_RUNS",
    "DEFAULT_SEED",
    "DEMO_POPULATION",
    "DemoError",
    "DemoSummary",
    "build",
    "contains_real_history",
]
