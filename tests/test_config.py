"""Configuration discovery and precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from flaky_detective.config import (
    DEFAULT_FLAKE_THRESHOLD,
    EXAMPLE_CONFIG,
    Config,
    find_config_file,
    load_config,
)


class TestDefaults:
    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        """Running with no config is the normal first experience."""
        settings = load_config(start=tmp_path)
        assert settings.flake_threshold == DEFAULT_FLAKE_THRESHOLD
        assert settings.source is None

    def test_quarantine_bar_is_higher_than_the_flake_bar(self) -> None:
        """Naming a flake is cheap; removing coverage is not."""
        settings = Config()
        assert settings.quarantine_threshold > settings.flake_threshold


class TestDiscovery:
    def test_finds_a_config_in_the_current_directory(self, tmp_path: Path) -> None:
        (tmp_path / ".flaky.toml").write_text("[flaky]\n")
        assert find_config_file(tmp_path) == tmp_path / ".flaky.toml"

    def test_walks_upwards(self, tmp_path: Path) -> None:
        """Commands get run from subdirectories, not just the repo root."""
        (tmp_path / ".flaky.toml").write_text("[flaky]\n")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_config_file(nested) == tmp_path / ".flaky.toml"

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert find_config_file(tmp_path) is None


class TestLoading:
    def test_reads_values(self, tmp_path: Path) -> None:
        (tmp_path / ".flaky.toml").write_text(
            "[flaky]\n"
            "flake_threshold = 0.42\n"
            "quarantine_threshold = 0.8\n"
            "confidence_runs = 25\n"
            "fixed_run_streak = 4\n"
            "hunt_iterations = 30\n"
            "quarantine_days = 7\n"
            'ignore = ["slow_", "wip_"]\n'
        )
        settings = load_config(start=tmp_path)
        assert settings.flake_threshold == 0.42
        assert settings.quarantine_threshold == 0.8
        assert settings.confidence_runs == 25
        assert settings.fixed_run_streak == 4
        assert settings.hunt_iterations == 30
        assert settings.quarantine_days == 7
        assert settings.ignore == ("slow_", "wip_")

    def test_paths_resolve_relative_to_the_config_file(self, tmp_path: Path) -> None:
        """Relative to cwd would move the database when you change directory."""
        (tmp_path / ".flaky.toml").write_text('[flaky]\ndb = "history/runs.db"\n')
        settings = load_config(start=tmp_path)
        assert settings.db_path == (tmp_path / "history" / "runs.db").resolve()

    def test_absolute_paths_are_left_alone(self, tmp_path: Path) -> None:
        # as_posix(), because a TOML basic string treats backslash as an escape: a raw
        # Windows path like C:\Users\... makes `\U` a unicode escape and the file fails to
        # parse. Forward slashes are valid on Windows and are what a user should write, so
        # EXAMPLE_CONFIG says so.
        (tmp_path / ".flaky.toml").write_text(
            f'[flaky]\ndb = "{tmp_path.as_posix()}/abs.db"\n', encoding="utf-8"
        )
        assert load_config(start=tmp_path).db_path == tmp_path / "abs.db"

    def test_accepts_a_bare_table(self, tmp_path: Path) -> None:
        (tmp_path / ".flaky.toml").write_text("flake_threshold = 0.3\n")
        assert load_config(start=tmp_path).flake_threshold == 0.3

    def test_malformed_toml_is_an_error(self, tmp_path: Path) -> None:
        """Silently ignoring a config the user wrote is worse than stopping."""
        path = tmp_path / ".flaky.toml"
        path.write_text("[flaky\nbroken")
        with pytest.raises(ValueError, match="Could not read config"):
            load_config(path)

    def test_wrong_value_type_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / ".flaky.toml"
        path.write_text('[flaky]\nflake_threshold = "high"\n')
        with pytest.raises(ValueError, match="must be a number"):
            load_config(path)

    def test_booleans_are_not_numbers(self, tmp_path: Path) -> None:
        path = tmp_path / ".flaky.toml"
        path.write_text("[flaky]\nflake_threshold = true\n")
        with pytest.raises(ValueError, match="must be a number"):
            load_config(path)


class TestOverrides:
    def test_supplied_values_win(self) -> None:
        assert Config().with_overrides(flake_threshold=0.9).flake_threshold == 0.9

    def test_none_means_not_supplied(self) -> None:
        """Every CLI flag defaults to None, so None must never overwrite."""
        base = Config(flake_threshold=0.5)
        assert base.with_overrides(flake_threshold=None).flake_threshold == 0.5

    def test_other_fields_are_untouched(self) -> None:
        base = Config(quarantine_days=30)
        assert base.with_overrides(flake_threshold=0.9).quarantine_days == 30


class TestExampleConfig:
    def test_it_parses_as_written(self, tmp_path: Path) -> None:
        """The file `flaky init` writes must be loadable by `flaky analyze`."""
        path = tmp_path / ".flaky.toml"
        path.write_text(EXAMPLE_CONFIG)
        settings = load_config(path)
        assert settings.flake_threshold == DEFAULT_FLAKE_THRESHOLD

    def test_it_documents_every_setting(self) -> None:
        for key in (
            "db",
            "quarantine",
            "flake_threshold",
            "quarantine_threshold",
            "confidence_runs",
            "fixed_run_streak",
            "hunt_iterations",
            "quarantine_days",
            "ignore",
        ):
            assert key in EXAMPLE_CONFIG
