# Flaky Test Detective

Find and diagnose flaky tests from the JUnit XML your test runner already produces.

```
score  verdict     runs      p/f  flips  commit  cause      test
 0.91  flaky         20    10/10     13     1/1  timeout    test_timing.py::test_worker_fin…
 0.84  flaky         20     9/11      9     1/1  order      test_shared_state.py::test_expe…
 0.83  flaky         20     14/6      8     1/1  network    test_network.py::test_client_co…
 0.00  broken        20     0/20      0     0/1  assertion  test_stable.py::test_known_brok…
```

---

## The problem

A flaky test produces different outcomes for the same code. Teams handle them one
of two ways, and both are bad:

1. **Retry everything.** `--reruns 3` on the whole suite. Real regressions get
   masked and CI time triples.
2. **Ignore red builds.** Once the build is "always a bit red", failures stop
   carrying information and genuine breakage ships.

The blocker is not willingness to fix flakes. It is that you cannot *identify*
them. A single failed CI run is indistinguishable from a real regression at the
moment you look at it. Telling them apart needs history across many runs,
correlated with the commit under test, and almost nobody has that in a queryable
form. CI providers keep logs, not structured outcomes.

## What this does differently

**It looks for proof, not patterns.** The primary signal is *same-commit
divergence*: one test, one commit SHA, both a pass and a fail. The code was
byte-identical, so the code is not the variable. That is evidence, not inference.

Flip rate (pass↔fail transitions over time) is used too, but weighted lower and
capped, because a single pass→fail transition is far more likely to be a
regression than a flake. Every score is shown alongside the counts it came from,
so you can check the arithmetic instead of trusting it.

**It refuses to cry wolf.** A test that fails consistently is reported as
`broken` or `regression`, never as flaky, because labelling a real break "flaky"
teaches you to re-run instead of investigate. That is the exact habit this tool
exists to break.

**It tells you what to fix, not just what is broken.** "This test is flaky" is
useless. "This test fails whenever it runs after `test_registers_session`, 100% of
the time, so reset that shared state in teardown" is a diff.

**It works for any language.** The tool reads JUnit XML and never reads your
source, so pytest, jest, go, JUnit, Gradle and .NET all work without it knowing
anything about them.

## Install

Requires Python 3.11 or newer. No services, no accounts, no network access, no
credentials.

```sh
git clone https://github.com/ahmedhashmu/flaky-test-detective
cd flaky-test-detective
uv sync                     # or: pip install -e ".[dev]"
uv run flaky --help
```

To use it against your own project:

```sh
uv tool install .           # puts `flaky` on your PATH
```

## Quick start

Two ways in. Either hunt for flakes now, or feed in reports you already have.

### Hunt: provoke flakes locally

```sh
flaky hunt -n 20 -- pytest tests/
```

This runs your suite 20 times, randomizing test order between runs, and records
every outcome. Real output against this repo's demo suite:

```
Hunting with pytest: 20 iterations, order randomization on.
    1/20    0.2s    7 failed  0 flaky so far
    2/20    0.2s    3 failed  8 flaky so far
    3/20    0.3s    6 failed  9 flaky so far
    ...
   20/20    0.3s    5 failed  10 flaky so far
Collected 20 of 20 iterations in 4.9s.
Found 10 flaky tests. Run `flaky analyze` for detail.
```

### Ingest: use the reports CI already produces

```sh
flaky ingest 'reports/**/*.xml'
```

Commit SHA, branch and CI run id are detected automatically from git and from
GitHub Actions, GitLab, CircleCI, Jenkins, Buildkite, Azure and Travis. Ingest is
idempotent: re-presenting the same report is a no-op, so CI retries cannot
double-count.

### Then look at the results

```sh
flaky analyze
```

