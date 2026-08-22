# 0017 — Windows is a supported platform, and the test suite could not have told us

**Status:** Accepted

## Context

CI ran on `ubuntu-latest` only, and the README said: *on Windows, run these under WSL*.

That sentence is a reasonable thing to write and a slightly dishonest thing to ship. The
tool's central claim is that it is language-agnostic and zero-setup — it reads JUnit XML,
needs no server, no account, and two dependencies. "Install a Linux subsystem first" is
setup, and it is the kind that stops a Windows .NET or Java team from ever trying it. Those
are exactly the teams whose CI produces the most JUnit XML.

There was also a specific reason to distrust a guess here. Adding `windows-latest` to the
matrix and watching it go green would have been *worse than not testing*, because it would
have produced a green tick over a real, reachable bug. That is worth explaining, because it
is the whole reason this record exists.

## The failure the test suite structurally cannot see

On Windows, CPython encodes `sys.stdout` with the locale codepage — commonly cp1252 —
whenever stdout is not an interactive console. Redirected to a file, piped to another
command: codepage. That is every CI job, and most of what this project's own README
tells people to do, since it pipes and redirects throughout.

Two outputs contain characters cp1252 cannot represent:

- `report/verification.py` draws the before/after failure-rate bars with `U+2588` and
  `U+2591`. Its docstring said "plain block characters so it survives a log file", which
  was precisely wrong: a log file is the case that breaks.
- `report/issue.py` puts `U+1F534` / `U+1F7E0` in the Slack payload title, and the
  command's own help text recommends `flaky issue -f slack | curl -d @-`.

So `flaky verify > verify.log` would raise `UnicodeEncodeError` on Windows while the same
command worked interactively.

**And no test would catch it.** pytest replaces `sys.stdout` with a UTF-8 buffer, and
typer's `CliRunner` does the same. The encoding never becomes cp1252 inside a test, so the
suite is blind to this by construction. A green `windows-latest` run would have said
nothing at all about the bug it was hiding.

That is the same shape as the fixture problem in
[ADR-0014](0014-search-a-window-for-the-polluter.md) and the vacuity problem in
[ADR-0016](0016-assert-relationships-not-only-examples.md): a check that cannot fail is
not evidence. Three times now, from three unrelated directions.

## Decision

Support Windows natively. Add `windows-latest` and `macos-latest` to the test matrix, fix
what an audit of the actual code found, and — because the matrix alone is insufficient —
add a CI step that exercises the condition the suite cannot.

### Reconstruct the platform, do not wait for the runner

`tests/test_portability.py` builds each platform condition explicitly: a cp1252
`TextIOWrapper` standing in for a redirected Windows console, a patched `os.name`, a
genuinely aborted merge. Every one runs on every platform, so a regression fails on the
developer's laptop rather than on a runner three pushes later.

The first test in the file is the control: it asserts cp1252 **cannot** encode our output.
Without that, the rest of the class would keep passing if the characters were ever removed,
and would be proving nothing.

### And still run the real thing

CI runs the installed console script with output redirected to files and
`PYTHONIOENCODING=cp1252`, then checks the files are valid UTF-8 JSON with LF endings. On
all three platforms, not just Windows — cp1252 exists everywhere, so the Windows condition
is reproducible on Linux, which makes it debuggable without a Windows machine.

## What the audit found

Four real defects and three cosmetic ones. None was hypothetical; each is a specific line.

**1. Locale-codepage stdout (would fail).** Four `stdout.file.write()` calls bypass rich
entirely, and `_emit` is the sink for eight commands. Fixed with a single
`reconfigure(encoding="utf-8", newline="\n")` in the typer callback, which also pins LF so a
report written on Windows byte-matches the same report from Linux.

**2. `DETACH` inside an open transaction (would fail, Windows-only symptom).**
`merge_from` ATTACHes the source and DETACHes in a `finally`. On the error path an implicit
transaction is still open, SQLite refuses to detach inside one, so the `finally` raised on
top of the original exception and left the source attached. On Linux the temp directory is
deleted anyway; on Windows an open handle makes the file undeletable and the real error is
lost. Fixed with `rollback()` before the detach.

This one has a test that was checked *against the unfixed code*: with the rollback removed,
`PRAGMA database_list` reports `['main', 'src']` and the test fails. A regression test that
has never been seen failing is a guess.

**3. Case-insensitive self-merge guard (would fail, macOS).** The guard was
`source.resolve() == self.path.resolve()`. macOS `resolve()` follows symlinks without
folding case, so `flaky merge A.DB` into `a.db` walked straight past it and attached a
database as its own source. Now `Path.samefile`, which asks the filesystem for device and
inode. The pre-existing property test could not catch this: it passes the identical string.

