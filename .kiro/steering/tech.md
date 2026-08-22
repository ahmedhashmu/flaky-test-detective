# Technical conventions

## Stack

- Python 3.11+ (needs `tomllib`, `StrEnum`, and modern typing)
- `typer` for the CLI, `rich` for terminal output
- `sqlite3` and `xml.etree.ElementTree` from the standard library
- `uv` for environment and dependency management
- `pytest` for tests, `ruff` for lint and format, `mypy` for types

Runtime dependencies stay at two. Every addition is a reason for someone not to
install the tool. Parsing, storage, and hashing all use the standard library.

## XML parsing safety

Test reports are untrusted input; they arrive from CI artifacts and downloads.
Use `xml.etree.ElementTree`, never `xml.dom.minidom` or `lxml` with external
entity resolution enabled. Do not resolve external entities or DTDs. A malicious
or malformed report must produce a diagnostic, not an exception that aborts a
batch and not a file read.

## Type hints

Annotate every public function. Use `from __future__ import annotations`.

Prefer frozen dataclasses with `slots=True` for the data model. These are
allocated once per test result and a large ingest creates hundreds of thousands
of them, so the memory layout matters.

## Error handling

Distinguish three cases and treat them differently:

1. **Bad input** — malformed XML, missing file. Report which file and why, skip
   it, continue the batch. Never let one bad artifact lose a whole ingest.
2. **Bad usage** — impossible flag combination, no database. Fail immediately
   with a message that says what to do instead.
3. **Bugs** — let them raise. Do not swallow exceptions to keep a command alive;
   a silent wrong answer is worse than a traceback.

Never use a bare `except:`. Catch specific exceptions. If catching broadly is
genuinely required at a boundary, catch `Exception`, log it with context, and say
in a comment why the boundary exists.

## SQL

Parameterized queries only. Never f-string a value into SQL, including values
that "obviously" came from our own code — test ids come from user-supplied XML
and are attacker-controlled in the general case.

Keep schema DDL in one place in `storage.py`. Bump `SCHEMA_VERSION` on change.

## Determinism

Analysis must be deterministic: same inputs, same output, including ordering.
Sort by an explicit tiebreaker (usually `test_id`) after sorting by score, since
equal scores would otherwise order by dict insertion and make output diff noisily
between runs.

Never call `random` in analysis. The `hunt` runner uses seeds, and every seed it
uses is recorded so a hunt can be replayed.

## Numbers in output

Scores are `[0, 1]`, displayed to two decimals. Rates displayed as percentages
with no decimals. Durations in seconds to one decimal, or milliseconds under one
second. Do not display more precision than the sample size supports.

## Testing

Tests must be fast and deterministic, with one deliberate exception: the demo
suite in `examples/` is intentionally flaky because it is the fixture the tool
detects. It is excluded from this project's own test run and gated behind an env
var so CI stays green.

Every parser dialect needs a real fixture file. Hand-written XML that happens to
parse proves nothing about Maven's actual output.

Test the analysis functions directly with constructed data. They are pure by
design specifically so they can be tested without a database or a filesystem.

Example tests pin decisions; property tests pin relationships. Both are required
and neither replaces the other. When adding a property in
`tests/test_properties.py`:

- State it so it is **true**, not so it passes. `analyze_test` requires
  chronological order, so "shuffling the outcomes changes nothing" is not an
  invariant of this codebase and asserting it would only force a sort that hides
  the real requirement.
- If a property needs a precondition, put it in the generator and say why in the
  generator's docstring. A precondition discovered later is a bug found; a
  precondition added quietly to make a test green is a bug hidden.
- Assert the generator's own reach. A property that never reaches the interesting
  state passes while proving nothing, which is the specific way this kind of test
  rots. `TestTheGeneratorIsNotVacuous` fails if flaky, broken, regression or
  never-passed histories stop being produced.

## Style

- `ruff` defaults, 100-char lines
- Module-level docstring on every module saying what it is for
- Comments explain *why*, never *what*. If a line needs a comment explaining what
  it does, rewrite the line.
- Private helpers prefixed `_`
- No `TODO` comments in committed code; either do it or write it in the spec