```
╭─ 20 runs, 320 results, 16 tests 2026-08-18 to 2026-08-18 ────────────────────────╮
│ 10 flaky  1 broken                                                               │
╰──────────────────────────────────────────────────────────────────────────────────╯
All under examples/flaky_demo/
score  verdict     runs      p/f  flips  commit  cause      test
 0.91  flaky         20    10/10     13     1/1  timeout    test_timing.py::test_worker_fin…
 0.87  flaky         20     12/8     11     1/1  race       test_concurrency.py::test_appen…
 0.84  flaky         20     11/9      9     1/1  race       test_concurrency.py::test_count…
 0.84  flaky         20     9/11      9     1/1  order      test_shared_state.py::test_expe…
 0.83  flaky         20     14/6      8     1/1  network    test_network.py::test_client_co…
 0.83  flaky         20    10/10      8     1/1  time       test_timing.py::test_token_stil…
 0.81  flaky         20     12/8      7     1/1  order      test_shared_state.py::test_coun…
 0.81  flaky         20     16/4      7     1/1  assertion  test_timing.py::test_batch_comp…
 0.79  flaky         20     9/11      6     1/1  random     test_randomness.py::test_shuffl…
 0.76  flaky         20     2/18      4     1/1  random     test_randomness.py::test_sample…
 0.00  broken        20     0/20      0     0/1  assertion  test_stable.py::test_known_brok…

Diagnosis
  examples/flaky_demo/test_shared_state.py::test_expects_clean_registry
    order dependent: fails after test_shared_state.py::test_registers_session
    in 100% of its failures
    also runs later when it fails: position 10 on average versus 6 when passing
    Reset the shared state in setup or teardown so the outcome does not depend
    on what ran before it.
  examples/flaky_demo/test_network.py::test_client_connects_once_server_is_listening
    likely network (matched: connection refused)
    Stub the network boundary. A test that reaches a real host is testing
    someone else's uptime.
```

Note the last row. `test_known_broken` fails every single run, so it is reported
as **broken**, not flaky. That distinction is the difference between exit code 2
and exit code 1.

## The command you will actually use most

`flaky triage` answers the question you have when CI goes red: **investigate, or
re-run?**

```sh
flaky triage reports/junit.xml
```

```
╭──────────────────────────────────────────────────────────────────────────────────╮
│ 1 failure needs attention (5 known flakes ignored)                               │
╰──────────────────────────────────────────────────────────────────────────────────╯
Known regressions or broken tests
   examples/flaky_demo/test_stable.py::test_known_broken
      AssertionError: expected 42, got 41 -- consistent, not intermittent

Known flakes
  test_network.py::test_client_connects_once_server_is_listening
      score 0.83, 6/20 runs failed
  test_timing.py::test_token_still_valid_at_check_time
      score 0.83, 10/20 runs failed
  ...
```

Six tests failed. One matters. Exit code 2.

History is evaluated with the triaged run **excluded**, so a first-time failure
cannot use the evidence of itself to argue that it is flaky.

## All commands

| Command | What it does |
|---|---|
| `flaky init` | Write a commented `.flaky.toml` and create the database |
| `flaky ingest <paths…>` | Parse JUnit XML files, directories or globs |
| `flaky hunt -- <cmd>` | Run a test command N times, recording every outcome |
| `flaky analyze` | Ranked flakes with diagnosis, to the terminal |
| `flaky triage <report>` | Known flakes vs new breakage for one run |
| `flaky report -f md\|json\|html` | Render for a PR comment, a script, or a browser |
| `flaky history <test-id>` | One test's timeline, run by run |
| `flaky stats` | What is in the database |
| `flaky quarantine …` | `list`, `recommend`, `add`, `remove`, `export`, `verify` |

Every command has usable `--help`. A few worth knowing:

```sh
flaky hunt -n 50 --stop-after 3 -- pytest tests/   # stop once 3 flakes are found
flaky hunt -n 20 --no-shuffle -- pytest tests/     # keep the natural order
flaky hunt --report-path target/surefire-reports -- mvn test
flaky analyze --last 50 --branch main              # only recent runs, one branch
flaky analyze --threshold 0.4                      # stricter bar for "flaky"
flaky history test_expects_clean_registry          # partial ids resolve
flaky report -f html -o flaky.html                 # standalone page, no CDN
```

### `flaky hunt` and order randomization

Order randomization is what surfaces order-dependent flakes, and it needs runner
support. The tool **probes for it** by running your command with `--help` and
looking for the flag, rather than guessing from the runner name — because for
pytest it comes from an optional plugin.

| Runner | Report flag injected | Randomization | Needs |
|---|---|---|---|
| pytest | `--junitxml=…` | `--randomly-seed=N` | `pytest-randomly` |
| jest | `JEST_JUNIT_OUTPUT_FILE` env | `--shuffle --seed=N` | jest 29+, `jest-junit` |
| vitest | `--outputFile=…` | `--sequence.shuffle` | vitest |
| go (`gotestsum`) | `--junitfile=…` | `-shuffle=on` | Go 1.17+ |
| anything else | use `--report-path` | not available | — |

