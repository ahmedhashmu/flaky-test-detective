"""JUnit XML parsing across runner dialects.

Every runner claims to emit "JUnit XML" and none of them agree. pytest writes a
flat `testsuite`, Maven nests them, jest puts the describe block in `classname`
and repeats it in `name`, go-junit-report uses the package path. This module
absorbs those differences so that nothing downstream has to know which runner
produced a report.

Reports are untrusted input: they arrive as CI artifacts and downloads. Entity
declarations are rejected before parsing and file size is capped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ..models import Status, TestOutcome, TestRun
from ..normalize import normalize_test_id, salient_line, signature_of

MAX_REPORT_BYTES = 64 * 1024 * 1024
"""A JUnit report larger than this is not a test report."""

MAX_DETAIL_CHARS = 8000
"""Tracebacks are kept for display but truncated so history stays small."""

_PROLOG_SCAN_BYTES = 8192
_ENTITY_DECLARATION = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)

_JAVA_FQCN = re.compile(r"^(?:[a-z][\w$]*\.)+[A-Z][\w$]*$")
_DOTNET_FQCN = re.compile(r"^(?:[A-Z][\w]*\.){2,}[A-Z][\w]*$")
_GO_TEST_NAME = re.compile(r"^(?:Test|Benchmark|Example|Fuzz)[A-Z_]")


class ParseError(ValueError):
    """A report could not be read.

    Bad input, not a bug: the caller reports the file and continues the batch.
    """


@dataclass(frozen=True, slots=True)
class _Case:
    """A testcase element flattened out of whatever nesting it arrived in."""

    name: str
    classname: str | None
    file: str | None
    suite: str | None
    duration: float | None
    status: Status
    message: str | None
    detail: str | None
    retried: bool


def parse_file(
    path: str | Path,
    *,
    iteration: int | None = None,
    seed: str | None = None,
    commit_sha: str | None = None,
    branch: str | None = None,
    ci_run_id: str | None = None,
) -> TestRun:
    """Parse one JUnit XML file into a TestRun.

    Raises ParseError for anything wrong with the file itself.
    """
    source = Path(path)
    raw = _read_report(source)

    try:
        root = ET.fromstring(raw)  # noqa: S314 - entity declarations rejected above
    except ET.ParseError as exc:
        # Truncated reports are exactly what a crashed or killed CI job leaves
        # behind, so this is a common path rather than an exotic one.
        raise ParseError(f"malformed XML: {exc}") from exc

    suites = _collect_suites(root)
    if not suites:
        raise ParseError("no <testsuite> elements found")

    cases: list[_Case] = []
    for suite in suites:
        cases.extend(_cases_in(suite))

    if not cases:
        raise ParseError("no <testcase> elements found")

    runner = detect_runner(root, suites, cases)
    outcomes = tuple(
        _to_outcome(case, position, runner) for position, case in enumerate(cases)
    )

    return TestRun(
        run_uid=_run_uid(raw, source, iteration),
        started_at=_started_at(suites, source),
        outcomes=outcomes,
        commit_sha=commit_sha,
        branch=branch,
        ci_run_id=ci_run_id,
        source_path=str(source),
        runner=runner,
        iteration=iteration,
        seed=seed,
        duration=_total_duration(root, suites),
    )


def detect_runner(
    root: ET.Element, suites: list[ET.Element], cases: list[_Case]
) -> str:
    """Identify the producing runner from structural fingerprints.

    Used for display and, more importantly, to pick the right randomization flag
    when hunting and the right skip-list format when quarantining.
    """
    suite_names = {(s.get("name") or "").lower() for s in suites}
    root_name = (root.get("name") or "").lower()

    if "pytest" in suite_names or "pytest" in root_name:
        return "pytest"
    if "jest" in root_name or "jest" in suite_names:
        return "jest"

    files = [c.file for c in cases if c.file]
    if files:
        if any(f.endswith(".py") for f in files):
            return "pytest"
        if any(f.endswith((".ts", ".tsx", ".js", ".jsx")) for f in files):
            return "jest"

    classnames = [c.classname for c in cases if c.classname]
    if classnames and sum(" > " in c for c in classnames) > len(classnames) / 2:
        return "jest"

    names = [c.name for c in cases]
    if names and sum(bool(_GO_TEST_NAME.match(n)) for n in names) > len(names) / 2:
        return "go"

    if classnames and sum(bool(_JAVA_FQCN.match(c)) for c in classnames) > len(classnames) / 2:
        return "junit"

    # .NET namespaces are PascalCase throughout, so they fail the Java pattern's
    # lowercase-package requirement and need their own check.
    if classnames and sum(bool(_DOTNET_FQCN.match(c)) for c in classnames) > len(classnames) / 2:
        return "dotnet"

    if any((s.get("name") or "").endswith(".dll") for s in suites):
        return "dotnet"

    return "unknown"


def _read_report(source: Path) -> bytes:
    if not source.is_file():
        raise ParseError("not a file")

    size = source.stat().st_size
    if size == 0:
        raise ParseError("file is empty")
    if size > MAX_REPORT_BYTES:
        raise ParseError(f"file is {size / 1e6:.0f} MB, over the {MAX_REPORT_BYTES / 1e6:.0f} MB cap")

    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ParseError(f"could not read: {exc}") from exc

    # Entity declarations must appear in the prolog, so scanning the head is
    # sufficient. ElementTree is vulnerable to entity-expansion attacks, and
    # refusing declarations outright avoids that without taking a dependency.
    if _ENTITY_DECLARATION.search(raw[:_PROLOG_SCAN_BYTES]):
        raise ParseError("contains a DOCTYPE or ENTITY declaration, refusing to parse")

    return raw


def _collect_suites(root: ET.Element) -> list[ET.Element]:
    """Find every testsuite, at any depth.

    Maven and Gradle nest testsuites inside testsuites; pytest does not. Walking
    the whole tree handles both without needing to know which we have.
    """
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = []
    suites.extend(root.iter("testsuite"))

    # A suite that only contains other suites holds no cases of its own; keeping
    # it would not break anything but pruning keeps the parse honest.
    seen: set[int] = set()
    unique: list[ET.Element] = []
    for suite in suites:
        marker = id(suite)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(suite)
    return unique


def _cases_in(suite: ET.Element) -> list[_Case]:
    """Extract direct testcase children of a suite."""
    suite_name = suite.get("name") or None
    cases: list[_Case] = []

    for element in suite.findall("testcase"):
        status, message, detail = _classify_case(element)
        cases.append(
            _Case(
                name=element.get("name") or "<unnamed>",
                classname=element.get("classname") or None,
                file=element.get("file") or None,
                suite=suite_name,
                duration=_float(element.get("time")),
                status=status,
                message=message,
                detail=detail,
                retried=_was_retried(element),
            )
        )
    return cases


_RETRY_TAGS = ("flakyFailure", "flakyError", "rerunFailure", "rerunError")

_UNINFORMATIVE_MESSAGES = frozenset(
    {"failed", "error", "failure", "test failed", "assertion failed", "exception", "-"}
)
"""Messages that carry no diagnostic content.

