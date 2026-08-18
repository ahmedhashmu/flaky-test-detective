"""Flakiness scoring and verdict assignment.

The whole tool rests on one idea: **same code, different outcome** is the only
direct proof of flakiness available without reading source. Everything else is
inference, and the scoring weights say so.

Three signals, in descending order of how much they are trusted:

1. **Runner-recorded retry.** Surefire's `<flakyFailure>` is the runner stating
   that this test failed and then passed inside a single run. Not inference.
2. **Same-commit divergence.** One test, one commit SHA, both a pass and a fail.
   The code was identical, so the code is not the variable.
3. **Flip rate.** Pass/fail transitions over time. Suggestive, but a single
   pass -> fail transition is far more likely to be a regression than a flake, so
   this signal alone must never be enough to call something flaky with confidence.

Pure functions over lists of TestOutcome. No database, no filesystem, which is
what makes the scoring testable with constructed data.

Weights live at module level. Changing them changes every number the tool
reports, so they are in one place and the design doc records why.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..models import Status, TestAnalysis, TestOutcome, Verdict

DIVERGENCE_WEIGHT = 0.7
FLIP_WEIGHT = 0.3
"""Divergence outweighs flips because divergence is proof and flips are a hint."""

CONFIDENCE_FLOOR = 0.5
"""A strong signal on few runs still surfaces; it just cannot outrank the same
signal backed by many runs. Never zero, or new flakes would stay invisible."""

FLIP_ONLY_CEILING = 0.85
"""Highest score reachable when no run carried a commit SHA.

Without commit data, flip rate is all there is, and flip rate is inference: a test
that alternates pass and fail might be flaky, or the code might have changed
between every run. A test with same-commit divergence has proof that the code was
not the variable.

Capping the inference-only path keeps the two distinguishable. Otherwise a
perfectly alternating history with no commit data scores 1.00, exactly the same as
one backed by proof, and the score stops carrying information about how much the
verdict can be trusted.
"""

REGRESSION_STREAK = 3
"""Consecutive trailing failures after which a consistent failure is called a
regression rather than a flake, even for a test with flaky history.

Calling a real break "flaky" is the worst failure mode this tool has: it teaches
the user to re-run instead of investigate, which is the habit the tool exists to
break. When commit data is available, prefer regression.
"""

MAX_REGRESSION_FLIPS = 2
"""Flip ceiling for calling something a regression when no commit data exists.

A regression has one signature: a single pass -> fail transition, then silence. A
test that has flipped five times has demonstrated it can flip, so a trailing run
of three failures is much more likely to be bad luck than a new break.

