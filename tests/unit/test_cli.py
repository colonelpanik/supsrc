#
# tests/unit/test_cli.py
#
"""
Comprehensive tests for CLI functionality.
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from supsrc.cli.config_cmds import config_cli
from supsrc.cli.main import cli
from supsrc.cli.watch_cmds import watch_cli


class TestMainCLI:
    """Test main CLI entry point."""

    def test_cli_help(self) -> None:
        """Test CLI help output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "Supsrc: Automated Git commit/push utility" in result.output
        assert "watch" in result.output
        assert "config" in result.output

    def test_cli_version(self) -> None:
        """Test CLI version output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])

        assert result.exit_code == 0
        assert "supsrc" in result.output.lower()

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
            result = runner.invoke(cli, [
                "--log-file", tmp_file.name,
                "config", "show", "--help"
            ])
            assert result.exit_code == 0

    def test_global_json_logs_option(self) -> None:
        """Test global JSON logs option."""
        runner = CliRunner()

        result = runner.invoke(cli, [
            "--json-logs",
            "config", "show", "--help"
        ])
        assert result.exit_code == 0


class TestConfigCommands:
    """Test configuration-related CLI commands."""

    def test_config_show_help(self) -> None:
        """Test config show command help."""
        runner = CliRunner()
        result = runner.invoke(config_cli, ["show", "--help"])

        assert result.exit_code == 0
        assert "Load, validate, and display the configuration" in result.output

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
        result = runner.invoke(config_cli, ["show", "--config-path", str(config_file)])

        # Should exit with success even if paths don't exist (validation warnings)
        assert result.exit_code == 0
        # Output should contain configuration information
        assert "test-repo" in result.output or "Configuration loaded" in result.output

    def test_config_show_nonexistent_file(self) -> None:
        """Test config show with non-existent file."""
        runner = CliRunner()
        result = runner.invoke(config_cli, [
            "show", "--config-path", "/nonexistent/config.conf"
        ])

        assert result.exit_code == 1
        assert "Error" in result.output
        assert "Configuration problem" in result.output

    def test_config_show_invalid_toml(self, tmp_path: Path) -> None:
        """Test config show with invalid TOML."""
        config_file = tmp_path / "invalid.conf"
        config_file.write_text('[invalid toml "missing quote')

        runner = CliRunner()
        result = runner.invoke(config_cli, ["show", "--config-path", str(config_file)])

        assert result.exit_code == 1
        assert "Error" in result.output

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

        # Test with SUPSRC_CONF environment variable
        with patch.dict("os.environ", {"SUPSRC_CONF": str(config_file)}):
            result = runner.invoke(config_cli, ["show"])

        assert result.exit_code == 0


class TestWatchCommands:
    """Test watch-related CLI commands."""

    def test_watch_help(self) -> None:
        """Test watch command help."""
        runner = CliRunner()
        result = runner.invoke(watch_cli, ["--help"])

        assert result.exit_code == 0
        assert "Monitor configured repositories" in result.output
        assert "--tui" in result.output

    @patch("supsrc.cli.watch_cmds.WatchOrchestrator")
    @patch("supsrc.cli.watch_cmds.load_config")
    def test_watch_normal_mode(
        self,
        mock_load_config: Mock,
        mock_orchestrator_class: Mock,
        tmp_path: Path
    ) -> None:
        """Test watch command in normal mode."""
        # Mock configuration
        mock_config = Mock()
        mock_load_config.return_value = mock_config

        # Mock orchestrator
        mock_orchestrator = Mock()
        mock_orchestrator.run = Mock()
        mock_orchestrator_class.return_value = mock_orchestrator

        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        # Use a timeout to prevent hanging
        with patch("asyncio.get_event_loop_policy") as mock_policy:
            mock_loop = Mock()
            mock_policy.return_value.get_event_loop.return_value = mock_loop
            mock_loop.is_closed.return_value = False
            mock_loop.run_until_complete.return_value = None

            result = runner.invoke(watch_cli, [
                "--config-path", str(config_file)
            ])

        # Should attempt to load config and create orchestrator
        mock_load_config.assert_called_once()
        mock_orchestrator_class.assert_called_once()

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_tui_mode(self, mock_tui_app: Mock, tmp_path: Path) -> None:
        """Test watch command in TUI mode."""
        mock_app_instance = Mock()
        mock_app_instance.run = Mock()
        mock_tui_app.return_value = mock_app_instance

        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()
        result = runner.invoke(watch_cli, [
            "--config-path", str(config_file),
            "--tui"
        ])

        # Should create and run TUI app
        mock_tui_app.assert_called_once()
        mock_app_instance.run.assert_called_once()

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", False)
    def test_watch_tui_unavailable(self, tmp_path: Path) -> None:
        """Test watch command when TUI is unavailable."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()
        result = runner.invoke(watch_cli, [
            "--config-path", str(config_file),
            "--tui"
        ])

        assert result.exit_code == 1
        assert "TUI mode requires" in result.output

    def test_watch_config_file_not_found(self) -> None:
        """Test watch command with non-existent config file."""
        runner = CliRunner()
        result = runner.invoke(watch_cli, [
            "--config-path", "/nonexistent/config.conf"
        ])

        assert result.exit_code != 0


