"""Failure clustering by normalized signature.

One root cause frequently produces dozens of failures across unrelated tests: a
flaky fixture, a shared container that is slow to start, a service that returns
503 under load. Reported per test, that looks like 40 problems. Reported per
signature, it is one problem with 40 symptoms, which is both truer and much
cheaper to fix.

Clusters are ranked by how many distinct tests they touch rather than by raw
failure count, because a cause that breaks 30 tests once is a bigger deal than one
that breaks a single test 30 times. The single-test case is already visible in the
per-test ranking.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import FailureCluster, TestOutcome
from .classify import classify


def cluster_failures(
    outcomes: list[TestOutcome], *, min_size: int = 1
) -> tuple[FailureCluster, ...]:
    """Group failures by signature.

    Retried outcomes are included even though their recorded status is a pass: the
    runner observed a real failure and its message is real diagnostic data.
    """
    grouped: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in outcomes:
        if not outcome.signature:
            continue
        if outcome.status.is_failure or outcome.retried:
            grouped[outcome.signature].append(outcome)

    clusters: list[FailureCluster] = []
    for signature, members in grouped.items():
        test_ids = tuple(sorted({m.test_id for m in members}))
        if len(test_ids) < min_size:
            continue

        messages = [m.message for m in members if m.message]
        clusters.append(
            FailureCluster(
                signature=signature,
                representative_message=messages[0] if messages else signature,
                test_ids=test_ids,
                failure_count=len(members),
                cause=classify(messages) if messages else None,
            )
        )

    # Explicit tiebreaker on signature so equal-ranked clusters do not reorder
    # between runs and make report diffs noisy.
    clusters.sort(key=lambda c: (-c.test_count, -c.failure_count, c.signature))
    return tuple(clusters)
