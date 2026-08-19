# 0002 — Reconstruct pytest node ids rather than trusting the XML

**Status:** Accepted

## Context

Test history is keyed on `test_id`. If the same test yields different ids across runs,
its history fragments and it becomes undetectable. This makes id stability the
load-bearing assumption of the entire tool.

The plan was to build ids from the JUnit `file` and `classname` attributes. Capturing
real output from pytest 9.1.1 showed that does not work:

```xml
<testcase classname="tests.test_sample" name="test_passes" time="0.000" />
<testcase classname="tests.test_sample.TestGrouped" name="test_inside_class_fails" />
```

**There is no `file` attribute.** pytest's default `junit_family=xunit2` omits `file`
and `line` entirely, encoding location only in a dotted `classname`. No format
description mentions this.

Falling back to `classname::name` would be stable, but produces
`tests.test_sample::test_passes` — which is not a pytest node id, so quarantine exports
using `--deselect` would silently select nothing.

## Decision

Reconstruct real node ids from the dotted classname, using the fact that pytest only
collects classes whose name starts with a capital:

```
tests.test_sample            → tests/test_sample.py
tests.test_sample.TestGroup  → tests/test_sample.py::TestGroup
```

Walk the dotted segments from the right, treating capitalized trailing segments as
classes and the rest as the module path.

## Consequences

**Quarantine exports work.** Verified by running the generated `--deselect` arguments
against a real suite and confirming the intended tests were deselected.

**A heuristic, not a parse.** A test class not starting with a capital would be
misread — but pytest would not have collected it either, so the case cannot arise in
practice.

**Other dialects need their own handling**, discovered the same way:

| Runner | Surprise |
|---|---|
| jest-junit | writes the identical string into both `classname` and `name`; joining them doubles the describe path |
| go-junit-report | writes `message="Failed"` on every failure, so trusting the attribute gives every Go failure one signature |
| Surefire | root element is `<testsuite>` with no `<testsuites>` wrapper |
| .NET (trx2junit) | PascalCase namespaces fail a Java FQCN pattern |

**Parameterized ids get scrubbed** inside their bracketed block only, so a parameter
containing a temp path or a UUID does not fragment history, while `test_x[3]` stays
distinct from `test_x[4]`.

## Note on evidence

The two dialects that shaped the parser most are the two that were **captured from real
runners**. The go, Surefire and .NET fixtures were written to documented formats because
those toolchains were unavailable, and that distinction is recorded in
[`tests/fixtures/README.md`](../../tests/fixtures/README.md) rather than glossed over.
Treat those three as unproven against live output.
