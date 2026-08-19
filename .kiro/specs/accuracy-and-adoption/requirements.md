# Requirements: Accuracy and Adoption

Second spec for this project. The first one
([`../flaky-test-detective/`](../flaky-test-detective/)) built the detector. This
one addresses three gaps that a review of the finished tool made obvious.

## Why a second spec

The first spec delivered a working detector with 447 tests. Reviewing it against
the question "would a team actually adopt this?" surfaced three problems that no
amount of additional test coverage would fix.

### Gap 1: the tool never proves its own accuracy

The product steering for this project says the tool's credibility "rests on never
crying wolf". Every claim about accuracy so far rests on one hand-built demo suite
of sixteen tests, inspected by eye.

That is not good enough for a tool whose entire argument is trustworthiness. A
flaky-test detector that cannot state its own false-positive rate is asking for the
same blind trust it exists to replace. Worse, the thresholds in
`analysis/flakiness.py` and `analysis/ordering.py` were tuned by looking at that
one suite, which is a sample size of one.

### Gap 2: history cannot be shared, and that was documented rather than solved

`run_uid` is a content hash. That property was chosen so ingest would be idempotent
under CI retries, but it has a second consequence that went unexploited: **two
databases built independently can be combined without double-counting.** The
README instead lists cross-machine history under "Limitations".

Without merging, the tool only works where all runs happen on one machine. That
rules out sharded CI, matrix builds, and any team wanting to pool local hunts —
which is most of the realistic use.

### Gap 3: adopting it in CI takes too much work

Current integration means hand-writing cache keys, database paths, and a triage
step. Every step is a chance to get it wrong, and test health is a side-concern
that gets exactly one chance to be easy.

### Gap 4: the tool says *what* is flaky but not *when it started*

The database already records outcomes per commit. Nothing surfaces the most useful
question after "which test": *which change made it flaky?*

## Functional requirements

### FR1 — Accuracy measurement

- **FR1.1** Generate synthetic test populations with **known ground truth**: each
  generated test carries the label it is supposed to receive.
- **FR1.2** Cover every verdict the tool can produce: flaky (across a range of
  failure rates), stable, broken, regression, fixed, and order-dependent.
- **FR1.3** Generation must be deterministic given a seed, so a reported figure can
  be reproduced exactly.
- **FR1.4** Run the **real** analysis pipeline over the generated data. No
  shortcuts, no mocking of the scorer; if the harness and the tool disagree, the
  harness is measuring the wrong thing.
- **FR1.5** Report per-label precision, recall and F1, plus a confusion matrix
  showing what was mistaken for what.
- **FR1.6** Report the metric that matters most for this tool specifically: the
  rate at which a genuine regression or broken test is misreported as flaky. That
  is the failure mode the product steering calls the worst possible one, so it gets
  its own number.
- **FR1.7** Support sweeping a parameter (run count, failure rate, commit coverage)
  to show how accuracy responds to available evidence.
- **FR1.8** Publish the measured numbers, including the weak spots. A benchmark
  that only reports favourable results is marketing.

### FR2 — History merging

- **FR2.1** Merge two or more databases into one.
- **FR2.2** Merging must be idempotent and order-independent: merging A into B must
  give the same analysis as merging B into A, and merging twice must change
  nothing.
- **FR2.3** Report how many runs came from each source and how many were skipped as
  duplicates.
- **FR2.4** Refuse to merge a database written by a newer schema version rather than
  silently misreading it.
- **FR2.5** Support the CI shard case directly: merge a directory of databases.

### FR3 — One-step CI adoption

- **FR3.1** Ship a GitHub Action usable in a handful of lines.
- **FR3.2** Handle history persistence automatically via the Actions cache.
- **FR3.3** Post the triage result as a pull-request comment, updating the existing
  comment rather than adding a new one on every push.
- **FR3.4** Expose the outcome as step outputs so a workflow can branch on it.
- **FR3.5** Fail the build according to the documented exit codes, configurably.
- **FR3.6** Work with any runner that produces JUnit XML, not just pytest.

### FR4 — Flakiness attribution

- **FR4.1** For a given test, identify the earliest commit where divergence appears.
- **FR4.2** Show the per-commit timeline so a human can see the transition.
- **FR4.3** State plainly when attribution is not possible, rather than guessing:
  with sparse history the answer is often genuinely unknown.

## Non-functional requirements

- **NFR1** The benchmark must complete in under 30 seconds at its default size, or
  nobody will run it.
- **NFR2** No new runtime dependencies. The benchmark is part of the tool, not a
  separate research project.
- **NFR3** The existing architecture rules still hold: `analysis/` stays pure, and
  the benchmark generates `TestOutcome` objects rather than reaching for storage.

## Out of scope

- Comparing accuracy against other flaky-test tools. Fair benchmarking of someone
  else's tool requires understanding their tuning, and getting that wrong would be
  worse than not doing it.
- Machine-learned scoring. The current rules are auditable, which for this problem
  is worth more than a few points of F1.
- A hosted service.

## Acceptance criteria

1. `flaky benchmark` reports precision and recall per verdict, reproducible from a
   seed.
2. The regression-misreported-as-flaky rate is measured and published.
3. Merging two databases produces the same analysis regardless of merge order, and
   merging twice is a no-op.
4. The GitHub Action is demonstrated working in this repository's own CI.
5. `flaky blame` identifies the introducing commit on synthetic data where the
   answer is known.
6. Every threshold change made in this round is justified by a benchmark number,
   not by opinion.
