# Verification walkthrough

Thirteen steps, each one a command you can run and a result you can check. Nothing
here needs credentials, an API key, a paid service or network access.

This lives outside the README on purpose: it is a *checklist to execute*, not prose to
read, and at 270 lines it buried the parts of the README that are meant to be read.

&larr; [Back to the README](../README.md)

No credentials, no API keys, no network access, no paid services.

**Linux, macOS and Windows are all tested in CI**, natively — no WSL needed. The commands
below use `/tmp` for scratch files, so on Windows either run them in Git Bash (which ships
with Git for Windows and maps `/tmp`) or substitute any writable directory.

What "tested" means here is narrower than a green test run, deliberately: pytest captures
stdout through a UTF-8 buffer, so the failure that actually bites on Windows — a redirected
console using the locale codepage, meeting the block characters in the verification bars —
cannot show up in the suite at all. CI therefore also runs the installed console script
with its output redirected and `PYTHONIOENCODING=cp1252`, on all three platforms, and
checks the files come back as UTF-8 with LF endings. Details and the four defects this
found in **[ADR-0017](adr/0017-windows-is-a-supported-platform.md)**.

```sh
git clone https://github.com/ahmedhashmu/flaky-test-detective
cd flaky-test-detective
uv sync
```

Substitute `pip install -e ".[dev]"` for `uv sync` and drop the `uv run` prefixes to use
pip instead.

**1. Test suite** — 1,077 tests, about 30 seconds:

```sh
uv run pytest
```

40 of those are property-based, not example-based: Hypothesis generates histories and
checks *relationships* rather than one hand-picked outcome. A test that never passed is
never labelled flaky, merging two databases is commutative and idempotent, ingesting runs
in reverse cannot change a verdict, and the JSON report cannot disagree with the analysis
it renders. Run them alone with:

```sh
uv run pytest tests/test_properties.py
```

One of them checks the *generator*, and fails if the generated histories stop reaching
flaky, broken, regression and never-passed states — because a property that never sees the
interesting case passes while proving nothing. Reasoning, and the two things this exercise
corrected about what the code was believed to guarantee, in
**[ADR-0016](adr/0016-assert-relationships-not-only-examples.md)**.

**2. See it working immediately**, with no suite of your own and no waiting:

```sh
uv run flaky demo --db /tmp/judge.db
```

A browser opens on a populated dashboard. The banner at the top says the history was
generated; the verdicts under it were not. Add `--no-serve` to build the data without
opening anything.

Two things worth checking here, because they are what separate this from a screenshot:

- `uv run flaky analyze --db /tmp/judge.db` shows the same verdicts the dashboard does,
  because both call the same `analyze()`.
- The order-dependent flake names a polluter that is a real test in the database, and the
  investigation page for it splits *proven* evidence (same-commit divergence, polluter
  correlation) from *inferred* (flip rate).

**3. See its measured accuracy.** This is the fastest way to judge whether the tool works:

```sh
uv run flaky benchmark
```

Expect a false-alarm rate of **0.0%** and accuracy around 93%. Try `--seed 99`, or
`--sweep coverage` to watch accuracy collapse without commit data.

**4. Watch it find real flakes.** `examples/flaky_demo/` has genuine nondeterminism: real
threads racing real deadlines, an unsynchronized counter, a loopback socket race, unseeded
randomness, and module-level state leaking between tests. Nothing is simulated with a coin
flip on a hardcoded list.

```sh
uv run flaky hunt -n 20 --db /tmp/demo.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

uv run flaky analyze --db /tmp/demo.db
```

Expect ~10 flaky tests, then check the three things that matter:

- The four `test_stable_*` tests must score **0.00**. Without controls, a tool that flagged
  everything would look identical to a working one.
- `test_known_broken` must be **broken**, never flaky.
- `test_expects_clean_registry` should be **order dependent**, naming
  `test_registers_session` as the polluter.

**5. Turn one of those flakes into a command that fails on demand.** This is the step that
separates a detector from an investigation tool, and it takes about a minute:

```sh
uv run flaky reproduce test_expects_clean_registry --db /tmp/demo.db -- \
  uv run pytest
```

It measures the test alone first, then delta-debugs the recorded predecessors. Expect:

- **Reproduced on demand**, with `15 candidates reduced to 1`.
- The named test in the printed sequence is `test_registers_session` — the actual polluter,
  which you can confirm by reading `examples/flaky_demo/test_shared_state.py`.
- `20/20 failed` in that order against `0/20` alone.

Then verify the tool's output independently, without the tool:

```sh
uv run pytest -p no:randomly \
  examples/flaky_demo/test_shared_state.py::test_registers_session \
  examples/flaky_demo/test_shared_state.py::test_expects_clean_registry   # 1 failed, 1 passed

uv run pytest -p no:randomly \
  examples/flaky_demo/test_shared_state.py::test_expects_clean_registry   # 1 passed
```

Now try one where there is nothing to isolate, which is the more important half:

```sh
uv run flaky reproduce test_worker_finishes_within_deadline --db /tmp/demo.db -- \
  uv run pytest
```

Expect **Fails on its own** and **no blamed neighbour**. That test is a timing race, and a
search without a measured control would have happily named whichever test it was holding.

**6. Open the dashboard** on the database you just built:

```sh
uv run flaky serve --db /tmp/demo.db
```

No `npm` step: the compiled bundle ships inside the package, and CI rebuilds it on every
push to prove the committed copy is current. The server binds `127.0.0.1` and opens the
database read-only.

