"""Git and CI metadata detection.

Same-commit divergence is the tool's only source of proof, and it needs a commit
SHA on every run. Asking users to pass `--commit` on every invocation guarantees
they will forget, so detection is automatic wherever possible.

Detection never fails hard: outside a repo, or on an unrecognized CI provider,
the fields come back None and the analysis falls back to flip rate alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class Environment:
    """Where and when a run happened."""

    commit_sha: str | None = None
    branch: str | None = None
    ci_run_id: str | None = None
    provider: str | None = None

    def merged_with(
        self,
        commit_sha: str | None = None,
        branch: str | None = None,
        ci_run_id: str | None = None,
    ) -> Environment:
        """Explicit values win over anything detected."""
        return Environment(
            commit_sha=commit_sha or self.commit_sha,
            branch=branch or self.branch,
            ci_run_id=ci_run_id or self.ci_run_id,
            provider=self.provider,
        )


# Provider -> (env var for run id, env var for branch, env var for sha).
# Ordered by how commonly they appear; first match wins.
_CI_PROVIDERS: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("github", "GITHUB_RUN_ID", "GITHUB_REF_NAME", "GITHUB_SHA"),
    ("gitlab", "CI_PIPELINE_ID", "CI_COMMIT_REF_NAME", "CI_COMMIT_SHA"),
    ("circleci", "CIRCLE_WORKFLOW_ID", "CIRCLE_BRANCH", "CIRCLE_SHA1"),
    ("buildkite", "BUILDKITE_BUILD_ID", "BUILDKITE_BRANCH", "BUILDKITE_COMMIT"),
    ("jenkins", "BUILD_ID", "GIT_BRANCH", "GIT_COMMIT"),
    ("azure", "BUILD_BUILDID", "BUILD_SOURCEBRANCHNAME", "BUILD_SOURCEVERSION"),
    ("travis", "TRAVIS_BUILD_ID", "TRAVIS_BRANCH", "TRAVIS_COMMIT"),
)


def detect(cwd: Path | None = None) -> Environment:
    """Detect commit, branch, and CI run id.

    CI environment variables take precedence over git, because in a pull-request
    build git reports the merge commit while the CI variable reports the commit
    actually under test. Grouping by the merge SHA would scatter one commit's runs
    across many SHAs and destroy the divergence signal.
    """
    ci = _detect_ci()
    git = _detect_git(cwd)

    return Environment(
        commit_sha=ci.commit_sha or git.commit_sha,
        branch=ci.branch or git.branch,
        ci_run_id=ci.ci_run_id,
        provider=ci.provider,
    )


def _detect_ci() -> Environment:
    for provider, run_var, branch_var, sha_var in _CI_PROVIDERS:
        run_id = os.environ.get(run_var)
        if not run_id:
            continue

        branch = os.environ.get(branch_var) if branch_var else None
        sha = os.environ.get(sha_var) if sha_var else None

        # In a GitHub pull_request build GITHUB_SHA is the ephemeral merge commit,
        # which changes whenever the base branch moves. The head SHA is stable and
        # is what the tests actually ran against.
        if provider == "github" and os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
            sha = os.environ.get("GITHUB_HEAD_SHA") or sha
            branch = os.environ.get("GITHUB_HEAD_REF") or branch

        return Environment(
            commit_sha=_clean(sha),
            branch=_clean(branch),
            ci_run_id=_clean(run_id),
            provider=provider,
        )
    return Environment()


def _detect_git(cwd: Path | None = None) -> Environment:
    if shutil.which("git") is None:
        return Environment()

    sha = _git(["rev-parse", "HEAD"], cwd)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch == "HEAD":
        # Detached head, which is normal in CI checkouts. No usable branch name.
        branch = None
    return Environment(commit_sha=sha, branch=branch)


def _git(args: list[str], cwd: Path | None) -> str | None:
    """Run a git command, returning None on any failure.

    Absence of git metadata is an expected condition, not an error: the tool must
    work on a downloaded CI artifact with no repository present.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],  # noqa: S607 - resolved via PATH by design
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    return _clean(result.stdout)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