**4. POSIX quoting in the printed reproducer command (cosmetic, but it is the product).**
`reproduce._quote` wrapped spaces in single quotes. cmd.exe treats those as literal
characters, so a Windows user pasting the command from
[ADR-0015](0015-reproduce-by-experiment-not-correlation.md) would get an error about a file
named `'C:\My`. Now platform-aware.

**Leaked temp directories (cosmetic, all platforms).** `runner.py` and `reproduce.py` each
`mkdtemp` and never clean up — one directory per hunt, one per reproduce. Now registered
with `atexit`, and only when the directory was ours: a caller-supplied `workdir` belongs to
the caller.

**No `.gitattributes` (latent).** GitHub's Windows image sets `core.autocrlf=true`, which
would rewrite the JUnit fixtures on checkout. Tracing it, no current assertion breaks — the
parser reads bytes and normalization collapses whitespace — but the steering rule is that
every dialect needs a *real* fixture, and a file git has reformatted is no longer the
runner's real output. `*.xml -text` pins them.

**Windows path traversal in the dashboard (no defect, thin margin).** `_serve_static` joins
a URL path onto `STATIC_ROOT`. On Windows `\` is a separator, and pathlib's `/` **discards
the left operand** when the right side is drive-absolute, so `STATIC_ROOT / "C:/Windows/win.ini"`
is just that Windows path. The containment check catches it and there is no traversal — but
that check was the only thing standing between a crafted request and an arbitrary file read,
and the existing test only covered POSIX `../` forms. Four Windows-shaped cases added.

**`UV_CACHE_DIR: /tmp/.uv-cache` (would misbehave).** Workflow-level, so a Windows job
inherited it and would create `C:\tmp` at the drive root. The obvious replacement,
`${{ runner.temp }}`, is unavailable in workflow-level `env` — the `runner` context only
exists at step level — and `github.workspace` would put the cache where the bundle-diff
check looks. Removed: `setup-uv` already picks a correct per-platform location.

## The bug this work introduced, and what it demonstrates

The first version of the encoding fix called `_force_utf8_streams()` at module import.
Importing `cli` happens while pytest holds `sys.stdout`, so reconfiguring detached the
buffer pytest was writing to. **The entire 937-test suite produced no output whatsoever and
exited 1** — no failure list, no summary, nothing to read.

Two changes followed. It now runs from the command callback rather than at import, and it
does nothing when the stream is already UTF-8, which is every POSIX system. Both are pinned
by tests, including one asserting that an already-UTF-8 stream is *not* touched.

Worth recording because of what it is an instance of: a change that is correct on the
platform it targets and destructive on the platform it runs through. Which is the same
category as the bug it was written to fix.

## Consequences

**The matrix is 4 + 2, not 4 × 3.** Full Python range on Linux; one version each on Windows
and macOS. Platform bugs are platform bugs, not version-specific ones, and twelve jobs for
that would spend runner minutes to learn nothing.

**Only the `test` job is cross-platform.** `dashboard`, `dogfood` and `action` keep to
`ubuntu-latest`: they depend on `jq`, `seq`, `trap`, heredocs, POSIX paths and
`sh reproduce-command.sh`. Rewriting them three ways would add surface area without adding
a claim, and the claims they check are platform-independent.

**`flaky reproduce` stays pytest-only on Windows too.** The constraint in ADR-0015 is about
what a runner accepts, not about the operating system.

**The README no longer mentions WSL** and says what "tested" covers, including the part the
suite cannot see. A support claim that quietly excludes the redirected case is the kind of
claim this project is otherwise careful not to make.

## Rejected alternatives

**Add the matrix, fix nothing, see what breaks.** It would have gone green and certified
the encoding bug. This is the specific trap the ADR exists to record.

**Set `PYTHONUTF8=1` or `PYTHONIOENCODING` in the CI env and call it fixed.** Fixes CI and
nobody's actual machine. The tool has to work when a user redirects its output, not only
where we control the environment.

**Strip the non-ASCII characters instead.** Considered seriously, since it removes the
problem rather than working around it. Rejected because the bars and the emoji carry real
scanning value, and the underlying defect is broader than those two characters: any future
non-ASCII in any report would reintroduce it. Fixing the stream fixes the class.

**Guard every possible stream shape in `_force_utf8_streams` rather than gating on
encoding.** Tried first, and it is what broke the suite. Narrowing to the case that needs
changing is a smaller blast radius than defending against every case that does not.
