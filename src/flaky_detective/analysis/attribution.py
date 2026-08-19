"""When did this test become flaky?

Once a test is known to be flaky, the next question is which change made it that way.
The database records outcomes per commit, so the answer is often available.

The important part of this module is what it refuses to answer. A naive implementation
points at the earliest commit in the recorded window and calls it the culprit, which is
an accusation the data does not support and a reliable way to send someone reverting an
innocent change. Every genuinely unknowable case is reported as unknown instead, and
`Attribution` has a distinct value for each so the caller can explain which kind of
"don't know" it is.

Pure functions over lists of outcomes, like the rest of `analysis/`. The result types
live in `models` so that reporters can render them without importing anything from
here.
"""

from __future__ import annotations

from collections import OrderedDict

from ..models import Attribution, BlameResult, CommitWindow, TestOutcome


def blame(test_id: str, outcomes: list[TestOutcome]) -> BlameResult:
    """Find the earliest commit where a test started diverging.

    `outcomes` must be in chronological order, which is what storage returns.
    """
    timeline = build_timeline(outcomes)

    if not timeline:
        return BlameResult(test_id=test_id, attribution=Attribution.NO_COMMIT_DATA)

    observable = [window for window in timeline if window.observable]
    if not observable:
        return BlameResult(
            test_id=test_id,
            attribution=Attribution.TOO_SPARSE,
            timeline=tuple(timeline),
            observable_commits=0,
        )

    diverged_at = next((window for window in timeline if window.diverged), None)
    if diverged_at is None:
        return BlameResult(
            test_id=test_id,
            attribution=Attribution.NO_DIVERGENCE,
            timeline=tuple(timeline),
            observable_commits=len(observable),
        )

    # A commit can only be blamed if some earlier commit was observably clean. Without
    # one, the flakiness may well predate the window, and saying so is more honest than
    # naming the oldest commit that happens to be on hand.
    index = timeline.index(diverged_at)
    earlier_clean = [
        window for window in timeline[:index] if window.observable and not window.diverged
    ]

    if not earlier_clean:
        return BlameResult(
            test_id=test_id,
            attribution=Attribution.PREDATES_HISTORY,
            commit_sha=diverged_at.commit_sha,
            timeline=tuple(timeline),
            observable_commits=len(observable),
        )

    return BlameResult(
        test_id=test_id,
        attribution=Attribution.INTRODUCED,
        commit_sha=diverged_at.commit_sha,
        previous_clean_sha=earlier_clean[-1].commit_sha,
        timeline=tuple(timeline),
        observable_commits=len(observable),
    )


def build_timeline(outcomes: list[TestOutcome]) -> list[CommitWindow]:
    """Collapse outcomes into one window per commit, in first-seen order.

    An OrderedDict rather than sorting by timestamp: commits should appear in the order
    the test first ran on them, and timestamps within a single hunt frequently tie.
    """
    grouped: OrderedDict[str, list[TestOutcome]] = OrderedDict()
    for outcome in outcomes:
        if not outcome.commit_sha or not outcome.status.counts_as_evidence:
            continue
        grouped.setdefault(outcome.commit_sha, []).append(outcome)

    windows: list[CommitWindow] = []
    for commit_sha, group in grouped.items():
        passes = sum(1 for o in group if o.status.is_pass and not o.retried)
        failures = sum(1 for o in group if o.status.is_failure)
        # A runner-recorded retry means both outcomes happened inside one run, which is
        # divergence at this commit even though only one row was written for it.
        retried = sum(1 for o in group if o.retried)
        windows.append(
            CommitWindow(
                commit_sha=commit_sha,
                runs=len(group) + retried,
                passes=passes + retried,
                failures=failures + retried,
                first_seen=group[0].started_at,
            )
        )
    return windows
