# Investigation engine — requirements

Fourth spec for this project.

- [`../flaky-test-detective/`](../flaky-test-detective/) built the detector.
- [`../accuracy-and-adoption/`](../accuracy-and-adoption/) made it measurable and adoptable.
- [`../product-layer/`](../product-layer/) made it legible.
- This one makes it **conclusive**: detection is where the tool used to stop, and stopping
  there leaves the actual work undone.

A note on how this round was planned, since the record should be accurate: it ran from a
14-item task list rather than from a written spec, and this document consolidates that list
into the same form as the other three. The requirements below were the criteria being worked
to; the tasks file marks what landed and what was deliberately left.

## Why a fourth spec

Two findings forced it, and neither was a matter of opinion.

### Finding 1: same-commit divergence is not a differentiator

A market check found that BuildPulse and Trunk already detect flakiness from repeated CI
results, including at the same commit. The primary signal this project was organised around
is table stakes, not an edge. Spending a fourth round making detection incrementally better
would have been rebuilding something that exists.

What none of them do is **close the loop**. They tell a team which tests are flaky. Somebody
still has to make the test fail on purpose, find the cause, fix it, and prove the fix. That
is where the cost is.

So the target for this round is a sequence, not a feature:

> detect → prove → reproduce → isolate cause → identify introduction → verify repair →
> prevent recurrence

### Finding 2: our own diagnosis was much weaker than our benchmark said

The generated benchmark reported order-dependence precision and recall of 1.000. Scored
against 146 order-labelled tests in real repositories, diagnosis was **11.6%**. The
generator placed every polluter immediately before its victim, which is the assumption the
detector was built on, so the two agreed with each other and neither was checked against
reality ([ADR-0014](../../../docs/adr/0014-search-a-window-for-the-polluter.md)).

That single number is the strongest argument in this spec. A tool that detects
order-dependent tests essentially perfectly and can explain one in nine of them is a tool
that hands its user a lead, not an answer.

## Functional requirements

### FR1 — Real-world validation against labels we did not write

- FR1.1 Score the detector against a published, developer-confirmed flaky-test dataset.
- FR1.2 Build and run each project at its recorded commit; do not trust archived results.
- FR1.3 Report recall against labels *that reproduced in our runs*, and precision against
  *observed same-commit divergence* — a denominator that cannot be gamed.
- FR1.4 Report the categories separately, including the ones the tool does badly at.
- FR1.5 Commit the raw results so the score can be recomputed without re-running anything.
- FR1.6 Fail CI if a published figure drifts.

### FR2 — Attribute flakiness to a branch

- FR2.1 Compare a head branch against a baseline and report what it **introduced** versus
  what it **inherited**.
- FR2.2 Block only on introduced flakiness or introduced breakage.
- FR2.3 Judge against the baseline's own uncertainty, not its point estimate: a clean
  baseline of 8 runs does not prove a low rate.
- FR2.4 Report `unproven` when the evidence does not support a conclusion.

### FR3 — Prove a fix worked

- FR3.1 Require more than a clean streak. The streak must beat the old failure rate, the
  conditions that used to fail must have actually occurred, and nothing else may have broken.
- FR3.2 State how many clean runs are needed at the old rate, and how many were seen.
- FR3.3 Say "cannot say yet" when it cannot be proved, with the number that would settle it.

### FR4 — One command to a working demo

- FR4.1 A judge or new user reaches a populated dashboard in two commands, with no suite of
  their own and no waiting.
- FR4.2 Demo data is labelled as demo data, first, in the interface.
- FR4.3 Refuse to overwrite real history.
- FR4.4 Reuse the real analysis path. A checked-in fixture would prove nothing.

### FR5 — Diagnosis beyond the adjacent test

- FR5.1 Search a bounded window of preceding tests for the polluter, with a multiplicity
  correction.
- FR5.2 Fix the generator so its polluters sit at varying distances, including one beyond the
  implementation's default reach.
- FR5.3 Report the measured effect on **real** data, whatever it is.
- FR5.4 Never name a polluter on weaker evidence than before. Precision is the constraint;
  naming rate is the target.

### FR6 — Where a test fails, as a measurement

- FR6.1 Correlate failures with environment dimensions recorded per run.
- FR6.2 Dimensions are generic key/value labels, not a fixed schema of named columns.
- FR6.3 Apply a multiplicity correction over the dimension/value pairs actually tested.
- FR6.4 Report confounding explicitly when two dimensions describe the same set of runs.
- FR6.5 Labels describe the machine that ingested a report, not the machine that produced it,
  and the tool must say so.

### FR7 — Turn a flake into a command that fails on demand

- FR7.1 Reduce the recorded predecessors to the smallest set that still makes the test fail,
  by experiment rather than inference.
- FR7.2 Output a literal command and the measured failure rate under it.
- FR7.3 Measure a control first — the test alone — and require every later observation to
  beat that rate on an exact binomial tail.
- FR7.4 Find conjunctions: two tests that only break the victim together.
- FR7.5 Report the honest non-answers: it fails on its own, or nothing tried made it fail.
- FR7.6 Publish the rate from a fresh confirmation run, never from the cheap trials used
  during the search.
- FR7.7 State the cost before starting, and the cost incurred afterwards.
- FR7.8 Name the runners it does not support rather than appearing to support them.

### FR8 — Assert relationships, not only examples

- FR8.1 Property-based tests over generated histories for the claims that are statements
  about *all* histories: never-passed is never flaky, merge is a set union, ingest order
  cannot change a verdict, formatting cannot alter a conclusion.
- FR8.2 Assert the generator's own reach, so a property cannot pass vacuously.
- FR8.3 Development dependency only.

### FR9 — Windows and macOS are supported platforms

- FR9.1 Native support. No WSL instruction.
- FR9.2 A cross-platform CI matrix.
- FR9.3 Exercise the failure the test suite structurally cannot see: a redirected,
  non-UTF-8 stdout.

## Non-functional requirements

- NFR1 **Two runtime dependencies.** Every addition is a reason not to install the tool.
  Property testing is a dev dependency; the reproducer uses `subprocess` and the existing
  JUnit parser.
- NFR2 **Credential-free.** No account, no network call, no API key, on any path a judge
  will take.
- NFR3 **Nothing simulated.** The demo suite fails for real reasons — threads, clocks,
  sockets, module state. A tool shown to detect simulated flakes has been shown nothing.
- NFR4 **Every number traceable.** Any figure in output or documentation must be reproducible
  by a command in the README.
- NFR5 **Negative results are published.** If a change does not work on real data, that is
  the finding, and it stays on the page.
- NFR6 **AI stays separated from proof.** Heuristic categories are labelled as guesses and
  never presented alongside measured evidence as though they were the same kind of thing.
- NFR7 **Determinism.** Same inputs, same output, including ordering, across platforms.

## Out of scope, deliberately

- **Beating Trunk or BuildPulse on detection accuracy.** Not a claim this project makes, and
  not a claim it could support. The differentiators are local-first, credential-free,
  reproducible evidence and self-measured accuracy.
- **"AI analyses your logs."** The tool reads structured results and computes statistics.
  Adding a language model to summarise failures would make every output unverifiable, which
  is the opposite of the goal.
- **Reading test source.** Would break language agnosticism, which is what makes the tool
  work for pytest, jest, go, JUnit and .NET without knowing anything about them.
- **A hosted service.** Zero setup means a SQLite file.
