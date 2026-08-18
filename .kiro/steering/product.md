# Product context

Flaky Test Detective finds and diagnoses flaky tests from JUnit XML that test
runners already produce.

## The one thing to keep in mind

The tool's credibility rests on never crying wolf. A false "this is flaky" label
on a real regression is the worst possible failure mode, because it teaches the
user to re-run instead of investigate, which is the exact habit this tool exists
to break.

So: when evidence is weak, say the evidence is weak. Never round a guess up to a
verdict. Every score in output must be traceable to observations the user can
inspect.

## Design principles

**Evidence over inference.** Same-commit pass/fail divergence is proof. Flip rate
is a hint. Message pattern matching is a guess. Weight and present them
accordingly, and always show the underlying counts.

**Zero setup.** SQLite file, no server, no accounts, no network. A tool for a
side-concern like test health gets exactly one chance to be easy.

**Language agnostic.** The tool reads JUnit XML, never source. This is why it
works for pytest, jest, go, JUnit, and .NET without knowing anything about them.
Resist any feature that requires reading test source, since it would break this.

**Actionable, not merely accurate.** "This test is flaky" is not useful. "This
test fails when it runs after `test_seeds_cache`, 12 of 14 times, likely shared
state" is. Push every output toward the second form.

## Vocabulary

Use these terms consistently in code, output, and docs:

- **flaky** — different outcomes for the same code
- **regression** — consistent failure that used to pass
- **broken** — has never passed in recorded history
- **fixed** — was flaky, now stable for N consecutive runs
- **divergence** — a pass and a fail at the same commit SHA
- **flip** — a pass↔fail transition between consecutive runs
- **signature** — normalized failure message used as a cluster key
- **hunt** — deliberately re-running a suite to provoke flakes
- **triage** — deciding whether this run's failures are known flakes or new

Do not introduce synonyms. "Intermittent", "unstable", and "unreliable" all mean
flaky and using them interchangeably makes output harder to scan.

## Output tone

Reports are read by someone whose build just went red and who is mildly annoyed.
Lead with the answer. No preamble, no celebration, no emoji in report bodies.
Numbers with units. Never claim certainty the data does not support.