If randomization is unavailable it says so loudly rather than running N identical
iterations and letting you believe you had tested for order dependence.

## How it decides

Full reasoning, including two rules that were wrong and had to be corrected, is in
[`.kiro/specs/flaky-test-detective/design.md`](.kiro/specs/flaky-test-detective/design.md).
The short version:

```
divergence_rate = commits where the test both passed and failed
                  ÷ commits where it ran more than once

flip_rate       = pass↔fail transitions ÷ (runs − 1)

raw             = 0.7 · divergence_rate + 0.3 · flip_rate
confidence      = min(1, runs ÷ 10)
score           = raw · (0.5 + 0.5 · confidence)
```

With no commit SHAs available, the weight falls back onto flip rate alone but is
capped at 0.85 — inference must not be able to reach the same ceiling as proof.

### Verdicts

Exactly one applies to each test.

| Verdict | Meaning |
|---|---|
| `flaky` | Different outcomes for the same code |
| `regression` | Consistent failure that used to pass |
| `broken` | Has never passed in recorded history |
| `fixed` | Was flaky, now stable for N consecutive runs |
| `stable` | Everything else |

### Root-cause categories

Heuristics, and labelled as such. The matched terms are always shown so you can
overrule them. `order_dependence` is the exception: it is measured from run
positions and predecessors, not guessed from text, and it names the polluting test.

`timeout` · `race` · `order_dependence` · `network` · `resource` ·
`time_dependence` · `randomness` · `assertion`

## CI integration

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean |
| `1` | Flaky tests found, nothing else needing a human |
| `2` | Regression or broken test found |
| `3` | Usage or input error |

`analyze` and `report` default to `--fail-on none`, so reading a report never
fails a shell. `triage` defaults to failing, because gating is its purpose.

### GitHub Actions

Gate a pull request on new breakage while tolerating known flakes:

```yaml
- name: Run tests
  run: pytest --junitxml=reports/junit.xml
  continue-on-error: true

- name: Restore flake history
  uses: actions/cache@v4
  with:
    path: .flaky.db
    key: flaky-db-${{ github.run_id }}
    restore-keys: flaky-db-

- name: Triage
  run: |
    flaky triage reports/junit.xml --ingest --format md >> "$GITHUB_STEP_SUMMARY"
    flaky triage reports/junit.xml
```

Exit 0 means every failure was a known flake. Exit 2 means something needs a
human.

This repository does exactly this to itself in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml), including a step that
hunts its *own* test suite and fails the build if a single flaky test is found.

### Sharing history across machines

The database is one SQLite file. Either cache it in CI (as above), commit it if
your suite is small, or point `--db` at a shared volume. There is deliberately no
server to run.

## Quarantine

Quarantine is a tourniquet, not a cure, so every entry carries an expiry date.

```sh
flaky quarantine recommend                  # dry run
flaky quarantine recommend --apply          # write .flaky-quarantine.json
flaky quarantine export -f pytest-conftest -o conftest_quarantine.py
flaky quarantine verify --release           # re-check expired entries
```

Regressions and broken tests are **never** recommended for quarantine.
Quarantining a real failure is how bugs reach production.

Export formats: `pytest-deselect`, `pytest-conftest`, `jest`, `list`, `json`.
`pytest-conftest` is the one to prefer in CI — it skips tests with a visible
reason rather than removing them silently, because a quarantined test nobody can
see is a quarantined test nobody will fix.

The `jest` export prints the test names and then explains that Jest cannot exclude
by test name. That is a real limitation of Jest, and stating it is better than
emitting a config snippet that looks like it works.

## Configuration

Optional. `flaky init` writes a commented `.flaky.toml`; discovery walks up from
the current directory. Any flag overrides the file.

`COLUMNS` is honoured for output width, which is worth setting in CI so that
report width does not depend on whichever runner picked up the job.

```toml
[flaky]
db = ".flaky.db"
quarantine = ".flaky-quarantine.json"
flake_threshold = 0.15          # score above which a test is called flaky
quarantine_threshold = 0.4      # higher: naming a flake is cheap, removing it is not
confidence_runs = 10            # runs needed before a score is fully trusted
fixed_run_streak = 10           # consecutive passes before "fixed"
hunt_iterations = 10
quarantine_days = 14
ignore = []                     # test id substrings to exclude
```

