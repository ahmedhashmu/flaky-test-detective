"""Build the published sample site: landing page, static report, live dashboard.

The interesting part is the dashboard. It is the *same* compiled bundle the package
ships, running against JSON files generated here instead of against `flaky serve`. So a
reviewer clicks through real analysis output with no install and no server, and what they
are looking at is the actual dashboard rather than a screenshot of one.

Two things make that possible without a second bundle:

- `window.__FTD_STATIC__` is set in the generated `index.html` before the bundle loads.
  `lib/api.ts` reads it and fetches `./api/*.json`; `main.tsx` reads it and uses a hash
  router, because a file host has no `/tests/<id>` to serve and would 404 on a refresh.
- Every test's detail payload goes into one `api/tests.json`, since a static host cannot
  route a path per test.

Deliberately not part of `src/flaky_detective/`: this publishes a demo, it is not a
feature of the tool, and nothing the package ships imports it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flaky_detective.config import Config  # noqa: E402
from flaky_detective.storage import Storage  # noqa: E402
from flaky_detective.web import STATIC_ROOT, api  # noqa: E402

API_VERSION = 1

FLAG = (
    "<script>window.__FTD_STATIC__=true;</script>"
    "\n<!-- Static sample: no server behind this page. See ../index.html for what the "
    "data is and is not. -->\n"
)


def build(db: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # 1. The landing page, which carries the "generated data" caveat.
    shutil.copy2(Path(__file__).parent / "index.html", out / "index.html")

    # 2. The dashboard, bundle and all.
    dashboard = out / "dashboard"
    if dashboard.exists():
        shutil.rmtree(dashboard)
    shutil.copytree(STATIC_ROOT, dashboard)

    index = dashboard / "index.html"
    html = index.read_text(encoding="utf-8")
    if "__FTD_STATIC__" not in html:
        # Injected before the first script so the flag exists when the bundle evaluates.
        marker = "<script"
        position = html.index(marker)
        html = html[:position] + FLAG + html[position:]
    index.write_text(html, encoding="utf-8")

    # 3. The payloads the dashboard would otherwise request from `flaky serve`.
    config = Config()
    with Storage(db) as store:
        overview = api.overview_payload(store, config)
        details = {}
        for row in overview["tests"]:
            test_id = row["test_id"]
            detail = api.test_detail_payload(store, config, test_id)
            if detail is not None:
                details[test_id] = detail

    api_dir = dashboard / "api"
    api_dir.mkdir(exist_ok=True)
    (api_dir / "overview.json").write_text(json.dumps(overview, default=str), encoding="utf-8")
    (api_dir / "tests.json").write_text(
        json.dumps({"api_version": API_VERSION, "tests": details}, default=str),
        encoding="utf-8",
    )

    # Jekyll would otherwise skip nothing here, but it also would not need to run at all.
    # The file costs nothing and removes a class of confusing 404.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    print(f"landing page   {out / 'index.html'}")
    print(f"dashboard      {index}")
    print(f"overview       {len(overview['tests'])} tests")
    print(f"details        {len(details)} payloads")
    if len(details) != len(overview["tests"]):
        raise SystemExit(
            f"{len(overview['tests']) - len(details)} tests in the overview have no detail "
            "payload; the dashboard would 404 on them"
        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build.py <demo.db> <output-dir>")
    build(Path(sys.argv[1]), Path(sys.argv[2]))
