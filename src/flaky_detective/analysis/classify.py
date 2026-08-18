"""Heuristic root-cause classification.

These are guesses, and the report says so. The value is not certainty; it is
turning a flat list of 200 flaky tests into "30 of these are timeouts, that is
one afternoon's work". Every verdict carries the terms that produced it so a
human can disagree in one glance.

Classification runs against the **raw** failure message, not the normalized one.
Normalization deliberately destroys numbers, and some of those numbers are the
signal: `HTTP 503` normalizes to `HTTP <NUM>` and the network rule stops firing.

Adding a category means adding one entry to _RULES. That is the only place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Cause, CauseEvidence, OrderEvidence

ORDER_OVERRIDE_CONFIDENCE = 0.9
"""Order dependence is measured from run positions, not guessed from text, so it
outranks any message-pattern match."""


@dataclass(frozen=True, slots=True)
class _Rule:
    cause: Cause
    weight: float
    remediation: str
    patterns: tuple[re.Pattern[str], ...]


def _p(*expressions: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(e, re.IGNORECASE) for e in expressions)


_RULES: tuple[_Rule, ...] = (
    _Rule(
        cause=Cause.TIMEOUT,
        weight=1.0,
        remediation=(
            "Wait on the condition rather than on the clock. Raise the limit only "
            "after confirming the operation is genuinely slow."
        ),
        patterns=_p(
            r"\btimed?\s?out\b",
            r"\btimeouts?\b",
            r"deadline exceeded",
            r"\bETIMEDOUT\b",
            r"exceeded timeout",
            r"took too long",
            r"did not (?:finish|complete|respond|settle)",
            r"still (?:running|pending) after",
            r"\bwait(?:ing)? for\b",
        ),
    ),
    _Rule(
        cause=Cause.NETWORK,
        weight=1.0,
        remediation=(
            "Stub the network boundary. A test that reaches a real host is testing "
            "someone else's uptime."
        ),
        patterns=_p(
            r"connection (?:refused|reset|aborted|closed|error)",
            r"\bECONNREFUSED\b",
            r"\bECONNRESET\b",
            r"\bENOTFOUND\b",
            r"\bEHOSTUNREACH\b",
            r"\bEPIPE\b",
            r"\bgetaddrinfo\b",
            r"name or service not known",
            r"\bDNS\b",
            r"\bunreachable\b",
            r"\bsocket\b",
            r"HTTP\s*5\d{2}\b",
            r"\b(?:502|503|504)\b",
            r"bad gateway",
            r"service unavailable",
            r"gateway timeout",
            r"TLS handshake",
            r"\bSSL\b",
        ),
    ),
    _Rule(
        cause=Cause.RESOURCE,
        weight=1.0,
        remediation=(
            "Release the resource in teardown, or allocate a unique one per test: "
            "an ephemeral port, a fresh temp directory, its own database."
        ),
        patterns=_p(
            r"out of memory",
            r"\bOOM\b",
            r"cannot allocate",
            r"too many open files",
            r"\bEMFILE\b",
            r"\bENFILE\b",
            r"\bENOSPC\b",
            r"no space left",
            r"address already in use",
            r"\bEADDRINUSE\b",
            r"port .{0,20}(?:in use|unavailable|taken)",
            r"resource temporarily unavailable",
            r"\bEAGAIN\b",
            r"quota exceeded",
            r"connection pool (?:exhausted|timeout|full)",
            r"too many connections",
            r"file descriptor",
        ),
    ),
    _Rule(
        cause=Cause.RACE,
        weight=0.95,
        remediation=(
            "Serialize access to the shared resource, or give each test its own "
            "instance so there is nothing to contend over."
        ),
        patterns=_p(
            r"\bdata race\b",
            r"\brace condition\b",
            r"\bdeadlock\b",
            r"\bconcurren(?:t|tly|cy)\b",
            r"\bmutex\b",
            r"\bsemaphore\b",
            r"\bgoroutine\b",
            r"ConcurrentModification",
            r"\bthread\b",
            r"\block (?:is |was )?(?:held|contended|timeout)",
            r"event loop",
            r"unhandled (?:promise )?rejection",
            r"\bawait(?:ed|ing)? (?:never|nothing)",
            r"was not awaited",
            r"\batomic\b",
            r"\bstale (?:read|element|reference)\b",
        ),
    ),
    _Rule(
        cause=Cause.RANDOMNESS,
        weight=0.85,
        remediation=(
            "Seed the generator explicitly, or assert on the property you care "
            "about rather than an exact value."
        ),
        patterns=_p(
            r"\brandom(?:ly|ness)?\b",
            r"\bshuffle[ds]?\b",
            r"\bseed(?:ed)?\b",
            r"\bfaker\b",
            r"\bmimesis\b",
            r"\bhypothesis\b.*falsif",
            r"\bnondeterministic\b",
            r"\bnon-deterministic\b",
            r"arbitrary order",
            r"(?:dict|dictionary|map|set|hash) (?:ordering|order)",
            r"\bunordered\b",
        ),
    ),
    _Rule(
        cause=Cause.TIME_DEPENDENCE,
        weight=0.8,
        remediation=(
            "Inject a fixed clock instead of reading the system time, so the test "
            "does not depend on when it runs."
        ),
        patterns=_p(
            r"\btime ?zone\b",
            r"\bDST\b",
            r"daylight saving",
            r"\bUTC\b",
            r"\bmidnight\b",
            r"\bleap (?:year|second|day)\b",
            r"\bclock\b",
            r"\b(?:has |had )?expired\b",
            r"\btoday\b",
            r"\btomorrow\b",
            r"\byesterday\b",
            r"end of (?:month|quarter|year)",
            r"\bnow\(\)",
            r"\bdatetime\b",
            r"\btimestamp\b",
        ),
    ),
    _Rule(
        cause=Cause.ORDER_DEPENDENCE,
        weight=0.7,
        remediation=(
            "Reset the shared state in setup or teardown so the outcome does not "
            "depend on what ran before it."
        ),
        patterns=_p(
            r"already exists",
            r"already registered",
            r"already initiali[sz]ed",
            r"duplicate key",
            r"unique constraint",
            r"\btable .{0,40}already\b",
            r"state (?:leak|pollution|polluted|contaminated)",
            r"\bsingleton\b",
            r"cache (?:already |not )?(?:warm|populated|stale)",
            r"fixture .{0,30}(?:missing|not found|already)",
            r"\bteardown\b",
            r"\bleft ?over\b",
        ),
    ),
    _Rule(
        cause=Cause.ASSERTION,
        weight=0.25,
        remediation=(
            "Nothing environmental in the message. Diff a passing run against a "
            "failing one to find what actually differs."
        ),
        patterns=_p(
            r"\bassert(?:ion)?\b",
            r"\bexpected\b",
            r"\bAssertionError\b",
            r"\bto(?:Be|Equal|Match|Contain|Throw)\b",
            r"\bshould\b",
            r"but (?:was|got|received)",
            r"\bmismatch\b",
            r"\bnot equal\b",
        ),
    ),
)

_REMEDIATION_UNKNOWN = (
    "No recognizable signal in the failure message. `flaky history <test-id>` "
    "shows the raw failures side by side."
)

_MAX_MATCHES_SHOWN = 4


def classify(
    messages: list[str] | tuple[str, ...],
    order: OrderEvidence | None = None,
) -> CauseEvidence:
    """Guess why a test fails, from its failure messages and run positions.

    Order dependence, when detected, overrides the message rules: it is measured
    from where the test ran rather than inferred from what it printed, and a
    measurement beats a guess. The message-based runner-up is still folded into
    the evidence so the caller can see both readings.
    """
    if order is not None:
        matched = [f"position separation {order.separation:.1f}x"]
        if order.likely_polluter:
            matched.append(f"fails after {order.likely_polluter}")
        return CauseEvidence(
            cause=Cause.ORDER_DEPENDENCE,
            matched=tuple(matched),
            remediation=_remediation_for(Cause.ORDER_DEPENDENCE),
            confidence=ORDER_OVERRIDE_CONFIDENCE,
        )

    text = "\n".join(m for m in messages if m)
    if not text.strip():
        return CauseEvidence(cause=Cause.UNKNOWN, remediation=_REMEDIATION_UNKNOWN, confidence=0.0)

    best: CauseEvidence | None = None
    for rule in _RULES:
        matched = _matches(rule, text)
        if not matched:
            continue

        # More independent signals mean more confidence, with diminishing returns
        # so that a rule with many near-synonymous patterns cannot run away.
        score = rule.weight * min(1.0, 0.6 + 0.2 * len(matched))
        if best is None or score > best.confidence:
            best = CauseEvidence(
                cause=rule.cause,
                matched=tuple(matched[:_MAX_MATCHES_SHOWN]),
                remediation=rule.remediation,
                confidence=round(min(score, 1.0), 2),
            )

    if best is None:
        return CauseEvidence(cause=Cause.UNKNOWN, remediation=_REMEDIATION_UNKNOWN, confidence=0.0)
    return best


def _matches(rule: _Rule, text: str) -> list[str]:
    """Collect the distinct literal fragments that fired, for display."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in rule.patterns:
        match = pattern.search(text)
        if match is None:
            continue
        fragment = match.group(0).strip().lower()
        if fragment and fragment not in seen:
            seen.add(fragment)
            found.append(fragment)
    return found


def _remediation_for(cause: Cause) -> str:
    for rule in _RULES:
        if rule.cause is cause:
            return rule.remediation
    return _REMEDIATION_UNKNOWN


def remediation_for(cause: Cause) -> str:
    """Public accessor, used by reporters that show a cause without re-classifying."""
    return _remediation_for(cause)