## JSON output

Stable, documented shape, versioned by `schema_version`. Every derived number
comes with the counts behind it.

```jsonc
{
  "schema_version": 1,
  "summary": {
    "runs": 20, "results": 320, "tests": 16,
    "flaky": 10, "regressions": 0, "broken": 1, "fixed": 0,
    "has_commit_data": true,        // false means scores rest on the weaker signal
    "commit_coverage": 1.0
  },
  "tests": [{
    "test_id": "…", "verdict": "flaky", "score": 0.91,
    "evidence": {
      "runs": 20, "passes": 10, "failures": 10, "flips": 13,
      "divergent_commits": 1, "observed_commits": 1, "confidence": 1.0
    },
    "cause": { "category": "timeout", "matched": ["timed out"], "remediation": "…" },
    "order_dependence": null
  }],
  "clusters": [{ "signature": "…", "test_ids": ["…"], "test_count": 3 }]
}
```

## Supported runners

| Runner | Status |
|---|---|
| pytest | **Verified** against real output (pytest 9, default `xunit2`) |
| jest (`jest-junit`) | **Verified** against real output (jest 29, jest-junit 16) |
| go (`go-junit-report`) | Parser written to the documented shape, not validated live |
| Maven Surefire | Parser written to the documented shape, including `<flakyFailure>` |
| Gradle | Nested `<testsuite>` handled |
| .NET (`trx2junit`) | Parser written to the documented shape, not validated live |

The distinction is honest and matters: no Go, JVM or .NET toolchain was available
on the build machine, so those parsers are structurally faithful but unproven
against live runner output. Details in
[`tests/fixtures/README.md`](tests/fixtures/README.md).

Where a runner records its own retries (Surefire's `<flakyFailure>`,
`pytest-rerunfailures`), that is treated as direct evidence of flakiness — the
runner watched one test produce two outcomes in a single run.

## Limitations

Stated plainly, because a tool about trustworthy signals should be honest about
its own.

- **Order dependence only checks the immediately preceding test.** A polluter
  running several tests earlier is missed. Checking every earlier test for every
  candidate is quadratic in suite size.
- **Root-cause categories are heuristics.** They will misclassify. The matched
  terms are shown so you can tell when they have.
- **No commit SHAs means weaker conclusions.** The tool says so in every output
  format rather than presenting flip-rate-only scores as equally sound.
- **JUnit XML only.** No TAP or Allure yet.
- **It does not fix anything.** Root causes are semantic.
- **Test ids must be stable across runs.** Parameterized ids containing random
  values are normalized, but a runner that renames tests between runs will
  fragment history.

## How Kiro was used

This project was built with Kiro using its spec-driven workflow. The `.kiro/`
directory is the record, and it is worth reading rather than taking on trust.

**Specs** — [`.kiro/specs/flaky-test-detective/`](.kiro/specs/flaky-test-detective/)
holds `requirements.md` (37 numbered functional requirements plus acceptance
criteria), `design.md` (architecture, the scoring maths, and the trade-offs), and
`tasks.md` (20 implementation tasks, each mapped back to requirements).
Requirements and design were settled before implementation, and the design
document was updated whenever reality disagreed with it.

**Steering** — [`.kiro/steering/`](.kiro/steering/) holds three always-on
documents that shaped every file: `product.md` (the "never cry wolf" principle and
a fixed vocabulary), `tech.md` (XML safety, error-handling categories,
parameterized SQL, determinism rules), and `structure.md` (the one-way dependency
direction). Those rules are not decoration — `tests/test_architecture.py` turns
them into 60 enforced assertions.

**Hooks** — [`.kiro/hooks/`](.kiro/hooks/) holds three: lint on save, an
architecture guard that fires on any change under `analysis/` or `report/`, and
the fast test suite after each spec task.

### The part worth actually looking at

The most useful thing Kiro did was catch its own mistakes by measuring output
against the demo suite. Three rules were written, tested, found wrong, and
rewritten. All three are documented in `design.md` with the measurements:

1. **Order dependence, v1.** Separation of mean positions divided by pooled
   standard deviation, flagged above 1.0. Misfired: labelled a purely random test
   order-dependent at 1.1σ.
2. **Order dependence, v2.** Added a sample-size-aware t-statistic and made
   predecessor correlation an independent trigger. Misfired differently: flagged
   eight of ten demo tests with a reported confidence of 100%, because in a
   shuffled ten-test suite a given predecessor precedes the victim only three or
   four times, and a test that already fails 70% of the time will fail all four by
   chance about a quarter of the time.
