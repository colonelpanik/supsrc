#
# tests/unit/test_config.py
#
"""
Comprehensive tests for configuration loading and validation.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from supsrc.config import load_config
from supsrc.config.models import (
    InactivityRuleConfig,
    ManualRuleConfig,
    SaveCountRuleConfig,
    SupsrcConfig,
)
from supsrc.exceptions import (
    ConfigFileNotFoundError,
    ConfigParsingError,
)


class TestConfigLoading:
    """Test configuration file loading functionality."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """Test loading a valid configuration file."""
        config_content = """
        [global]
        log_level = "DEBUG"

        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.inactivity"
        period = "30s"

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        auto_push = true
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert isinstance(config, SupsrcConfig)
        assert config.global_config.log_level == "DEBUG"
        assert len(config.repositories) == 1

        repo_config = config.repositories["test-repo"]
        assert repo_config.enabled is True
        assert isinstance(repo_config.rule, InactivityRuleConfig)
        assert repo_config.rule.period == timedelta(seconds=30)
        assert config.global_config.log_level == "DEBUG"
        assert len(config.repositories) == 1

        repo_config = config.repositories["test-repo"]
        assert repo_config.enabled is True
        assert isinstance(repo_config.rule, InactivityRuleConfig)
        assert repo_config.rule.period == timedelta(seconds=30)
        assert repo_config.repository["type"] == "supsrc.engines.git"
        assert repo_config.repository["auto_push"] is True

    def test_load_nonexistent_config(self) -> None:
        """Test loading a non-existent configuration file."""
        with pytest.raises(ConfigFileNotFoundError) as exc_info:
            load_config(Path("/nonexistent/config.conf"))

        assert "not found" in str(exc_info.value)

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        """Test loading invalid TOML syntax."""
        config_file = tmp_path / "invalid.conf"
        config_file.write_text('[invalid toml "missing quote')

        with pytest.raises(ConfigParsingError) as exc_info:
            load_config(config_file)

        assert "TOML" in str(exc_info.value)


class TestRuleConfiguration:
    """Test rule configuration validation."""

    def test_inactivity_rule_config(self, tmp_path: Path) -> None:
        """Test inactivity rule configuration."""
        config_content = """
        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.inactivity"
        period = "5m"

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)
        rule = config.repositories["test-repo"].rule

        assert isinstance(rule, InactivityRuleConfig)
        assert rule.period == timedelta(minutes=5)
        assert rule.type == "supsrc.rules.inactivity"

    def test_save_count_rule_config(self, tmp_path: Path) -> None:
        """Test save count rule configuration."""
        config_content = """
        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.save_count"
        count = 10

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)
        rule = config.repositories["test-repo"].rule

        assert isinstance(rule, SaveCountRuleConfig)
        assert rule.count == 10
        assert rule.type == "supsrc.rules.save_count"

    def test_manual_rule_config(self, tmp_path: Path) -> None:
        """Test manual rule configuration."""
        config_content = """
        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.manual"

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)
        rule = config.repositories["test-repo"].rule

        assert isinstance(rule, ManualRuleConfig)
        assert rule.type == "supsrc.rules.manual"


class TestGlobalConfiguration:
    """Test global configuration handling."""

    def test_default_global_config(self, tmp_path: Path) -> None:
        """Test default global configuration values."""
        config_content = """
        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.manual"

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.global_config.log_level == "INFO"
        assert config.global_config.numeric_log_level == 20

    def test_custom_global_config(self, tmp_path: Path) -> None:
        """Test custom global configuration."""
        config_content = """
        [global]
        log_level = "DEBUG"

        [repositories.test-repo]
        path = "/tmp/test"
        enabled = true

        [repositories.test-repo.rule]
        type = "supsrc.rules.manual"

        [repositories.test-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "test.conf"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.global_config.log_level == "DEBUG"
        assert config.global_config.numeric_log_level == 10


# 🧪⚙️