go-junit-report writes message="Failed" on every failure and puts the real output
in the element text. Trusting the attribute there would give every Go failure the
same signature and collapse unrelated bugs into one cluster.
"""


def _classify_case(element: ET.Element) -> tuple[Status, str | None, str | None]:
    """Determine outcome and extract the failure text.

    Precedence is error > failure > skipped > passed. A testcase carrying both an
    error and a failure is malformed, but runners do emit it, and error is the
    more severe reading.
    """
    for tag, status in (("error", Status.ERROR), ("failure", Status.FAILED)):
        node = element.find(tag)
        if node is not None:
            return status, _message_of(node), _detail_of(node)

    skipped = element.find("skipped")
    if skipped is not None:
        return Status.SKIPPED, _message_of(skipped), None

    # A test that failed and then passed on retry is recorded as passing, because
    # that is what happened. The retry element still holds the only description of
    # the failure, so it is kept for diagnosis.
    for tag in _RETRY_TAGS:
        node = element.find(tag)
        if node is not None:
            return Status.PASSED, _message_of(node), _detail_of(node)

    return Status.PASSED, None, None


def _was_retried(element: ET.Element) -> bool:
    """Detect a runner-recorded retry.

    Surefire emits <flakyFailure> when a test failed then passed on retry, and
    <rerunFailure> when it failed every attempt. pytest-rerunfailures emits
    <rerunFailure>. Either way the runner observed one test produce more than one
    outcome inside a single run, which is direct evidence of flakiness rather than
    an inference from history.
    """
    return any(element.find(tag) is not None for tag in _RETRY_TAGS)


def _message_of(node: ET.Element) -> str | None:
    """Pick the most informative description available on a failure element.

    The `message` attribute is preferred, but only when it says something. Some
    runners fill it with a constant.
    """
    message = (node.get("message") or "").strip()
    if message and message.lower() not in _UNINFORMATIVE_MESSAGES:
        return message

    text = (node.text or "").strip()
    if text:
        salient = salient_line(text)
        if salient:
            return salient[:1000]

    if message:
        return message

    kind = (node.get("type") or "").strip()
    return kind or None


def _detail_of(node: ET.Element) -> str | None:
    text = (node.text or "").strip()
    if not text:
        return None
    if len(text) > MAX_DETAIL_CHARS:
        return text[:MAX_DETAIL_CHARS] + "\n... (truncated)"
    return text


def _to_outcome(case: _Case, position: int, runner: str) -> TestOutcome:
    return TestOutcome(
        test_id=build_test_id(case.name, case.classname, case.file, runner),
        name=case.name,
        status=case.status,
        suite=case.suite,
        duration=case.duration,
        message=case.message,
        detail=case.detail,
        signature=signature_of(case.message, case.detail) or None,
        position=position,
        retried=case.retried,
    )


def build_test_id(
    name: str, classname: str | None, file: str | None, runner: str = "unknown"
) -> str:
    """Build the identifier that history is keyed on.

    Stability across runs is the load-bearing assumption of the whole tool: an id
    that varies run to run fragments a test's history and makes it invisible.

    For pytest, the id is reconstructed as a real nodeid
    (`tests/test_x.py::TestClass::test_y`) rather than left as the dotted
    classname, because that is the form `--deselect` accepts and quarantine export
    is only useful if the ids can be pasted straight into a command.
    """
    if runner == "pytest":
        return normalize_test_id(_pytest_node_id(name, classname, file))

    if classname:
        # jest repeats the full describe path in both attributes; joining them
        # would double it.
        if name.startswith(classname):
            return normalize_test_id(name)
        return normalize_test_id(f"{classname}::{name}")

    if file:
        return normalize_test_id(f"{file}::{name}")

    return normalize_test_id(name)


def _pytest_node_id(name: str, classname: str | None, file: str | None) -> str:
    """Reconstruct a pytest nodeid from JUnit attributes.

    pytest's default xunit2 output drops the `file` and `line` attributes and
    encodes location entirely in a dotted `classname`, so the module path has to
    be recovered from it. Test classes are distinguishable because pytest only
    collects classes whose name starts with a capital `Test`, so a trailing
    capitalized segment is a class and everything before it is the module.

        tests.test_sample            -> tests/test_sample.py
        tests.test_sample.TestGroup  -> tests/test_sample.py::TestGroup
    """
    if not classname:
        return f"{file}::{name}" if file else name

    segments = classname.split(".")
    classes: list[str] = []
    while len(segments) > 1 and segments[-1][:1].isupper():
        classes.insert(0, segments.pop())

    if file:
        module_path = file
    elif segments:
        module_path = "/".join(segments) + ".py"
    else:
        module_path = classname

    return "::".join([module_path, *classes, name])


def _run_uid(raw: bytes, source: Path, iteration: int | None) -> str:
    """Content-addressed run identity, which is what makes ingest idempotent.

    Content alone is not enough: two suites can legitimately produce byte-identical
    XML. Path alone is not enough either, because CI overwrites the same path on
    every run. Iteration is included so a hunt's repeated runs stay distinct even
    when a suite is fully deterministic and every report is identical.
    """
    digest = hashlib.sha256()
    digest.update(raw)
    digest.update(b"\x00")
    digest.update(str(source.resolve()).encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(str(iteration if iteration is not None else "").encode())
    return digest.hexdigest()


def _started_at(suites: list[ET.Element], source: Path) -> str:
    """Best available start time, as an ISO 8601 string.

    Ordering by this drives flip detection, so a wrong value produces wrong
    results. Prefer the timestamp the runner recorded; fall back to file mtime,
    which for a freshly written report is close enough.
    """
    for suite in suites:
        stamp = suite.get("timestamp")
        if stamp:
            parsed = _parse_timestamp(stamp)
            if parsed:
                return parsed

    return datetime.fromtimestamp(source.stat().st_mtime, tz=UTC).isoformat()


def _parse_timestamp(value: str) -> str | None:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _total_duration(root: ET.Element, suites: list[ET.Element]) -> float | None:
    root_time = _float(root.get("time"))
    if root_time is not None:
        return root_time

    times = [t for t in (_float(s.get("time")) for s in suites) if t is not None]
    return sum(times) if times else None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
