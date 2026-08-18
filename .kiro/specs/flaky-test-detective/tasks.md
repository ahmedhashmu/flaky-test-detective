# Implementation Plan: Flaky Test Detective

Tasks are ordered so that each one is verifiable when it lands. The analysis
modules come before the CLI so they can be tested as pure functions.

- [ ] 1. Project scaffolding
  - `pyproject.toml` with `uv` support, `typer` + `rich` runtime deps, `pytest` +
    `ruff` + `mypy` dev deps, `flaky` console script
  - Package skeleton under `src/flaky_detective/`
  - `ruff` and `mypy` configured strictly enough to be useful, loosely enough not
    to fight generated code
  - _Requirements: NFR3, NFR4_

- [ ] 2. Core data models
  - `TestOutcome`, `TestRun`, `Status` enum, `FlakeVerdict`, `TestAnalysis`
  - Frozen dataclasses; `slots=True` since these are allocated per result row
  - `test_id` construction and normalization
  - _Requirements: FR1.1, FR2.5_

- [ ] 3. Message normalization and signatures
  - Ordered substitution pipeline per design table
  - `normalize_message`, `signature_of`
  - Unit tests covering each substitution and their interaction order
  - _Requirements: FR3.1_

- [ ] 4. SQLite storage layer
  - Schema, WAL, indexes, `meta` version row
  - `Storage` context manager; `add_run` returning existing id on duplicate
    `run_uid` for idempotency
  - Query helpers: results by window, by test, distinct commits, stats
  - _Requirements: FR1.4, NFR1, NFR2_

- [ ] 5. JUnit XML parser
  - Recursive `testsuite` walk, since Maven nests and pytest does not
  - Dialect handling for pytest, jest, go-junit-report, surefire, trx2junit
  - Runner auto-detection from structural fingerprints
  - Skip/error/failure distinction; `position` assignment in document order
  - Malformed-file tolerance returning a partial result plus a diagnostic
  - Tests using real fixture XML from each dialect
  - _Requirements: FR1.1, FR1.2, FR1.5_

- [ ] 6. Environment and git metadata detection
  - Commit SHA and branch from `git rev-parse`, with graceful failure outside a
    repo
  - CI detection: GitHub Actions, GitLab, CircleCI, Jenkins, Buildkite
  - Explicit flags override everything detected
  - _Requirements: FR1.3_

- [ ] 7. Flakiness engine
  - Same-commit divergence, flip rate, confidence-weighted score
  - Verdict assignment: flaky / regression / broken / fixed / stable
  - Weight renormalization when no commit data exists
  - Tests: known-flaky, known-regression, never-passed, recovered sequences
  - _Requirements: FR2.1–FR2.5_

- [ ] 8. Failure clustering
  - Group by signature, rank by test count then failure count
  - Representative message per cluster
  - _Requirements: FR3.2_

- [ ] 9. Root-cause classifier
  - Rule table with weights; return category plus matched evidence
  - Remediation hint per category
  - Tests asserting each category fires on a realistic message
  - _Requirements: FR3.3_

- [ ] 10. Order-dependence detector
  - Position separation statistic with minimum-sample guard
  - Predecessor correlation to name the likely polluting test
  - Tests with synthetic position data, including a negative case
  - _Requirements: FR3.4_

- [ ] 11. Re-run driver
  - `subprocess` execution, temp XML per iteration, ingest through the shared
    parser path
  - Per-runner randomization injection; explicit warning when unavailable
  - Progress streaming, early stop on N distinct flakes
  - _Requirements: FR4.1–FR4.4_

- [ ] 12. Reporters
  - Console via `rich` tables
  - Markdown sized for a PR comment
  - JSON with a documented shape
  - Single-file HTML, no external requests
  - Triage view: known flake vs new failure for one report
  - _Requirements: FR5.1–FR5.5_

- [ ] 13. Quarantine management
  - JSON-backed list with reason, score, expiry, added-at
  - Exporters: pytest deselect, pytest markers, jest ignore patterns, generic
  - `verify` re-checking expired entries against current history
  - _Requirements: FR6.1–FR6.4_

- [ ] 14. Configuration
  - `.flaky.toml` discovery walking up from cwd
  - Thresholds, paths, ignore patterns; flags take precedence
  - _Requirements: FR7.2_

- [ ] 15. CLI
  - All commands from the design surface
  - Exit codes 0/1/2/3 per FR7.1
  - `--help` text that stands alone without the README
  - _Requirements: FR7.1, FR7.3_

- [ ] 16. Demo suite with real flakes
  - `examples/flaky_demo/`: tests that are genuinely intermittent, not simulated
    — a time-boundary race, a shared-state order dependence, a resource
    contention flake, a seeded-randomness flake, plus stable tests as controls
    and one consistently failing test to prove regression detection
  - Deterministic escape hatch via env var so CI of *this* repo stays green
  - _Requirements: acceptance criteria 1–3_

- [ ] 17. Integration tests
  - Full pipeline: hunt the demo suite, analyze, assert the planted flakes rank
    above the controls
  - Idempotency: double ingest leaves analysis unchanged
  - CLI smoke tests for every command via `typer.testing.CliRunner`
  - _Requirements: acceptance criteria 4–5_

- [ ] 18. CI workflow and Kiro hooks
  - GitHub Actions: lint, type check, test on 3.11–3.13
  - A second job dogfooding the tool on this repo's own suite
  - `.kiro/hooks` for test-on-save and post-task verification
  - _Requirements: FR7.1_

- [ ] 19. Documentation
  - README: problem, install, every command with real output, CI recipes, how
    Kiro was used, testing instructions with no credentials
  - Document exit codes and the JSON shape
  - _Requirements: submission requirements_

- [ ] 20. End-to-end verification
  - Clean clone, `uv sync`, run every documented command, confirm output matches
    the README
  - _Requirements: all_
