#!/usr/bin/env python3
"""Fetch real projects with published flaky-test labels and run the detector on them.

Deliberately not part of the installed package. It clones repositories, builds
virtualenvs at old Python versions, and takes hours; none of that belongs in a tool
whose promise is that it installs in one command and needs no setup. See
`validation/README.md` for the method and its limits.

What ships instead is the scorer: `flaky validate validation/results` recomputes every
published number from the committed raw output in seconds, so the claim is checkable
without re-running any of this.

Usage:
    python validation/run.py                 # every project in projects.json
    python validation/run.py freezegun knack # just these
    python validation/run.py --iterations 8  # shorter, for a smoke test
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# Clones and virtualenvs for a dozen third-party projects, deliberately outside the repo
# so a validation run cannot pollute the working tree. Override with
# FLAKY_VALIDATION_WORK. Not a security-sensitive path: nothing secret is written here,
# and the contents are public repositories.
WORK = Path(os.environ.get("FLAKY_VALIDATION_WORK", "/tmp/flaky-validation"))  # noqa: S108
RESULTS = HERE / "results"
DATASET_REPO = "https://github.com/TestingResearchIllinois/idoft"

CLONE_TIMEOUT = 300
INSTALL_TIMEOUT = 900
# A hunt is N suite runs, so it gets a generous ceiling. Projects slower than this are
# not worth the wall clock: the sample is meant to be broad, not exhaustive.
HUNT_TIMEOUT = 3600


class Skip(Exception):
    """This project cannot be evaluated, with a reason worth recording."""


def run(
    argv: list[str], *, cwd: Path | None = None, timeout: int, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, shell=False
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def log(message: str) -> None:
    print(message, flush=True)


def fetch_dataset() -> tuple[Path, str]:
    """Clone IDoFT and return its path and the SHA we scored against.

    The SHA matters: the dataset is actively maintained, so a number that does not name
    the label set it came from cannot be reproduced later.
    """
    path = WORK / "idoft"
    if not path.exists():
        log(f"Cloning the dataset into {path}")
        WORK.mkdir(parents=True, exist_ok=True)
        result = run(
            ["git", "clone", "--depth", "1", DATASET_REPO, str(path)], timeout=CLONE_TIMEOUT
        )
        if result.returncode != 0:
            raise SystemExit(f"Could not clone the dataset: {result.stderr.strip()}")

    sha = run(["git", "rev-parse", "HEAD"], cwd=path, timeout=30).stdout.strip()
    return path, sha


def labels_for(dataset: Path, repo: str, sha: str) -> dict[str, str]:
    """Every labelled test for one project at one SHA, as node id -> category."""
    import csv

    url = f"https://github.com/{repo}"
    name_field = "Pytest Test Name (PathToFile::TestClass::TestMethod or PathToFile::TestMethod)"
    with (dataset / "py-data.csv").open(encoding="utf-8") as handle:
        return {
            row[name_field].strip(): row["Category"].strip()
            for row in csv.DictReader(handle)
            if row["Project URL"].strip() == url
            and row["SHA Detected"].strip() == sha
            and row["Category"].strip()
        }


def checkout(repo: str, sha: str, root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)

    result = run(
        ["git", "clone", "-q", f"https://github.com/{repo}", str(root)], timeout=CLONE_TIMEOUT
    )
    if result.returncode != 0:
        raise Skip(f"clone failed: {result.stderr.strip()[:200]}")

    result = run(["git", "checkout", "-q", sha], cwd=root, timeout=120)
    if result.returncode != 0:
        raise Skip(f"SHA {sha[:10]} not reachable: {result.stderr.strip()[:200]}")


def build_venv(root: Path, spec: dict[str, Any], defaults: dict[str, Any]) -> Path:
    """Install the project and a test runner into its own virtualenv.

    Ordering is load-bearing. The project's own pinned requirements go in first, then
    pytest and the shuffle plugin last, so that a repository pinning a pytest from 2016
    cannot leave us unable to collect its tests. Any project needing more than that
    carries explicit `pins` in projects.json rather than being fixed here invisibly.
    """
    python = str(spec.get("python") or defaults["python"])
    venv = root / ".flaky-venv"

    result = run(["uv", "venv", "-q", "--python", python, str(venv)], timeout=INSTALL_TIMEOUT)
    if result.returncode != 0:
        raise Skip(f"no Python {python} available: {result.stderr.strip()[:200]}")

    env = {**os.environ, "VIRTUAL_ENV": str(venv)}

    def pip(*args: str) -> subprocess.CompletedProcess[str]:
        return run(
            ["uv", "pip", "install", "-q", *args], cwd=root, timeout=INSTALL_TIMEOUT, env=env
        )

    if spec.get("requirements", True):
        for name in (
            "requirements.txt",
            "requirements-dev.txt",
            "requirements_dev.txt",
            "dev-requirements.txt",
            "requirements-test.txt",
            "test-requirements.txt",
        ):
            if (root / name).is_file():
                pip("-r", name)

    installed = pip("-e", ".")
    if installed.returncode != 0:
        installed = pip(".")
    if installed.returncode != 0:
        raise Skip(f"install failed: {installed.stderr.strip()[-300:]}")

    for extra in ("dev", "test", "tests"):
        pip("-e", f".[{extra}]")

    if pins := spec.get("pins"):
        forced = pip(*pins)
        if forced.returncode != 0:
            raise Skip(f"pins failed: {forced.stderr.strip()[-300:]}")

    runner = pip(str(spec.get("pytest") or defaults["pytest"]), defaults["shuffle_plugin"])
    if runner.returncode != 0:
        raise Skip(f"could not install pytest: {runner.stderr.strip()[-300:]}")

    return venv


def collect(venv: Path, root: Path) -> int:
    """How many tests the suite collects, and proof that it collects at all."""
    result = run(
        [str(venv / "bin" / "python"), "-m", "pytest", "-p", "no:cacheprovider", "-q", "--co"],
        cwd=root,
        timeout=600,
    )
    if result.returncode not in (0, 1):
        tail = (result.stdout + result.stderr).strip()[-400:]
        raise Skip(f"collection failed (exit {result.returncode}): {tail}")

    count = 0
    for line in result.stdout.splitlines():
        if "test" in line and "::" in line:
            count += 1
    return count


def hunt(venv: Path, root: Path, db: Path, iterations: int) -> dict[str, Any]:
    """Run the suite N times through the real `flaky hunt`."""
    if db.exists():
        db.unlink()

    flaky = REPO / ".venv" / "bin" / "flaky"
    if not flaky.exists():
        raise SystemExit(f"Build this project's own venv first: {flaky} is missing")

    started = time.time()
    result = run(
        [
            str(flaky),
            "hunt",
            "-n",
            str(iterations),
            "--seed",
            "1",
            "--db",
            str(db),
            "--",
            str(venv / "bin" / "python"),
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=root,
        timeout=HUNT_TIMEOUT,
    )
    elapsed = time.time() - started

    if not db.exists():
        tail = (result.stdout + result.stderr).strip()[-400:]
        raise Skip(f"hunt recorded nothing: {tail}")

    shuffled = "shuffl" in (result.stdout + result.stderr).lower()
    return {"seconds": round(elapsed, 1), "hunt_output_mentions_shuffle": shuffled}


def analyze(db: Path) -> dict[str, Any]:
    """The shipped analysis, via the JSON report. No private API, no re-implementation."""
    flaky = REPO / ".venv" / "bin" / "flaky"
    result = run([str(flaky), "report", "--db", str(db), "--format", "json"], timeout=600)
    if result.returncode != 0 or not result.stdout.strip():
        raise Skip(f"analysis failed: {result.stderr.strip()[-300:]}")
    return json.loads(result.stdout)


def evaluate(
    project: dict[str, Any],
    defaults: dict[str, Any],
    dataset: Path,
    dataset_sha: str,
    iterations: int,
) -> dict[str, Any]:
    repo = project["repo"]
    sha = project["sha"]
    name = repo.split("/")[-1]
    root = WORK / "projects" / name

    log(f"\n=== {repo} @ {sha[:10]}")
    labels = labels_for(dataset, repo, sha)
    if not labels:
        raise Skip("no labels in the dataset for this repo at this SHA")
    log(f"    {len(labels)} labelled tests")

    checkout(repo, sha, root)
    venv = build_venv(root, project, defaults)
    collected = collect(venv, root)
    log(f"    collects {collected} tests")

    db = WORK / "dbs" / f"{name}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    log(f"    hunting {iterations} iterations")
    hunt_info = hunt(venv, root, db, iterations)
    log(f"    hunt took {hunt_info['seconds']}s")

    report = analyze(db)
    log(f"    {report['summary']['runs']} runs, {report['summary']['results']} results")

    return {
        "repo": repo,
        "sha": sha,
        "python": str(project.get("python") or defaults["python"]),
        "pins": project.get("pins", []),
        "why": project.get("why", ""),
        "dataset_sha": dataset_sha,
        "iterations": iterations,
        "collected": collected,
        "hunt": hunt_info,
        "labels": labels,
        "report": report,
    }


def _existing_skips() -> list[dict[str, str]]:
    path = RESULTS / "skipped.json"
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("only", nargs="*", help="Project names to run. Default: all of them.")
    parser.add_argument("--iterations", type=int, default=None)
    args = parser.parse_args()

    manifest = json.loads((HERE / "projects.json").read_text(encoding="utf-8"))
    defaults = manifest["defaults"]
    iterations = args.iterations or int(defaults["iterations"])

    dataset, dataset_sha = fetch_dataset()
    log(f"Dataset at {dataset_sha[:10]}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    skipped: list[dict[str, str]] = []
    done = 0

    for project in manifest["projects"]:
        name = project["repo"].split("/")[-1]
        if args.only and name not in args.only:
            continue
        try:
            result = evaluate(project, defaults, dataset, dataset_sha, iterations)
        except Skip as exc:
            log(f"    SKIPPED: {exc}")
            skipped.append({"repo": project["repo"], "sha": project["sha"], "reason": str(exc)})
            continue
        except subprocess.TimeoutExpired:
            log("    SKIPPED: timed out")
            skipped.append({"repo": project["repo"], "sha": project["sha"], "reason": "timed out"})
            continue

        path = RESULTS / f"{name}.json"
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log(f"    wrote {path.relative_to(REPO)}")
        done += 1

    # Every dropped project is recorded. An unexplained gap between the manifest and the
    # results is indistinguishable from quietly removing the projects that scored badly.
    #
    # Merged with what is already on disk rather than overwritten, because runs are often
    # partial (`run.py webssh`) and a fresh write would erase the record of everything not
    # attempted this time -- turning the audit trail into a report on the last invocation.
    record = {entry["repo"]: entry for entry in _existing_skips()}
    record.update({entry["repo"]: entry for entry in skipped})
    for path in RESULTS.glob("*.json"):
        if path.name == "skipped.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        record.pop(payload.get("repo", ""), None)

    (RESULTS / "skipped.json").write_text(
        json.dumps([record[key] for key in sorted(record)], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    log(f"\n{done} evaluated, {len(skipped)} skipped")
    log("Score them with:  flaky validate validation/results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