class TestCLIIntegration:
    """Test CLI integration scenarios."""

    def test_end_to_end_config_validation(self, tmp_path: Path) -> None:
        """Test end-to-end configuration validation."""
        # Create a valid Git repository
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        try:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

            (repo_path / "README.md").write_text("Test repo")
            subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo_path, check=True)

        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip("Git not available for integration test")

        # Create valid configuration
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

        # Test config show
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--log-level", "DEBUG",
            "config", "show",
            "--config-path", str(config_file)
        ])

        assert result.exit_code == 0
        assert "integration-test" in result.output

    def test_cli_error_handling(self, tmp_path: Path) -> None:
        """Test CLI error handling scenarios."""
        runner = CliRunner()

        # Test with invalid command
        result = runner.invoke(cli, ["invalid-command"])
        assert result.exit_code != 0

        # Test with invalid option
        result = runner.invoke(cli, ["--invalid-option"])
        assert result.exit_code != 0

        # Test config show with invalid path
        result = runner.invoke(cli, [
            "config", "show",
            "--config-path", "/invalid/path/config.conf"
        ])
        assert result.exit_code == 1
        assert "Error" in result.output

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
        result = runner.invoke(cli, [
            "--log-level", "DEBUG",
            "--log-file", str(log_file),
            "--json-logs",
            "config", "show",
            "--config-path", str(config_file)
        ])

        # Should complete without error
        assert result.exit_code == 0

        # Log file should be created
        assert log_file.exists()

        # Log file should contain JSON logs
        log_content = log_file.read_text()
        assert "{" in log_content  # Basic JSON check


class TestCLIUtilities:
    """Test CLI utility functions and helpers."""

    def test_command_parsing(self) -> None:
        """Test command line argument parsing."""
        # Test that Click properly parses our commands
        from supsrc.cli.main import cli

        # Test that the main CLI has expected commands
        assert "config" in cli.commands
        assert "watch" in cli.commands

        # Test that commands have expected options
        config_cmd = cli.commands["config"]
        assert any("show" in str(cmd) for cmd in config_cmd.commands.keys())

    def test_environment_variable_integration(self) -> None:
        """Test environment variable integration."""
        runner = CliRunner()

        # Test SUPSRC_LOG_LEVEL environment variable
        with patch.dict("os.environ", {"SUPSRC_LOG_LEVEL": "WARNING"}):
            result = runner.invoke(cli, ["--help"])
            assert result.exit_code == 0

    def test_context_passing(self) -> None:
        """Test Click context passing between commands."""
        runner = CliRunner()

        # Test that options are properly passed to subcommands
        result = runner.invoke(cli, [
            "--log-level", "DEBUG",
            "config", "show", "--help"
        ])

        assert result.exit_code == 0

# 🧪🖥️
