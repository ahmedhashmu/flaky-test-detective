# Investigation engine — tasks

Ordered so that the measurement lands before the thing it justifies. Real-world validation
came first on purpose: it produced the 11.6% figure, and that number is what made the
reproducer the right piece to build rather than a guess about what would be impressive.

- [x] 1. Real-world validation against published labels
  - `benchmark/realworld.py`: `score_project`, `score_all`, `load_results`, `ProjectScore`,
    `RealWorldResult`; `ORDER_CATEGORIES`, `NOD`, `NIO`, `EXCLUDED`
  - `validation/`: harness, project manifest, and the committed raw results
  - `report/validation.py`; `flaky validate <results-dir>`
  - CI re-scores and fails if a published figure drifts
  - **Measured:** 12 repositories, 288 suite runs, 41,585 executions, 211 labels. Recall
    99.4% (174/175), precision 100.0% (183/183), 20 consistently-failing labels correctly
    withheld, 0 wrongly called flaky. Order dependence diagnosed **11.6%**.
  - _Requirements: FR1.1–FR1.6, NFR4_

- [x] 2. Branch attribution
  - `analysis/comparison.py`, `models.Change`, `TestComparison`, `ComparisonReport`
  - `flaky compare` with `--baseline/--head` or `--base-branch/--head-branch`
  - `action.yml`: `compare-against`, and the comparison goes first in the PR comment
  - **Measured:** 8 baseline runs admit a rate near 30%, so 5/20 on the head is `unproven`;
    against 60 baseline runs the same observation is a high-confidence new flake
  - _Requirements: FR2.1–FR2.4_

- [x] 3. Fix verification
  - `analysis/statistics.py` extracted here: `cdf_at_most`, `tail_at_least`, `tail_at_most`,
    `upper_bound`, `lower_bound`, `trials_needed`
  - `analysis/verification.py`, `report/verification.py`, `flaky verify`
  - **Three bugs found by running it**, all in the ADR: `math.comb` overflow at 2000 trials
    (now lgamma in log space); before/after split compared ISO *strings* across UTC offsets
    so every run landed on the wrong side; hunt progress on stdout corrupted `--format json`
  - _Requirements: FR3.1–FR3.3_

- [x] 4. `flaky demo`
  - `demo.py` as a producer, reusing `benchmark/generate.py` and the real `analyze()`
  - `DEMO_RUNNER` marker drives a "Demo data" caveat, placed first
  - Refuses to write over non-demo history without `--force`
  - CI builds a wheel, installs it into a clean venv, and runs the two-command path from a
    directory where the source tree is absent
  - _Requirements: FR4.1–FR4.4_

- [x] 5. Windowed polluter search
  - `build_ordering_index(outcomes, window)`; Bonferroni over testable candidates; exact
    binomial tail replacing `base_rate ** fail_count`
  - Generator fixed: `POLLUTER_DISTANCES = (1, 2, 3, 5, 8)` with real spacers in the gap
  - Scorer fixed: `polluter_precision` divides by **named**, not diagnosed — an honest
    refusal was scoring like a wrong attribution
  - **Measured:** generated naming rate up 3.5× (6/24 → 21/24 at window 6), precision 1.000
    at every window. **Real data: no effect.** Published as a negative result, with the gate
    table showing the real ceiling is a median best-candidate share of 0.73
  - Three fixture bugs found, all of which had produced plausible numbers
  - _Requirements: FR5.1–FR5.4, NFR5_

- [x] 6. Environment correlation
  - Schema v2: `run_labels(run_id, key, value)`, additive migration, carried through `merge`
  - `analysis/correlation.py`, `DimensionAssociation`, `flaky ingest --label key=value`
  - `covaries_with` computed from actual run sets, not matching counts
  - **Verified end to end** through real storage and a real merge: `arch=arm64` at 18/23
    against 2/46 elsewhere, lift 18×, with `cpus=2` reported as indistinguishable from it;
    a control test flaky at a similar rate with no architectural relationship got nothing
  - _Requirements: FR6.1–FR6.5_

- [x] 7. Reproducer engine
  - `reproduce.py`: pure `ddmin` plus an injected oracle; `report/reproduction.py`;
    `flaky reproduce <test> -- pytest`
  - Control measured first; exact binomial tail against it; conjunctions findable; two trial
    budgets with the published rate from a fresh confirmation
  - 55 tests against a fake oracle, running in 0.1s
  - **Measured** on `examples/flaky_demo`: 15 candidates reduced to 1 — the true polluter —
    in 8 experiments, 20/20 failures in that order against 0/20 alone, 88 suite runs. The
    printed command was then run independently, outside the tool, and failed 3 times of 3
  - **Negative answers are cheap and honest:** the timing flake reports `fails_alone` with
    nothing blamed, in a third of the cost
  - CI runs the printed command with `sh` and **fails the build if it passes**
  - _Requirements: FR7.1–FR7.8_

- [x] 8. Property-based invariants
  - `tests/test_properties.py`, 40 properties in six groups; Hypothesis as a dev dependency
  - `TestTheGeneratorIsNotVacuous` asserts the generator reaches flaky, broken, regression,
    never-passed, diverged and retried states
  - **No analysis-layer defect found**, and the ADR says so. What it produced instead were two
    corrections to what the code was believed to guarantee, both found while trying to state a
    property precisely enough to be true
  - _Requirements: FR8.1–FR8.3_

- [x] 9. Windows and macOS
  - CI matrix 4 + 2; `tests/test_portability.py` (27 tests) reconstructing platform conditions
  - A CI step running the console script with redirected, cp1252 stdout on all three platforms
  - **Six defects fixed.** Four from a static audit: locale-codepage stdout across eight
    commands, `DETACH` inside an open transaction leaving a source database attached,
    a self-merge guard defeated by a case-insensitive filesystem, POSIX-only quoting in the
    reproducer's printed command. Two the matrix found that the audit could not: `flaky serve`
    silently ignoring an occupied port on Windows, and TOML rejecting a pasted Windows path
  - _Requirements: FR9.1–FR9.3_

- [x] 10. Documentation and final verification
  - ADRs 0011–0017; `docs/real-world.md`; `docs/accuracy.md` rewritten for order dependence
  - Clean-clone verification from the published remote: `uv sync`, full suite, and every
    headline figure recomputed by the commands the README gives
  - Relative links and in-page anchors checked across all markdown
  - _Requirements: NFR4_

## Not done, and why

Recorded rather than dropped, because a task list that quietly loses its unfinished items is
not a record of anything.

- [ ] **Adversarial benchmark with harder ground-truth cases.** The generated benchmark still
  models independent draws, while real flakiness clusters. Worth doing, and the honest reason
  it did not happen is that [ADR-0011](../../../docs/adr/0011-validate-against-real-repositories.md)
  made real repositories the primary accuracy evidence, which lowered the value of making the
  synthetic fixture harder. The limitation is stated in the README.

- [ ] **CI incident clustering.** Grouping failures across a build into one incident rather
  than N test failures. A presentation improvement on top of the existing signature
  clustering, and it would not have changed a verdict.

- [ ] **Validate the go, Surefire and .NET parsers against live runners.** Those toolchains
  were unavailable. The fixtures are real captured output where they could be obtained, and
  `tests/fixtures/README.md` records the provenance of each one so a reader can see which are
  which. Listed in the README's limitations rather than glossed.

- [ ] **Dashboard narrative pass.** The dashboard renders the evidence split into proven and
  inferred, which is the property that matters. Further visualisation work was lower value
  than the reproducer for the same effort.
