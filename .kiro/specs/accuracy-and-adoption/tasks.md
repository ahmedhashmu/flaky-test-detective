# Implementation Plan: Accuracy and Adoption

- [x] 1. Ground-truth generation
  - `benchmark/generate.py`: `GroundTruth`, `Population`, `generate_population()`
  - Every label from the design table, including the hard cases: low-rate flakes,
    high-rate flakes, regressions with flaky history, sparse commit coverage
  - Deterministic from a seed via one threaded `random.Random`
  - _Requirements: FR1.1, FR1.2, FR1.3_

- [x] 2. Scoring
  - `benchmark/score.py`: per-label precision, recall, F1, support
  - Confusion matrix
  - `false_alarm_rate` and `missed_break_rate` as first-class figures
  - _Requirements: FR1.5, FR1.6_

- [x] 3. Harness entry point
  - `benchmark/__init__.py`: `run_benchmark()` calling the real `analysis.analyze`
  - Parameter sweep over run count and commit coverage
  - _Requirements: FR1.4, FR1.7_

- [x] 4. `flaky benchmark` command
  - Console, Markdown and JSON output
  - `--seed`, `--tests`, `--runs`, `--sweep`
  - _Requirements: FR1.8_

- [x] 5. Measure, then act on what the measurement says
  - Record the baseline
  - Change a threshold only where a number justifies it
  - Attempt the order-dependence limitation (immediate predecessor only) and keep
    the change **only** if precision and recall both hold or improve
  - _Requirements: acceptance criterion 6_

- [x] 6. History merging
  - `storage.Storage.merge_from()` using `ATTACH DATABASE`
  - Schema version check per source; remap `results.run_id` through `run_uid`
  - `flaky merge <source…> [--into]`, accepting directories for the shard case
  - _Requirements: FR2.1–FR2.5_

- [x] 7. Flakiness attribution
  - `analysis/attribution.py`: first divergent commit, with the unknowable cases
    reported as unknown rather than guessed
  - `flaky blame <test-id>`
  - _Requirements: FR4.1–FR4.3_

- [x] 8. GitHub Action
  - Composite `action.yml` with cache, ingest, triage, PR comment, outputs
  - PR comment updates in place via a hidden marker
  - Demonstrated in this repository's own CI
  - _Requirements: FR3.1–FR3.6_

- [x] 9. Tests for everything above
  - Generator determinism; benchmark reproducibility from a seed
  - Merge idempotency and order independence
  - Attribution, including every unknowable case
  - _Requirements: acceptance criteria 1–5_

- [x] 10. Documentation
  - `docs/` with architecture (diagrams), scoring, accuracy, CI integration
  - ADRs recording the decisions and the corrections behind them
  - README rebuilt around the measured numbers
  - _Requirements: FR1.8_

- [x] 11. Verification
  - Gates, full suite, clean clone, CI green
  - _Requirements: all_

---

## Outcome

All 11 tasks landed. Final state:

- `flaky benchmark` reports per-label precision and recall over 107 labelled tests,
  reproducible from a seed
- **False alarm rate 0.0%** at 30 runs, on all six seeds tried. Missed break rate
  5.4%, overall accuracy 93.5%. Order-dependence precision and recall both 1.000
- `flaky merge` verified idempotent and order-independent, dogfooded in CI across two
  independently hunted shards
- Composite `action.yml`, used on this repository via `uses: ./`
- `flaky blame`, reporting unknown where the history cannot answer
- `docs/` with architecture diagrams, scoring, accuracy and eight ADRs

Full numbers, including the weak spots, in [`docs/accuracy.md`](../../../docs/accuracy.md).

### What the plan got wrong

**Task 5 was the point of the whole round, and it changed two thresholds that no
amount of reasoning would have got right.**

The streak-beats-chance rule for regression detection cut the missed-break rate from
18.9% to 5.4% while holding false alarms at zero. Then sweeping run count exposed a
**50% false-alarm rate at five runs**, caused by a hard streak floor of three; making
the requirement proportional to available history took that to 12.5%. Neither effect
was predicted. Both are recorded in
[ADR-0006](../../../docs/adr/0006-streak-beats-chance.md).

**Task 2's scorer was measuring the harness's own bug.** It reported polluter precision
of 0.000 while the detector was, on inspection, perfect. Overlapping positions in the
generated data made "ran immediately before" arbitrary, so the ground truth was wrong
rather than the detection. The generator is now tested as carefully as the scorer, which
is now a standing rule in the structure steering. →
[ADR-0007](../../../docs/adr/0007-measure-our-own-accuracy.md)

**Task 7 needed a fourth outcome nobody specified: unknowable.** The plan assumed blame
either finds a commit or does not. In practice there are three distinct ways it cannot
answer — no commit SHAs at all, no commit with more than one run, and divergence at the
very first recorded commit. The last is the dangerous one: naming the earliest commit in
the window would be an accusation the data does not support, and a good way to send
someone reverting an innocent change. All three are now reported as what they are.
