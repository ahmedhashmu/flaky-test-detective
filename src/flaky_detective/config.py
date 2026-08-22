"""Configuration: defaults, `.flaky.toml` discovery, and precedence.

Precedence is flags > config file > defaults. Nothing here reads the environment;
CI and git detection live in `environment.py`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

CONFIG_FILENAME = ".flaky.toml"

DEFAULT_FLAKE_THRESHOLD = 0.15
"""Above this score a test is called flaky.

Deliberately low. One same-commit divergence in ten runs is worth surfacing, and
the report shows the counts so a user can judge for themselves.
"""

DEFAULT_QUARANTINE_THRESHOLD = 0.4
"""Above this score quarantine is recommended. Higher than the flake threshold:
naming a flake is cheap, removing it from the suite should not be.
"""

DEFAULT_CONFIDENCE_RUNS = 10
DEFAULT_ORDER_WINDOW = 6
"""How many preceding tests are searched for a polluter.

Mirrors `analysis.ordering.DEFAULT_WINDOW`, and is exposed here because it is a real
trade a project might want to make: wider finds polluters further back, and costs
precision through the multiplicity correction. Swept in `flaky benchmark --sweep
window`, so the default is a measurement rather than a preference.
"""
DEFAULT_FIXED_RUN_STREAK = 10
DEFAULT_HUNT_ITERATIONS = 10


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved settings for one invocation."""

    db_path: Path = Path(".flaky.db")
    quarantine_path: Path = Path(".flaky-quarantine.json")
    flake_threshold: float = DEFAULT_FLAKE_THRESHOLD
    quarantine_threshold: float = DEFAULT_QUARANTINE_THRESHOLD
    confidence_runs: int = DEFAULT_CONFIDENCE_RUNS
    order_window: int = DEFAULT_ORDER_WINDOW
    fixed_run_streak: int = DEFAULT_FIXED_RUN_STREAK
    hunt_iterations: int = DEFAULT_HUNT_ITERATIONS
    quarantine_days: int = 14
    ignore: tuple[str, ...] = ()
    source: Path | None = None

    def with_overrides(self, **overrides: object) -> Config:
        """Apply non-None overrides. None means "flag not supplied"."""
        supplied = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **supplied)  # type: ignore[arg-type]


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for `.flaky.toml`.

    Walking up means the tool works from any subdirectory of a repo, which is
    where people actually run commands.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None, start: Path | None = None) -> Config:
    """Load configuration, falling back to defaults when no file exists.

    A missing file is normal and silent. A malformed file is a usage error and
    raises, because silently ignoring a config the user wrote is worse than
    stopping.
    """
    config_path = path or find_config_file(start)
    if config_path is None:
        return Config()

    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read config {config_path}: {exc}") from exc

    section = data.get("flaky", data)
    if not isinstance(section, dict):
        raise ValueError(f"{config_path}: expected a [flaky] table")

    base = config_path.parent
    return Config(
        db_path=_resolve(base, section.get("db"), Path(".flaky.db")),
        quarantine_path=_resolve(base, section.get("quarantine"), Path(".flaky-quarantine.json")),
        flake_threshold=_number(section, "flake_threshold", DEFAULT_FLAKE_THRESHOLD),
        quarantine_threshold=_number(section, "quarantine_threshold", DEFAULT_QUARANTINE_THRESHOLD),
        confidence_runs=int(_number(section, "confidence_runs", DEFAULT_CONFIDENCE_RUNS)),
        order_window=int(_number(section, "order_window", DEFAULT_ORDER_WINDOW)),
        fixed_run_streak=int(_number(section, "fixed_run_streak", DEFAULT_FIXED_RUN_STREAK)),
        hunt_iterations=int(_number(section, "hunt_iterations", DEFAULT_HUNT_ITERATIONS)),
        quarantine_days=int(_number(section, "quarantine_days", 14)),
        ignore=tuple(str(x) for x in section.get("ignore", ()) or ()),
        source=config_path,
    )


EXAMPLE_CONFIG = """\
# Flaky Test Detective configuration.
# Every value here can be overridden by a command-line flag.

[flaky]
# Where run history is stored. Commit this file to share history across a team,
# or cache it in CI. It is a plain SQLite database.
#
# Relative paths resolve against this file's directory. On Windows, use forward
# slashes -- "C:/builds/history.db" -- or a single-quoted TOML literal string.
# A double-quoted TOML string reads backslash as an escape, so a pasted Windows
# path fails to parse as soon as a directory name begins with U, x, or n.
db = ".flaky.db"

# Where quarantine decisions are recorded.
quarantine = ".flaky-quarantine.json"

# Score above which a test is reported as flaky. Scores run 0 to 1.
flake_threshold = 0.15

# Score above which quarantine is recommended. Kept higher than the flake
# threshold: naming a flake is cheap, removing it from the suite is not.
quarantine_threshold = 0.4

# Runs needed before a score is considered fully confident. Below this, scores
# are damped so a test seen 3 times cannot outrank one seen 200 times on the
# same evidence.
confidence_runs = 10

# How many preceding tests to search when looking for a polluter. Wider finds
# polluters further back; because every extra candidate tightens the significance
# threshold all of them must clear, it also costs precision. Measured, not guessed:
# see `flaky benchmark --sweep window`.
order_window = 6

# Consecutive passes after which a previously flaky test is reported as fixed.
fixed_run_streak = 10

# Default iteration count for `flaky hunt`.
hunt_iterations = 10

# Days a quarantine entry lasts before it must be re-verified.
quarantine_days = 14

# Test id substrings to exclude from analysis entirely.
ignore = []
"""


def _resolve(base: Path, value: object, default: Path) -> Path:
    """Resolve a configured path relative to the config file, not to cwd.

    Relative to cwd would mean the database moves when you change directory.
    """
    if value is None:
        return (base / default).resolve()
    candidate = Path(str(value)).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def _number(section: dict[str, object], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Config key '{key}' must be a number, got {value!r}")
    return float(value)
