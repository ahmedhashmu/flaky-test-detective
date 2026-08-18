# Requirements: Flaky Test Detective

## Problem statement

A flaky test is a test that produces different outcomes for the same code. Teams
respond to them in one of two ways, and both are bad:

1. **Retry everything.** `--reruns 3` on the whole suite. Real regressions get
   masked, and CI time triples.
2. **Ignore red builds.** Once "the build is always a bit red", failures stop
   carrying information and genuine breakage ships.

The blocker is not that engineers lack the will to fix flakes. It is that they
cannot *identify* them. A single failed CI run is indistinguishable from a real
regression at the moment you look at it. Distinguishing the two requires history
across many runs, correlated with the commit under test, and almost no team has
that in a queryable form. CI providers keep logs, not structured outcomes.

## Goal

Given the JUnit XML that virtually every test runner already emits, accumulate
run history and answer, with evidence:

- Which tests are flaky, ranked by how much damage they cause?
- Why is each one flaky, categorized by likely root cause?
- Which should be quarantined right now to unblock the team?
- Did a given red build fail because of a real regression or a known flake?

## Users

**Primary — the engineer on build duty.** CI is red. They need to know within
seconds whether to investigate or re-run.

**Secondary — the engineer paying down test debt.** They need a ranked worklist
of the flakes that cost the most, with enough diagnostic signal to start fixing
rather than start investigating.

**Tertiary — CI itself.** Automated gating: fail the build on new flakes,
comment the triage summary on the PR.

## Functional requirements

### FR1 — Ingest

- **FR1.1** Parse JUnit XML from `pytest`, `jest --reporters=jest-junit`,
  `go-junit-report`, Maven/Gradle Surefire, and .NET `trx2junit`. These dialects
  disagree on nesting, attribute names, and how skips are encoded.
- **FR1.2** Accept a single file, a glob, or a directory tree.
- **FR1.3** Attach each ingested run to a commit SHA, branch, and CI run
  identifier. Auto-detect from the git repo and from the environment variables of
  GitHub Actions, GitLab CI, CircleCI, Jenkins, and Buildkite. Allow explicit
  override.
- **FR1.4** Ingest must be idempotent. Re-ingesting the same file must not
  double-count, because CI retries and local experimentation will both cause it.
- **FR1.5** A malformed or truncated XML file must not abort a batch ingest.
  Report it and continue; truncated reports are exactly what a crashed CI job
  leaves behind.

### FR2 — Detection

- **FR2.1** The primary signal is **same-commit divergence**: one test, one
  commit SHA, both a pass and a fail. This is proof of flakiness, not an
  inference, because the code was identical.
- **FR2.2** The secondary signal is **flip rate**: transitions between pass and
  fail across the run history, normalized by run count. This catches flakes that
  were never run twice on one commit.
- **FR2.3** Produce a **flake score** in `[0, 1]` combining both signals,
  weighted by confidence so a test seen 3 times does not outrank one seen 200
  times on identical evidence.
- **FR2.4** Distinguish a flake from a **consistent failure** (fails on every
  run at a commit; that is a regression, not a flake) and from a **fixed** test
  (flaky historically, stable for the last N runs).
- **FR2.5** Report per-test evidence: total runs, failures, flips, diverging
  commits, first and last seen.

### FR3 — Diagnosis

- **FR3.1** Normalize failure messages so that superficially different messages
  with the same cause collapse together: strip memory addresses, timestamps,
  UUIDs, ports, temp paths, line numbers, and durations.
- **FR3.2** Cluster failures by normalized signature and report cluster size, so
  one root cause spanning 40 tests is visible as one problem.
- **FR3.3** Classify likely root cause by heuristic, with the matched evidence
  attached so a human can overrule it: timeout, race condition, order
  dependence, external network, resource exhaustion, time/date dependence,
  randomness, and plain assertion failure.
- **FR3.4** Detect **order dependence** specifically: correlate outcome with the
  test's position in the run and with which tests preceded it. A test that only
  fails when it runs after some other test is a shared-state bug, and that is a
  different fix from a timeout.

### FR4 — Active hunting

- **FR4.1** Run a user-supplied test command N times and ingest each result, so
  a team can find flakes before CI does.
- **FR4.2** Support order randomization between runs to surface order-dependent
  flakes, which sequential repetition alone will never find.
- **FR4.3** Stream progress. A 50-iteration hunt is a long wait and a silent
  terminal is indistinguishable from a hang.
- **FR4.4** Stop early once a configurable number of distinct flakes is found.

### FR5 — Reporting

- **FR5.1** Console report with ranked flakes for interactive triage.
- **FR5.2** Markdown report suitable for posting as a PR comment.
- **FR5.3** JSON report for programmatic use, with a documented, stable shape.
- **FR5.4** Self-contained HTML dashboard, no external asset requests.
- **FR5.5** A **triage** view answering the build-duty question directly: of the
  tests that failed in this specific run, which are known flakes and which are
  new? This is the highest-value output of the tool.

### FR6 — Quarantine

- **FR6.1** Recommend quarantine for tests above a threshold.
- **FR6.2** Emit skip lists in runner-native formats: pytest `--deselect` args,
  pytest marker expressions, Jest `testPathIgnorePatterns`, and a generic list.
- **FR6.3** Every quarantine entry carries an expiry date. Quarantine without
  expiry is deletion with extra steps.
- **FR6.4** Re-verify expired entries against current history and report which
  are now stable and can be released.

### FR7 — CI integration

- **FR7.1** Exit codes that gate a build: `0` clean, `1` new flakes detected,
  `2` regression (consistent failure, not flakiness), `3` usage or input error.
- **FR7.2** All thresholds configurable by file and by flag.
- **FR7.3** Operate with no network access and no credentials of any kind.

## Non-functional requirements

- **NFR1** Analyze 100k stored results in under 2 seconds on a laptop. Slower
  than that and it gets dropped from CI.
- **NFR2** Storage is a single SQLite file. No service to run, because a tool
  that needs infrastructure will not get adopted for a testing side-concern.
- **NFR3** Runtime dependencies limited to `typer` and `rich`. Parsing uses the
  standard library.
- **NFR4** Python 3.11+, on macOS and Linux.
- **NFR5** Language-agnostic by construction. The tool sees JUnit XML, never
  source code, so it works for any stack that emits it.

## Out of scope

- Automatically fixing flaky tests. Root causes are semantic and require
  understanding intent.
- A hosted service, dashboard server, or accounts.
- Reading or modifying test source code.
- Non-JUnit result formats in v1. TAP and Allure are plausible later.

## Acceptance criteria

1. A planted flaky suite in `examples/` is detected end-to-end, and the tests
   that are genuinely flaky are ranked above the ones that are not.
2. A consistently failing test is reported as a regression, not a flake.
3. An order-dependent test is identified as order-dependent, not as a generic
   flake.
4. Every documented command runs as documented, from a clean clone, with no
   credentials.
5. Re-ingesting a report twice leaves the analysis unchanged.
