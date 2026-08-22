# 0018 — Capture real runner output, because a faithful fixture is not the same thing

**Status:** Accepted

## Context

`tests/fixtures/README.md` labelled each parser fixture as **captured** or **reference**, and
said plainly that go, Surefire and .NET were "untested against live runners until someone with
those toolchains confirms it".

That was honest, and it was also an unpaid debt. The reference fixtures were written
carefully: correct element structure, root-level `testsuite` and nested variants, retry
elements, package-path classnames. Every parser path they were meant to exercise, they
exercised. The steering rule they were written against says exactly why that is not enough:

> Every parser dialect needs a real fixture file. Hand-written XML that happens to parse
> proves nothing about Maven's actual output.

A Go toolchain became available, so the debt on that dialect could be paid.

## Decision

Install Go, write a deliberately mixed suite (a pass, two failures with distinct messages, a
skip, and three subtests including a failing one), capture real `gotestsum --junitfile`
output, and add it as `tests/fixtures/go-gotestsum.xml` **alongside** the existing reference
file rather than replacing it.

Alongside, because the two cover different things. The reference fixture exercises multiple
suites and race-detector output, which a single-package capture does not produce. Deleting it
to look tidier would have traded real coverage for a cleaner directory listing.

## What the capture found

A defect, in the exact place the fixture could not reach.

Go wraps every failure in banner lines:

```
=== RUN   TestExpectsCleanRegistry
    basket_test.go:25: registry already contains 'session' -- state left behind by another test
--- FAIL: TestExpectsCleanRegistry (0.00s)
```

The parser already knew not to trust Go's `message="Failed"` attribute — there was a comment
in `junit.py` naming go-junit-report for exactly that. It fell back to
`normalize.salient_line`, which took **the last non-empty line**, on a stated belief written
into the docstring:

> the last non-empty line, which is where the actual error sits in a Python traceback and in
> most Go test output.

The second half of that sentence is false. The last line is the `--- FAIL:` banner, and it
carries the **test's own name**.

Two consequences, neither cosmetic:

1. **Signature clustering was dead for Go.** The signature is the cluster key. With the test
   name inside it, no two Go tests could ever share a signature, so two tests failing from one
   root cause were never grouped. Clustering existed and could not work.
2. **Every Go failure classified as `unknown`.** `classify.py` matches on message text. It saw
   `--- FAIL: TestX (0.00s)` and matched nothing, so no Go failure ever got a root-cause
   category or a remediation hint.

Measured on the captured file, before:

```
signature: '--- FAIL: TestExpectsCleanRegistry (<DURATION>)'
signature: '--- FAIL: TestKnownBroken (<DURATION>)'
```

After:

```
signature: "registry already contains 'session' -- state left behind by another test"
signature: 'assertion failed: expected 3, got 4'
```

### Two fixes, and a third that followed

**`salient_line` skips runner banners.** A `_GO_BANNER` pattern matches `=== RUN`, `=== PAUSE`,
`=== CONT`, `=== NAME`, `--- FAIL:`, `--- PASS:`, `--- SKIP:`, `--- BENCH:` and bare
`FAIL`/`ok`/`PASS` lines. The search runs from the end and returns the last line that is not
one of them.

When *every* line is a banner the last one is still returned. Go does that for a parent test
whose only failure is that a subtest failed: there is genuinely no message, and the banner is
the honest answer rather than an empty signature.

**A multi-line `message` attribute is reduced the same way element text is.** gotestsum puts
the whole banner-wrapped block into the `message` attribute on a *skip*, so trusting the
attribute verbatim put three lines of scaffolding where a one-line reason belongs. Routing both
the attribute and the text through the same function is also the only way the two cannot
disagree.

**A leading source location is dropped from the cluster key.** Fixing the above left signatures
of the form `basket_test.go:<N>: connection refused`, so two Go tests in *different files*
failing on the same cause still could not cluster — Go prefixes every message with the
assertion's location. `normalize_message`'s docstring already committed to removing paths and
numbers as run-to-run noise; a leading `file.go:NN:` is the same category.

Anchored to the start, deliberately. A location appearing mid-message is usually a traceback
frame that distinguishes genuinely different failures, and the filename remains on the
outcome's `message` and `detail` for a human to read. Only the cluster key loses it.

## Measured effect

The structure steering file requires a benchmark number before and after any change that
touches scoring or clustering. Run at the same seed:

| | Before | After |
|---|---:|---:|
| Overall accuracy | 93.5% | 93.5% |
| False alarm rate | 0.0% | 0.0% |
| Missed break rate | 5.4% | 5.4% |
| Clusters on the demo database | 5 | 5 |

**Nothing moved,** and that is the expected result rather than a disappointing one. Verdicts
come from divergence and flips, not from message text, and the generated benchmark's messages
carry no leading source locations, so there was nothing for the new rule to act on. The change
is confined to the dialect it was written for, which is what a targeted fix should look like.

The Go behaviour is pinned by ten new tests, including one that parses two synthetic-but-real
shaped Go failures from different files and asserts they land in **one** cluster — the property
the defect destroyed, checked through the real parse path rather than by calling the normalizer
directly.

## Consequences

**pytest, jest and go are now validated against captured output. Surefire and .NET are not**,
and the fixture README says so in those words. The caveat is worth more now, not less: the one
dialect that moved from reference to captured immediately produced a defect, which is direct
evidence about what the remaining two are hiding.

**The regeneration commands are recorded** in `tests/fixtures/README.md`, so the next person
with a JVM can close the Surefire gap without reverse-engineering how the existing files were
made.

**`test_directory_ingest_skips_bad_files_and_continues` asserts 7 runs, not 6.** The count is
spelled out rather than derived on purpose — adding a fixture makes it fail and someone has to
look, which is the check that a malformed report is skipped rather than silently counted.

## Rejected alternatives

**Replacing `go.xml` with the capture.** Would have lost multi-suite and race-detector
coverage. Two fixtures for one dialect is not duplication when they exercise different paths.

**Leaving the caveat in place and doing nothing.** Defensible, since the limitation was
disclosed. Rejected because a disclosed limitation is still a limitation, and this one turned
out to be concealing a defect that made a shipped feature inoperative for an entire language.

**Stripping source locations everywhere, not just at the start.** Would collapse genuinely
different failures that differ only by which frame raised them.

**Installing a JVM and a .NET SDK to close all three.** Out of proportion to the remaining
value, and the honest disclosure costs nothing. If they were free the answer would be yes.
