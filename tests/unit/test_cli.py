#
# tests/unit/test_cli.py
#
"""
Comprehensive tests for CLI functionality.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from supsrc.cli.main import cli


class TestMainCLI:
    """Test main CLI entry point."""

    def test_cli_help(self) -> None:
        """Test CLI help output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "supsrc" in result.output.lower()
        assert "watch" in result.output
        assert "tail" in result.output
        assert "config" in result.output

    def test_cli_version(self) -> None:
        """Test CLI version output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])

        assert result.exit_code == 0
        # Check for version from pyproject.toml
        assert "0.2.0" in result.output

    def test_global_log_level_option(self) -> None:
        """Test global log level option."""
        runner = CliRunner()

        # Test valid log level
        result = runner.invoke(cli, ["--log-level", "DEBUG", "config", "show", "--help"])
        assert result.exit_code == 0

        # Test invalid log level
        result = runner.invoke(cli, ["--log-level", "INVALID", "config", "show", "--help"])
        assert result.exit_code != 0

    def test_global_log_file_option(self) -> None:
        """Test global log file option."""
        runner = CliRunner()

        with tempfile.NamedTemporaryFile() as tmp_file:
            result = runner.invoke(cli, ["--log-file", tmp_file.name, "config", "show", "--help"])
            assert result.exit_code == 0

    def test_global_json_logs_option(self) -> None:
        """Test global JSON logs option."""
        runner = CliRunner()

        # Invoke a real command to ensure the context is processed
        result = runner.invoke(cli, ["--json-logs", "config", "show", "--help"])
        assert result.exit_code == 0


class TestConfigCommands:
    """Test configuration-related CLI commands."""

    def test_config_show_help(self) -> None:
        """Test config show command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show", "--help"])

        assert result.exit_code == 0
        assert "load, validate, and display the configuration" in result.output.lower()

    def test_config_show_valid_file(self, tmp_path: Path) -> None:
        """Test config show with valid configuration file."""
        config_content = """
        [global]
        log_level = "INFO"

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

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show", "--config-path", str(config_file)])

        assert result.exit_code == 0
        assert "test-repo" in result.output

    def test_config_show_nonexistent_file(self) -> None:
        """Test config show with non-existent file."""
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show", "--config-path", "/nonexistent/config.conf"])

        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_config_show_invalid_toml(self, tmp_path: Path) -> None:
        """Test config show with invalid TOML."""
        config_file = tmp_path / "invalid.conf"
        config_file.write_text('[invalid toml "missing quote')

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show", "--config-path", str(config_file)])

        assert result.exit_code != 0
        assert "error" in result.output.lower()
        assert "toml" in result.output.lower()

    def test_config_show_with_env_var(self, tmp_path: Path) -> None:
        """Test config show with environment variable."""
        config_content = """
        [repositories.env-test]
        path = "/tmp/env-test"
        enabled = true

        [repositories.env-test.rule]
        type = "supsrc.rules.manual"

        [repositories.env-test.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "env_test.conf"
        config_file.write_text(config_content)

        runner = CliRunner()

        with patch.dict("os.environ", {"SUPSRC_CONF": str(config_file)}):
            result = runner.invoke(cli, ["config", "show"])

        assert result.exit_code == 0
        assert "env-test" in result.output


class TestCLIIntegration:
    """Test CLI integration scenarios."""

    def test_end_to_end_config_validation(self, tmp_path: Path) -> None:
        """Test end-to-end configuration validation."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        try:
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("Git not available for integration test")

        config_content = f"""
        [global]
        log_level = "DEBUG"
        [repositories.integration-test]
        path = "{repo_path}"
        enabled = true
        [repositories.integration-test.rule]
        type = "supsrc.rules.inactivity"
        period = "30s"
        [repositories.integration-test.repository]
        type = "supsrc.engines.git"
        auto_push = false
        """
        config_file = tmp_path / "integration.conf"
        config_file.write_text(config_content)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["config", "show", "--config-path", str(config_file)],
        )

        assert result.exit_code == 0
        assert "integration-test" in result.output

    def test_cli_error_handling(self, tmp_path: Path) -> None:
        """Test CLI error handling scenarios."""
        runner = CliRunner()

        result = runner.invoke(cli, ["invalid-command"])
        assert result.exit_code != 0

        result = runner.invoke(cli, ["--invalid-option"])
        assert result.exit_code != 0

        result = runner.invoke(cli, ["config", "show", "--config-path", "/invalid/path/config.conf"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_cli_logging_integration(self, tmp_path: Path) -> None:
        """Test CLI logging integration."""
        config_content = """
        [repositories.log-test]
        path = "/tmp/log-test"
        enabled = true
        [repositories.log-test.rule]
        type = "supsrc.rules.manual"
        [repositories.log-test.repository]
        type = "supsrc.engines.git"
        """
        config_file = tmp_path / "log_test.conf"
        config_file.write_text(config_content)

        log_file = tmp_path / "test.log"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--log-level", "DEBUG",
                "--log-file", str(log_file),
                "--json-logs",
                "config", "show",
                "--config-path", str(config_file),
            ],
        )

        assert result.exit_code == 0
        assert log_file.exists()
        log_content = log_file.read_text()
        assert "{" in log_content  # Basic JSON check


class TestCLIUtilities:
    """Test CLI utility functions and helpers."""

    def test_command_parsing(self) -> None:
        """Test that Click properly parses our commands."""
        assert "config" in cli.commands
        assert "watch" in cli.commands
        assert "tail" in cli.commands

        config_cmd = cli.commands["config"]
        assert "show" in config_cmd.commands

    def test_environment_variable_integration(self) -> None:
        """Test environment variable integration."""
        runner = CliRunner()
        with patch.dict("os.environ", {"SUPSRC_LOG_LEVEL": "WARNING"}):
            # Must invoke a real command for context to be processed
            result = runner.invoke(cli, ["--help"])
            assert result.exit_code == 0

    def test_context_passing(self) -> None:
        """Test Click context passing between commands."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--log-level", "DEBUG", "config", "show", "--help"])
        assert result.exit_code == 0

# 🧪🖥️
