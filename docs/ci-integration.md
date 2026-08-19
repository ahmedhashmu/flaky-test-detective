# CI integration

The goal is a build that fails on real breakage and does not fail on known flakes.

Two things are easy to get wrong, so they come first:

1. **History must persist between runs.** Flakiness is only visible across runs. Without a
   persisted database the tool sees one run and detects nothing.
2. **The default branch needs baseline history.** Triaging a pull request against an empty
   history reports everything as new.

## GitHub Actions, the short version

```yaml
name: Tests

on: [push, pull_request]

permissions:
  contents: read
  pull-requests: write   # only needed for the PR comment

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run tests
        run: pytest --junitxml=reports/junit.xml
        continue-on-error: true    # let the tool decide whether this is fatal

      - uses: ahmedhashmu/flaky-test-detective@main
        with:
          report-path: reports/junit.xml
          # On the default branch, accumulate history instead of gating on it.
          ingest-only: ${{ github.ref == 'refs/heads/main' }}
```

`continue-on-error` on the test step is the part people miss. Without it the job stops
before triage, and the tool never gets to say whether the failure mattered.

### Inputs

| Input | Default | Purpose |
|---|---|---|
| `report-path` | *required* | JUnit XML file, directory, or glob |
| `fail-on` | `regression` | `regression`, `flaky`, or `none` |
| `comment` | `true` | Post the triage summary on the PR, updating in place |
| `cache` | `true` | Persist history via the Actions cache |
| `cache-key` | `flaky-history` | Change to start history over |
| `db` | `.flaky.db` | Where history is stored |
| `ingest-only` | `false` | Record results without triaging or failing |
| `python-version` | `3.12` | Runs the tool; unrelated to the suite under test |
| `upload-report` | `true` | Upload HTML and JSON reports as artifacts |

### Outputs

| Output | Meaning |
|---|---|
| `exit-code` | 0 clean, 1 flaky found, 2 needs a human, 3 usage error |
| `actionable` | Failures needing attention |
| `known-flakes` | Failures matched to known flaky tests |
| `all-known-flaky` | `true` when every failure was already known |
| `summary` | One-line verdict |

Branch on them:

```yaml
      - uses: ahmedhashmu/flaky-test-detective@main
        id: flaky
        with:
          report-path: reports/junit.xml

      - name: Label the PR when only known flakes failed
        if: steps.flaky.outputs.all-known-flaky == 'true'
        run: gh pr edit "$NUMBER" --add-label "flaky-only"
        env:
          GH_TOKEN: ${{ github.token }}
          NUMBER: ${{ github.event.pull_request.number }}
```

## Sharded and matrix builds

This is where `flaky merge` matters. Each shard produces its own history; a final job
pools them.

Merging is a set union over content-addressed run ids, so it is idempotent and
order-independent — running the collect job twice, or receiving shards in any order,
gives the same result. See [ADR-0005](adr/0005-content-addressed-runs.md).

```yaml
jobs:
  test:
    strategy:
      matrix:
        shard: [1, 2, 3, 4]
    steps:
      - uses: actions/checkout@v4
      - run: pip install flaky-test-detective

      - run: pytest --shard ${{ matrix.shard }} --junitxml=reports/junit.xml
        continue-on-error: true

      - run: flaky ingest reports/junit.xml --db shard-${{ matrix.shard }}.db

      - uses: actions/upload-artifact@v4
        with:
          name: flaky-shard-${{ matrix.shard }}
          path: shard-${{ matrix.shard }}.db

  collect:
    needs: test
    steps:
      - uses: actions/checkout@v4
      - run: pip install flaky-test-detective

      - uses: actions/download-artifact@v4
        with:
          pattern: flaky-shard-*
          path: shards
          merge-multiple: true

      - uses: actions/cache/restore@v4
        with:
          path: .flaky.db
          key: flaky-history-${{ github.run_id }}
          restore-keys: flaky-history-

      # A directory argument merges every *.db inside it.
      - run: flaky merge shards/

      - run: flaky analyze --limit 30
      - run: flaky report --format md >> "$GITHUB_STEP_SUMMARY"

      - uses: actions/cache/save@v4
        if: always()
        with:
          path: .flaky.db
          key: flaky-history-${{ github.run_id }}
```

## GitLab CI

```yaml
test:
  script:
    - pytest --junitxml=reports/junit.xml || true
    - flaky ingest reports/junit.xml
    - flaky triage reports/junit.xml --format md
    - flaky triage reports/junit.xml    # exit code gates the job
  cache:
    key: flaky-history
    paths: [.flaky.db]
  artifacts:
    when: always
    reports:
      junit: reports/junit.xml
```

Commit SHA, branch and pipeline id are detected automatically from GitLab's environment
variables, as they are for CircleCI, Jenkins, Buildkite, Azure Pipelines and Travis.

## Any CI, manually

```sh
pytest --junitxml=reports/junit.xml || true

flaky ingest reports/junit.xml
flaky triage reports/junit.xml
case $? in
  0) echo "Clean, or only known flakes" ;;
  1) echo "Flaky tests present" ;;
  2) echo "Needs a human"; exit 1 ;;
  3) echo "Configuration problem"; exit 1 ;;
esac
```

Set `COLUMNS` to pin report width so output does not depend on which runner picked up the
job.

## Sharing history without a cache

The database is one SQLite file, so:

- **Cache it** (shown above). Simplest, and history survives as long as the cache entry.
- **Commit it.** Works for small suites, and makes history reviewable in a PR. Expect
  merge conflicts on a busy repository.
- **Artifact plus merge.** Upload per-run, download several, `flaky merge`. Best when
  cache eviction is a problem.
- **Shared volume.** Point `--db` at it. Note that SQLite uses WAL mode here, so
  concurrent readers do not block writers, but a network filesystem with poor locking is
  still a bad host.

## Filling history quickly

Triage needs history, and a new setup has none. Rather than waiting a fortnight:

```sh
flaky hunt -n 20 -- pytest tests/
```

Twenty runs locally, order randomized, in one command. [Measured accuracy](accuracy.md)
shows 10 runs is roughly the point where the tool becomes trustworthy, so this closes the
gap immediately.

## Quarantining in CI

```sh
flaky quarantine recommend --apply
flaky quarantine export -f pytest-conftest -o conftest_quarantine.py
```

Prefer `pytest-conftest` over `pytest-deselect`: it skips tests with a visible reason
rather than removing them silently, and a quarantined test nobody can see is a
quarantined test nobody will fix. Every entry carries an expiry; `flaky quarantine
verify` re-checks the expired ones.

Regressions and broken tests are never recommended for quarantine. Quarantining a real
failure is how bugs reach production.

## What this repository does to itself

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) has a `dogfood` job that hunts
the planted flakes in `examples/flaky_demo`, publishes reports as artifacts, and then
hunts this project's **own** test suite and fails the build if it finds a single flaky
test.

That last step is the one worth copying. A flaky-test detector with flaky tests would not
deserve to be believed about anything.