3. **Order dependence, v3 (final).** Measuring 40 shuffled iterations showed the
   two *strongest* position signals were both timing flakes (t = 3.47), while the
   two genuinely order-dependent tests scored t ≈ 2.3. Position tracks machine
   warm-up, not state pollution. Detection now requires naming a polluter that
   beats the test's own base failure rate. Result: exactly the two real victims,
   both correctly naming `test_registers_session`.

Two more corrections came from writing tests: regression detection was
mislabelling unlucky flakes, and flip-rate-only scoring could reach 1.00 — the
same as a score backed by proof.

## Testing instructions

No credentials, no API keys, no network access, no paid services. Everything runs
locally.

```sh
git clone https://github.com/ahmedhashmu/flaky-test-detective
cd flaky-test-detective
uv sync
```

**1. Run the test suite** — 426 tests, about 5 seconds on a laptop:

```sh
uv run pytest
```

**2. Watch it find real flakes.** `examples/flaky_demo/` is a suite with genuine
nondeterminism: real threads racing real deadlines, an unsynchronized counter, a
loopback socket race, unseeded randomness, and module-level state leaking between
tests. Nothing is simulated with a coin flip on a hardcoded list.

```sh
uv run flaky hunt -n 20 --db /tmp/demo.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

uv run flaky analyze --db /tmp/demo.db
```

Expect roughly 10 flaky tests. Then check the three things that matter:

- The four `test_stable_*` tests must score **0.00**. A tool that flagged
  everything would look identical to a working one without these controls.
- `test_known_broken` must be **broken**, never flaky. It fails every run.
- `test_expects_clean_registry` should be **order dependent**, naming
  `test_registers_session` as the polluter.

**3. Try triage**, the gate you would put in CI:

```sh
uv run pytest examples/flaky_demo -q --junitxml=/tmp/run.xml ; true
uv run flaky triage /tmp/run.xml --db /tmp/demo.db ; echo "exit: $?"
```

Several tests will have failed. It should tell you only `test_known_broken`
needs attention, and exit 2.

**4. Check the quarantine export really works:**

```sh
uv run flaky quarantine recommend --db /tmp/demo.db --apply
uv run flaky quarantine export -f pytest-conftest -o /tmp/qp/qplugin.py
PYTHONPATH=/tmp/qp uv run pytest examples/flaky_demo -p qplugin -q -rs
```

The quarantined tests are reported as skipped with a reason. `test_known_broken`
still fails, because quarantine never hides a real failure.

**5. Verify the project's own suite is not flaky.** Reasonable thing to demand of
this particular tool:

```sh
uv run flaky hunt -n 3 --db /tmp/self.db -- \
  uv run pytest -q -m "not integration" -p no:cacheprovider
uv run flaky analyze --db /tmp/self.db --fail-on flaky ; echo "exit: $?"
```

Expect `0 flaky` and exit 0.

### Notes

- The demo suite is genuinely random, so exact numbers vary run to run. The three
  checks in step 2 hold every time.
- To make the demo deterministic (all green except `test_known_broken`), set
  `FLAKY_DEMO_DETERMINISTIC=1`.
- `examples/` is excluded from this project's own test collection via
  `norecursedirs`, so the deliberately flaky suite cannot break the build.

## Costs and limits

None. No third-party APIs, no hosted services, no rate limits, no accounts, no
network access at any point.

## Built with

- [Typer](https://typer.tiangolo.com/) — CLI framework (MIT)
- [Rich](https://rich.readthedocs.io/) — terminal formatting (MIT)
- [pytest](https://pytest.org/), [ruff](https://docs.astral.sh/ruff/),
  [mypy](https://mypy-lang.org/), [uv](https://docs.astral.sh/uv/) — development
- [pytest-randomly](https://github.com/pytest-dev/pytest-randomly) — order
  randomization for the demo (dev only)

Runtime dependencies are Typer and Rich, and nothing else. XML parsing, storage
and hashing all use the Python standard library.

Test fixtures were captured from real pytest and jest-junit output. The go,
Surefire and .NET fixtures were written to their documented formats; provenance
for each is recorded in [`tests/fixtures/README.md`](tests/fixtures/README.md).

## License

MIT. See [LICENSE](LICENSE).
