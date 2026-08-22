# Parser fixtures

Provenance matters here. A hand-written XML file that happens to parse proves
nothing about what a real runner emits, so each fixture below is labelled with
how it was obtained.

| File | Provenance |
|---|---|
| `pytest.xml` | **Captured.** Real output of `pytest --junitxml` (pytest 9.1.1, default `junit_family=xunit2`). |
| `jest.xml` | **Captured.** Real output of `jest --reporters=jest-junit` (jest 29.7.0, jest-junit 16.0.0). |
| `go.xml` | **Reference.** Written to the `go-junit-report` v2 output shape. Exercises multiple suites and race-detector output, which the captured file below does not. |
| `go-gotestsum.xml` | **Captured.** Real output of `gotestsum --junitfile` (Go 1.27.0, gotestsum from `gotest.tools/gotestsum@latest`), on a suite with a pass, two failures, a skip and three subtests. |
| `surefire.xml` | **Reference.** Written to the Maven Surefire output shape, including `<flakyFailure>`. Not captured; no JVM was available. |
| `gradle-nested.xml` | **Reference.** Nested `<testsuite>` inside `<testsuites>`, which Gradle and some Surefire aggregators produce. |
| `trx.xml` | **Reference.** Written to the `trx2junit` output shape. Not captured; no .NET SDK was available. |
| `truncated.xml` | **Synthetic.** A report cut off mid-element, which is what a killed CI job leaves behind. |
| `entity.xml` | **Synthetic.** Carries a DOCTYPE/ENTITY declaration. Asserts the parser refuses it. |
| `empty.xml` | **Synthetic.** Zero-length file. |
| `no-testcases.xml` | **Synthetic.** Valid XML, well-formed suite, no test cases. |

## Why the captured/reference distinction is called out

The dialects that actually shaped the parser are the captured ones, because they
contained surprises that no format description mentions:

- pytest's default `xunit2` family **omits the `file` and `line` attributes**
  entirely and encodes location only in a dotted `classname`. Test ids therefore
  have to be reconstructed from `classname` to be usable with `--deselect`.
- jest-junit writes the **identical string** into both `classname` and `name`, so
  naively joining them doubles the describe path.
- gotestsum writes `message="Failed"` on every failure and puts the real output in
  the element text, **wrapped in banner lines**:

  ```
  === RUN   TestExpectsCleanRegistry
      basket_test.go:25: registry already contains 'session'
  --- FAIL: TestExpectsCleanRegistry (0.00s)
  ```

  The parser already distrusted the constant `message`, but then took the *last*
  non-empty line of the text — which is the `--- FAIL:` banner, carrying the test's
  own name. Every Go failure therefore had a unique signature, so signature
  clustering could never group two Go tests and `classify.py` saw no cause text at
  all. Capturing this file is what found it. The `go.xml` reference fixture did not,
  because whoever wrote it put a sensible string in the `message` attribute rather
  than reproducing Go's banners.

The reference fixtures are structurally faithful and exercise the parser paths
they are meant to (root-level `testsuite`, nested suites, retry elements,
package-path classnames), but they have not been validated against real runner
output. **Treat Surefire and .NET support as untested against live runners** until
someone with a JVM or a .NET SDK confirms it. Go was in that list until this
capture, and the defect it exposed is the reason the caveat is worth keeping for
the other two.

## Regenerating the captured fixtures

```sh
# pytest
python -m pytest <suite> --junitxml=tests/fixtures/pytest.xml

# jest
npx jest --reporters=default --reporters=jest-junit   # writes per jest-junit config

# go
go install gotest.tools/gotestsum@latest
gotestsum --junitfile=tests/fixtures/go-gotestsum.xml -- ./...
```
