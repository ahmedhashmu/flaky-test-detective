"""Failure-message scrubbing and signature computation.

Two superficially different failure messages often share one root cause. The
difference is usually a memory address, a temp path, a port, or a timestamp.
Stripping those turns "40 unrelated failures" into "one bug hit 40 times", which
is the difference between an unusable report and an actionable one.

Substitutions are ordered, most specific first: a UUID would otherwise be partly
eaten by the hex-address rule, and a duration would be eaten by the integer rule.

Imports nothing else from the package.
"""

from __future__ import annotations

import re

SIGNATURE_MAX_LENGTH = 500
"""Stack traces make poor cluster keys past the first few frames."""

_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Most specific first. Reordering this tuple changes clustering behaviour.
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<UUID>",
    ),
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        ),
        "<TIMESTAMP>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>),]+"), "<URL>"),
    # Must precede the bare-integer rule, which would otherwise chew the first
    # octet of an address and leave "<NUM>.0.0.1".
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    (re.compile(r"\b0[xX][0-9a-fA-F]{4,}\b"), "<ADDR>"),
    # Temp paths must precede the general path rule; they are the noisiest source
    # of spurious signature variation because every run gets a fresh one.
    (
        re.compile(
            r"(?:/private)?/(?:tmp|var/folders|var/tmp)/[^\s'\"<>),;]*",
            re.IGNORECASE,
        ),
        "<TMP>",
    ),
    (re.compile(r"[A-Za-z]:\\+(?:Users\\+[^\\\s]+\\+)?AppData\\+Local\\+Temp[^\s'\"<>),;]*"), "<TMP>"),
    (re.compile(r"[A-Za-z]:\\+(?:[\w.\-+@]+\\+)+[\w.\-+@]*"), "<PATH>"),
    (re.compile(r"(?<![\w<])/(?:[\w.\-+@]+/)+[\w.\-+@]*"), "<PATH>"),
    # Source locations must be handled before host:port, because `store_test.go:41`
    # otherwise reads as a hostname with a port and the line number becomes <PORT>.
    # An explicit extension list rather than a wildcard, so that `example.com:8080`
    # is still recognized as a host and not as a source file.
    (
        re.compile(
            r"(\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|go|java|kt|kts|rb|rs|cs|php|c|cc|cpp"
            r"|h|hpp|swift|scala|sh|pl|lua|dart|ex|exs|vue|svelte)):\d+(?::\d+)?\b"
        ),
        r"\1:<N>",
    ),
    (
        re.compile(
            r"(localhost|<IP>|[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}):\d{1,5}\b"
        ),
        r"\1:<PORT>",
    ),
    (re.compile(r"\bport\s+\d{1,5}\b", re.IGNORECASE), "port <PORT>"),
    (re.compile(r"\b(line|lineno|row|col|column|offset)\s*[:=]?\s*\d+\b", re.IGNORECASE), r"\1 <N>"),
    (
        re.compile(
            r"\b\d+(?:\.\d+)?\s*(?:ms|us|\u00b5s|ns|sec|secs|second|seconds|minutes?|hours?|s)\b"
        ),
        "<DURATION>",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    # Collections of numbers, before the bare-integer rule. A message like
    # "sample [3, 8, 14] did not include 0" varies on every run while describing
    # one bug, and leaving the list intact splits it into a fresh cluster each time.
    (re.compile(r"[\[(]\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+\s*[\])]"), "[<NUMS>]"),
    # Same reasoning for lists of quoted strings: "order was ['beta', 'alpha']"
    # describes one bug but reads as a new signature on every run.
    (
        re.compile(
            r"""[\[(]\s*(['"])[^'"]*\1(?:\s*,\s*(['"])[^'"]*\2)+\s*[\])]"""
        ),
        "[<LIST>]",
    ),
    (re.compile(r"\b\d{3,}\b"), "<NUM>"),
    (re.compile(r"\s+"), " "),
)

_PARAM_BLOCK = re.compile(r"\[([^\]]*)\]")
_NOISE_IN_PARAM = re.compile(
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|0[xX][0-9a-fA-F]{4,}"
    r"|\d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2}:?\d{0,2}"
    r"|\b\d{6,}\b)"
)

_EXCEPTION_LINE = re.compile(
    r"^\s*(?:E\s+)?[\w.$]*(?:Error|Exception|Failure|Timeout|Panic|Fault|Violation)\b\s*:.*$",
    re.MULTILINE,
)
"""A line naming an exception type.

Deliberately permissive on the prefix: this has to catch bare `Error:` from Node,
`java.lang.AssertionError:` from the JVM, and pytest's `E   AssertionError:`
gutter-marked lines, which are all the same thing wearing different clothes.
"""


def normalize_message(message: str | None) -> str:
    """Reduce a failure message to a stable cluster key.

    Deliberately lossy. Numbers, paths, addresses and timings are removed because
    they vary run to run without changing the cause. This means the result is not
    suitable for root-cause classification, which needs values like HTTP status
    codes -- classification runs against the raw message instead.
    """
    if not message:
        return ""

    text = message.strip()
    for pattern, replacement in _SUBSTITUTIONS:
        text = pattern.sub(replacement, text)

    text = text.strip()
    if len(text) > SIGNATURE_MAX_LENGTH:
        text = text[:SIGNATURE_MAX_LENGTH].rstrip() + "..."
    return text


def signature_of(message: str | None, detail: str | None = None) -> str:
    """Compute the clustering signature for a failure.

    Prefers the message, since runners put the assertion summary there. Falls
    back to the most informative line of the detail block: the exception line if
    one is present, otherwise the last non-empty line, which for a traceback is
    where the actual error appears.
    """
    source = (message or "").strip()
    if not source:
        source = salient_line(detail)
    if not source:
        return ""

    first_line = source.splitlines()[0] if source else ""
    normalized = normalize_message(first_line or source)
    return normalized or normalize_message(source)


def normalize_test_id(test_id: str) -> str:
    """Stabilize a test identifier across runs.

    History is keyed on this value, so an id that varies run to run fragments a
    test's history and makes it undetectable. Parameterized ids are the usual
    culprit: pytest embeds the parameter repr, and a parameter holding a temp path
    or a UUID produces a fresh id every run.

    Only the inside of bracketed parameter blocks is scrubbed. Scrubbing the whole
    id would collapse genuinely distinct tests whose names contain digits.
    """
    if not test_id:
        return test_id

    def _scrub(match: re.Match[str]) -> str:
        return "[" + _NOISE_IN_PARAM.sub("<X>", match.group(1)) + "]"

    return _PARAM_BLOCK.sub(_scrub, test_id).strip()


def salient_line(detail: str | None) -> str:
    """Pick the line of a traceback most likely to identify the cause.

    Prefers a line naming an exception type. Otherwise takes the last non-empty
    line, which is where the actual error sits in a Python traceback and in most
    Go test output.
    """
    if not detail:
        return ""

    match = _EXCEPTION_LINE.search(detail)
    if match:
        # Strip pytest's "E   " gutter marker so the line clusters with the same
        # exception reported by a runner that does not use one.
        return re.sub(r"^E\s+", "", match.group(0).strip())

    # No exception line. Fall back to the last non-empty line, which is where the
    # error sits in Go test output and in a bare Python traceback. The first line
    # would be a header ("WARNING: DATA RACE") rather than the cause.
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    return lines[-1] if lines else ""