Check that the trust score is decomposed rather than asserted. The listed penalties sum to
exactly the points deducted, and the headline number is that deduction rounded to a whole
number — nothing else sits in between:

```sh
curl -s http://127.0.0.1:8420/api/overview | python3 -c "
import json, sys
t = json.load(sys.stdin)['trust']
print('components sum :', round(sum(c['penalty'] for c in t['components']), 1))
print('deducted       :', t['deducted'])
print('score          :', t['score'], '==', round(100 - t['deducted']))"
```

Then click any flaky test. The investigation page separates **proven** evidence
(same-commit divergence, runner-recorded retries, polluter correlation) from **inferred**
signals (flip rate), because a pattern match must not borrow the authority of a
measurement. Every number on the page comes from the same `analyze()` the CLI calls;
`tests/test_web.py` asserts the payload verdicts match it exactly.

**7. Export an issue body** for the tracker of your choice:

```sh
uv run flaky issue test_expects_clean_registry --db /tmp/demo.db -f markdown
uv run flaky issue test_expects_clean_registry --db /tmp/demo.db -f slack
```

It prints; it never posts. There is no credential to supply and nothing leaves the machine.

**8. Prove it can tell whose fault flakiness is.** The demo suite ships a deterministic
mode, so the same tests can be recorded stable and then genuinely flaky — exactly the
before/after a pull request creates:

```sh
FLAKY_DEMO_DETERMINISTIC=1 uv run flaky hunt -n 20 --db /tmp/base.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

uv run flaky hunt -n 20 --db /tmp/pr.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

uv run flaky compare --baseline /tmp/base.db --head /tmp/pr.db ; echo "exit: $?"
```

Expect roughly 8–10 tests reported as **newly flaky** with high confidence, each naming
same-commit divergence as the evidence, and exit 1.

Then three things worth checking, because they are where this is easy to get wrong:

- `test_known_broken` fails every run on **both** sides. It must be reported `unchanged`,
  never as a new break — it was already broken.
- Reverse the arguments (`--baseline /tmp/pr.db --head /tmp/base.db`). It must report **0
  introduced** and around 10 `improved`. A comparison that is not antisymmetric is not
  measuring what changed.
- Some tests land in "not enough evidence to attribute". That is the intended answer at 20
  runs a side, not a bug. Re-record the baseline with `-n 60` and watch them move.

**9. Watch it refuse to certify a fix it cannot prove.** Record flaky history, then "fix"
the tests by switching the demo suite to deterministic mode:

```sh
uv run flaky hunt -n 30 --db /tmp/fix.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

TEST=examples/flaky_demo/test_timing.py::test_worker_finishes_within_deadline

FLAKY_DEMO_DETERMINISTIC=1 uv run flaky verify "$TEST" -n 4  --db /tmp/fix.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q

FLAKY_DEMO_DETERMINISTIC=1 uv run flaky verify "$TEST" -n 40 --db /tmp/fix.db -- \
  uv run pytest examples/flaky_demo -p no:cacheprovider -q
```

The first call is 4 for 4 clean and reports **Cannot say yet**, naming how many clean runs
the old failure rate actually requires. The second clears that bar and reports **Fixed**,
with the probability of the streak, the failure rate before and after, and a check that
nothing else broke.

That refusal is the feature. A clean streak is only evidence in proportion to the rate it
is replacing, and for an order-dependent flake it is worth nothing at all unless the
polluting order was actually exercised — which `verify` also counts.

**10. Triage**, the CI gate:

```sh
uv run pytest examples/flaky_demo -q --junitxml=/tmp/run.xml ; true
uv run flaky triage /tmp/run.xml --db /tmp/demo.db ; echo "exit: $?"
```

Several tests failed; it should report only `test_known_broken` as needing attention, and
exit 2.

**11. Merge history from two machines:**

```sh
uv run flaky hunt -n 6 --db /tmp/a.db -- uv run pytest examples/flaky_demo -q
uv run flaky hunt -n 6 --db /tmp/b.db -- uv run pytest examples/flaky_demo -q
uv run flaky merge /tmp/b.db --into /tmp/a.db     # 12 runs
uv run flaky merge /tmp/b.db --into /tmp/a.db     # no-op, idempotent
```

**12. Verify the quarantine export really works:**

```sh
uv run flaky quarantine recommend --db /tmp/demo.db --apply
uv run flaky quarantine export -f pytest-conftest -o /tmp/qp/qplugin.py
PYTHONPATH=/tmp/qp uv run pytest examples/flaky_demo -p qplugin -q -rs
```

Quarantined tests are reported as skipped with a reason. `test_known_broken` still fails,
because quarantine never hides a real failure.

**13. Confirm this project's own suite is not flaky** — a reasonable thing to demand of this
particular tool:

```sh
uv run flaky hunt -n 3 --db /tmp/self.db -- \
  uv run pytest -q -m "not integration" -p no:cacheprovider
uv run flaky analyze --db /tmp/self.db --fail-on flaky ; echo "exit: $?"
```

Expect `0 flaky` and exit 0.

### Notes

- The demo suite is genuinely random, so exact numbers vary. The three checks in step 3
  hold every time.
- `FLAKY_DEMO_DETERMINISTIC=1` makes the demo deterministic (all green except
  `test_known_broken`).
- `examples/` is excluded from this project's own collection via `norecursedirs`, so the
  deliberately flaky suite cannot break the build.