This guard only applies when no run carried a commit SHA. With commit data,
`_latest_commit_diverged` answers the question directly and is trusted instead,
because it is evidence rather than inference.
"""


def analyze_test(
    test_id: str,
    outcomes: list[TestOutcome],
    *,
    threshold: float,
    confidence_runs: int,
    fixed_run_streak: int,
) -> TestAnalysis:
    """Score one test across its whole recorded history.

    `outcomes` must be in chronological order; flip counting depends on it. The
    storage layer returns them that way.
    """
    evidence = [o for o in outcomes if o.status.counts_as_evidence]
    passes = sum(1 for o in evidence if o.status.is_pass)
    failures = sum(1 for o in evidence if o.status.is_failure)
    skips = sum(1 for o in outcomes if o.status is Status.SKIPPED)
    retries = sum(1 for o in outcomes if o.retried)

    flips = _count_flips(evidence)
    flip_rate = flips / (len(evidence) - 1) if len(evidence) > 1 else 0.0

    divergent, observed = _commit_divergence(evidence)
    divergence_rate = divergent / observed if observed else 0.0

    runs = len(evidence)
    confidence = min(1.0, runs / confidence_runs) if confidence_runs > 0 else 1.0
    score = _score(
        divergence_rate=divergence_rate,
        flip_rate=flip_rate,
        retries=retries,
        runs=runs,
        observed_commits=observed,
        confidence=confidence,
    )

    verdict = _verdict(
        evidence=evidence,
        passes=passes,
        failures=failures,
        retries=retries,
        flips=flips,
        score=score,
        threshold=threshold,
        fixed_run_streak=fixed_run_streak,
    )

    failure_messages = [o.message for o in outcomes if (o.status.is_failure or o.retried)]
    signatures = _ranked_signatures(outcomes)
    sample = next((m for m in failure_messages if m), None)
    latest = evidence[-1] if evidence else None

    return TestAnalysis(
        test_id=test_id,
        name=outcomes[-1].name if outcomes else test_id,
        suite=outcomes[-1].suite if outcomes else None,
        verdict=verdict,
        score=round(score, 4),
        runs=runs,
        passes=passes,
        failures=failures,
        skips=skips,
        flips=flips,
        flip_rate=round(flip_rate, 4),
        divergent_commits=divergent,
        observed_commits=observed,
        divergence_rate=round(divergence_rate, 4),
        confidence=round(confidence, 4),
        retries=retries,
        first_seen=outcomes[0].started_at if outcomes else None,
        last_seen=outcomes[-1].started_at if outcomes else None,
        last_status=latest.status if latest else None,
        consecutive_passes=_trailing(evidence, passing=True),
        signatures=signatures,
        representative_message=sample,
    )


def _score(
    *,
    divergence_rate: float,
    flip_rate: float,
    retries: int,
    runs: int,
    observed_commits: int,
    confidence: float,
) -> float:
    """Combine the signals into [0, 1].

    When no run carried a commit SHA there is no divergence signal at all. Rather
    than silently scoring every test low, the weight renormalizes onto whatever
    proof is available: runner-recorded retries if any, otherwise flip rate alone.
    """
    retry_rate = retries / runs if runs else 0.0
    has_commit_signal = observed_commits > 0

    if has_commit_signal or retries:
        proof = max(divergence_rate, retry_rate) if has_commit_signal else retry_rate
        raw = DIVERGENCE_WEIGHT * proof + FLIP_WEIGHT * flip_rate
    else:
        raw = FLIP_ONLY_CEILING * flip_rate

    return min(1.0, raw * (CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * confidence))


def _count_flips(evidence: list[TestOutcome]) -> int:
    """Count pass <-> fail transitions in chronological order."""
    flips = 0
    previous: bool | None = None
    for outcome in evidence:
        current = outcome.status.is_failure
        if previous is not None and current != previous:
            flips += 1
        previous = current
    return flips


def _commit_divergence(evidence: list[TestOutcome]) -> tuple[int, int]:
    """Count commits where this test both passed and failed.

    Returns (divergent_commits, observed_commits). The denominator counts only
    commits where the test ran more than once, or where the runner recorded a
    retry. A commit with a single run cannot show divergence, and including it
    would dilute the rate with non-evidence.
    """
    by_commit: dict[str, list[TestOutcome]] = defaultdict(list)
    for outcome in evidence:
        if outcome.commit_sha:
            by_commit[outcome.commit_sha].append(outcome)

    divergent = observed = 0
    for group in by_commit.values():
        retried_here = any(o.retried for o in group)
        if len(group) < 2 and not retried_here:
            continue

        observed += 1
        saw_pass = any(o.status.is_pass for o in group)
        saw_failure = any(o.status.is_failure for o in group)
        if retried_here or (saw_pass and saw_failure):
            divergent += 1

    return divergent, observed


def _verdict(
    *,
    evidence: list[TestOutcome],
    passes: int,
    failures: int,
    retries: int,
    flips: int,
    score: float,
    threshold: float,
    fixed_run_streak: int,
) -> Verdict:
    """Assign exactly one verdict.

    Check order encodes the priorities: never-passed beats everything, then
    consistent recent failure, then recovery, then flakiness. Flaky is checked
    last on purpose, so that anything explainable as a real break is reported as
    one.
    """
    if not evidence:
        return Verdict.STABLE

    if failures == 0 and retries == 0:
        return Verdict.STABLE

    if passes == 0:
        # Never passed in recorded history. Usually an incomplete commit rather
        # than a break, and definitely not a flake.
        return Verdict.BROKEN

    if _is_regression(evidence, flips=flips):
        return Verdict.REGRESSION

    consecutive_passes = _trailing(evidence, passing=True)
    if consecutive_passes >= fixed_run_streak:
        return Verdict.FIXED

    if score >= threshold:
        return Verdict.FLAKY

    return Verdict.STABLE


def _is_regression(evidence: list[TestOutcome], *, flips: int) -> bool:
    """Is this a new, consistent break rather than a flake?

    Requires a trailing run of failures. Beyond that the answer depends on what
    evidence exists:

    - **With commit SHAs**, ask whether the newest commit still shows a pass. If it
      does, flakiness is the live explanation; if it shows only failures, the code
      is the more likely variable and this is a regression.
    - **Without commit SHAs** there is no way to separate "flaky and unlucky" from
      "newly broken", so fall back to shape: a regression flips once, a flake flips
      repeatedly.
    """
    if _trailing(evidence, passing=False) < REGRESSION_STREAK:
        return False

    if any(o.commit_sha for o in evidence):
        return not _latest_commit_diverged(evidence)

    return flips <= MAX_REGRESSION_FLIPS


def _latest_commit_diverged(evidence: list[TestOutcome]) -> bool:
    """Did the most recent commit show both a pass and a fail?

    This is what stops a long-standing flake that has now genuinely broken from
    being written off as "just flaky again". If the newest commit still shows
    divergence, flakiness is the live explanation; if it only shows failures, the
    code is the more likely variable.
    """
    latest_sha: str | None = None
    for outcome in reversed(evidence):
        if outcome.commit_sha:
            latest_sha = outcome.commit_sha
            break

    if latest_sha is None:
        return False

    group = [o for o in evidence if o.commit_sha == latest_sha]
    if any(o.retried for o in group):
        return True
    return any(o.status.is_pass for o in group) and any(o.status.is_failure for o in group)


def _trailing(evidence: list[TestOutcome], *, passing: bool) -> int:
    """Length of the run of passes (or failures) at the end of the sequence."""
    count = 0
    for outcome in reversed(evidence):
        matches = outcome.status.is_pass if passing else outcome.status.is_failure
        if not matches:
            break
        count += 1
    return count


def _ranked_signatures(outcomes: list[TestOutcome]) -> tuple[str, ...]:
    """Distinct failure signatures, most frequent first.

    More than one signature on a single test usually means more than one bug, and
    that is worth seeing rather than averaging away.
    """
    counter: Counter[str] = Counter(
        o.signature for o in outcomes if o.signature and (o.status.is_failure or o.retried)
    )
    return tuple(signature for signature, _ in counter.most_common())
