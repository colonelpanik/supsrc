#
# tests/unit/test_tail_command.py
#
"""
Tests for the new 'tail' command (formerly 'watch' in non-TUI mode).
"""

from pathlib import Path
from unittest.mock import Mock, patch

from click.testing import CliRunner

from supsrc.cli.main import cli


class TestTailCommand:
    """Test the tail command functionality."""

    def test_tail_command_exists(self) -> None:
        """Test that tail command exists in CLI."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "tail" in result.output
        assert "Follow repository changes" in result.output

    def test_tail_help(self) -> None:
        """Test tail command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["tail", "--help"])
        assert result.exit_code == 0
        assert "tail" in result.output
        assert "--config-path" in result.output
        assert "--tui" not in result.output

    @patch("supsrc.cli.tail_cmds._run_headless_orchestrator")
    @patch("supsrc.cli.tail_cmds.WatchOrchestrator")
    def test_tail_basic_operation(
        self, mock_orchestrator_class: Mock, mock_runner: Mock, tmp_path: Path
    ) -> None:
        """Test tail command basic operation."""
        mock_orchestrator_instance = mock_orchestrator_class.return_value
        mock_runner.return_value = 0  # Simulate successful run
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories.test]\npath = '/tmp/test'")
        runner = CliRunner()

        result = runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        # Asserting the result's exit code is the correct way to test for success.
        assert result.exit_code == 0
        mock_orchestrator_class.assert_called_once()
        args, kwargs = mock_orchestrator_class.call_args
        assert kwargs["config_path"] == config_file
        assert kwargs["app"] is None
        assert kwargs["console"] is None
        mock_runner.assert_called_once_with(mock_orchestrator_instance)

    def test_tail_with_invalid_config(self) -> None:
        """Test tail command with invalid config path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["tail", "--config-path", "/nonexistent/config.conf"])
        assert result.exit_code != 0
        assert "Error" in result.output or "does not exist" in result.output

    @patch("supsrc.cli.tail_cmds._run_headless_orchestrator")
    @patch("supsrc.cli.tail_cmds.WatchOrchestrator")
    def test_tail_with_env_config(
        self, mock_orchestrator_class: Mock, mock_runner: Mock, tmp_path: Path
    ) -> None:
        """Test tail command with config from environment variable."""
        mock_runner.return_value = 0
        config_file = tmp_path / "env_test.conf"
        config_file.write_text("[repositories.env-test]\npath = '/tmp/env-test'")
        runner = CliRunner()

        with patch.dict("os.environ", {"SUPSRC_CONF": str(config_file)}):
            result = runner.invoke(cli, ["tail"])

        assert result.exit_code == 0
        mock_orchestrator_class.assert_called_once()
        args, kwargs = mock_orchestrator_class.call_args
        assert kwargs["config_path"] == config_file

    @patch("supsrc.cli.tail_cmds._run_headless_orchestrator")
    @patch("supsrc.cli.utils.core_setup_logging")
    def test_tail_logging_setup(
        self, mock_setup_logging: Mock, mock_runner: Mock, tmp_path: Path
    ) -> None:
        """Test that tail command sets up logging correctly."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")
        runner = CliRunner()

        runner.invoke(cli, ["tail", "--log-level", "DEBUG", "--config-path", str(config_file)])

        mock_setup_logging.assert_called()
        call_args, call_kwargs = mock_setup_logging.call_args
        assert call_kwargs["level"] == 10  # DEBUG

    @patch("supsrc.cli.tail_cmds._run_headless_orchestrator")
    def test_tail_runner_returns_error_code(
        self, mock_runner: Mock, tmp_path: Path
    ) -> None:
        """Test that a non-zero exit code from the runner is propagated."""
        mock_runner.return_value = 130  # Simulate exit code from interrupt
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")
        runner = CliRunner()

        result = runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        mock_runner.assert_called_once()
        # The CliRunner catches the sys.exit and reports the code here. This is the robust way to test it.
        assert result.exit_code == 130

    @patch("supsrc.cli.tail_cmds._run_headless_orchestrator")
    def test_tail_runner_raises_keyboard_interrupt(
        self, mock_runner: Mock, tmp_path: Path
    ) -> None:
        """Test that tail command handles KeyboardInterrupt from the runner."""
        mock_runner.side_effect = KeyboardInterrupt()
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")
        runner = CliRunner()

        # The runner will catch the exception and store it in the result object.
        result = runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        # Click translates KeyboardInterrupt into a non-zero exit.
        # It does NOT store the exception in result.exception for this specific case.
        # Instead, it aborts execution and returns an exit code of 1.
        assert result.exit_code == 1
