# Parser fixtures

Provenance matters here. A hand-written XML file that happens to parse proves
nothing about what a real runner emits, so each fixture below is labelled with
how it was obtained.

| File | Provenance |
|---|---|
| `pytest.xml` | **Captured.** Real output of `pytest --junitxml` (pytest 9.1.1, default `junit_family=xunit2`). |
| `jest.xml` | **Captured.** Real output of `jest --reporters=jest-junit` (jest 29.7.0, jest-junit 16.0.0). |
| `go.xml` | **Reference.** Written to the `go-junit-report` v2 output shape. Not captured; no Go toolchain was available on the build machine. |
| `surefire.xml` | **Reference.** Written to the Maven Surefire output shape, including `<flakyFailure>`. Not captured; no JVM was available. |
| `gradle-nested.xml` | **Reference.** Nested `<testsuite>` inside `<testsuites>`, which Gradle and some Surefire aggregators produce. |
| `trx.xml` | **Reference.** Written to the `trx2junit` output shape. Not captured; no .NET SDK was available. |
| `truncated.xml` | **Synthetic.** A report cut off mid-element, which is what a killed CI job leaves behind. |
| `entity.xml` | **Synthetic.** Carries a DOCTYPE/ENTITY declaration. Asserts the parser refuses it. |
| `empty.xml` | **Synthetic.** Zero-length file. |
| `no-testcases.xml` | **Synthetic.** Valid XML, well-formed suite, no test cases. |

## Why the captured/reference distinction is called out

The two dialects that actually shaped the parser are the two captured ones,
because they contained surprises that no format description mentions:

- pytest's default `xunit2` family **omits the `file` and `line` attributes**
  entirely and encodes location only in a dotted `classname`. Test ids therefore
  have to be reconstructed from `classname` to be usable with `--deselect`.
- jest-junit writes the **identical string** into both `classname` and `name`, so
  naively joining them doubles the describe path.

The reference fixtures are structurally faithful and exercise the parser paths
they are meant to (root-level `testsuite`, nested suites, retry elements,
package-path classnames), but they have not been validated against real runner
output. Treat go, Surefire, and .NET support as untested against live runners
until someone with those toolchains confirms it.

## Regenerating the captured fixtures

```sh
# pytest
python -m pytest <suite> --junitxml=tests/fixtures/pytest.xml

# jest
npx jest --reporters=default --reporters=jest-junit   # writes per jest-junit config
```
