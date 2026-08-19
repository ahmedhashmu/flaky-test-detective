# Implementation Plan: Accuracy and Adoption

- [ ] 1. Ground-truth generation
  - `benchmark/generate.py`: `GroundTruth`, `Population`, `generate_population()`
  - Every label from the design table, including the hard cases: low-rate flakes,
    high-rate flakes, regressions with flaky history, sparse commit coverage
  - Deterministic from a seed via one threaded `random.Random`
  - _Requirements: FR1.1, FR1.2, FR1.3_

- [ ] 2. Scoring
  - `benchmark/score.py`: per-label precision, recall, F1, support
  - Confusion matrix
  - `false_alarm_rate` and `missed_break_rate` as first-class figures
  - _Requirements: FR1.5, FR1.6_

- [ ] 3. Harness entry point
  - `benchmark/__init__.py`: `run_benchmark()` calling the real `analysis.analyze`
  - Parameter sweep over run count and commit coverage
  - _Requirements: FR1.4, FR1.7_

- [ ] 4. `flaky benchmark` command
  - Console, Markdown and JSON output
  - `--seed`, `--tests`, `--runs`, `--sweep`
  - _Requirements: FR1.8_

- [ ] 5. Measure, then act on what the measurement says
  - Record the baseline
  - Change a threshold only where a number justifies it
  - Attempt the order-dependence limitation (immediate predecessor only) and keep
    the change **only** if precision and recall both hold or improve
  - _Requirements: acceptance criterion 6_

- [ ] 6. History merging
  - `storage.Storage.merge_from()` using `ATTACH DATABASE`
  - Schema version check per source; remap `results.run_id` through `run_uid`
  - `flaky merge <source…> [--into]`, accepting directories for the shard case
  - _Requirements: FR2.1–FR2.5_

- [ ] 7. Flakiness attribution
  - `analysis/attribution.py`: first divergent commit, with the unknowable cases
    reported as unknown rather than guessed
  - `flaky blame <test-id>`
  - _Requirements: FR4.1–FR4.3_

- [ ] 8. GitHub Action
  - Composite `action.yml` with cache, ingest, triage, PR comment, outputs
  - PR comment updates in place via a hidden marker
  - Demonstrated in this repository's own CI
  - _Requirements: FR3.1–FR3.6_

- [ ] 9. Tests for everything above
  - Generator determinism; benchmark reproducibility from a seed
  - Merge idempotency and order independence
  - Attribution, including every unknowable case
  - _Requirements: acceptance criteria 1–5_

- [ ] 10. Documentation
  - `docs/` with architecture (diagrams), scoring, accuracy, CI integration
  - ADRs recording the decisions and the corrections behind them
  - README rebuilt around the measured numbers
  - _Requirements: FR1.8_

- [ ] 11. Verification
  - Gates, full suite, clean clone, CI green
  - _Requirements: all_
