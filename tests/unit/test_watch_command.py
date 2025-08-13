#
# tests/unit/test_watch_command.py
#
"""
Tests for the new 'watch' command (formerly 'tui' command).
"""

from pathlib import Path
from unittest.mock import Mock, patch

from click.testing import CliRunner

from supsrc.cli.main import cli


class TestWatchCommand:
    """Test the watch command functionality (interactive UI mode)."""

    def test_watch_command_exists(self) -> None:
        """Test that watch command exists in CLI."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "watch" in result.output
        # The help should indicate it's the interactive mode
        assert "Interactive" in result.output or "dashboard" in result.output.lower()

    def test_watch_help(self) -> None:
        """Test watch command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["watch", "--help"])

        assert result.exit_code == 0
        assert "watch" in result.output
        assert "--config-path" in result.output
        # Should NOT have --tui flag
        assert "--tui" not in result.output

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_runs_tui(self, mock_tui_app: Mock, tmp_path: Path) -> None:
        """Test watch command runs the TUI application."""
        mock_app_instance = Mock()
        mock_app_instance.run = Mock()
        mock_tui_app.return_value = mock_app_instance

        config_file = tmp_path / "test.conf"
        config_file.write_text("""
        [repositories.test]
        path = "/tmp/test"
        enabled = true

        [repositories.test.rule]
        type = "supsrc.rules.inactivity"
        period = "30s"

        [repositories.test.repository]
        type = "supsrc.engines.git"
        """)

        runner = CliRunner()
        runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        # Should create and run TUI app
        mock_tui_app.assert_called_once()
        call_args = mock_tui_app.call_args
        assert call_args[1]["config_path"] == config_file
        # Should have cli_shutdown_event parameter
        assert "cli_shutdown_event" in call_args[1]
        mock_app_instance.run.assert_called_once()

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", False)
    def test_watch_without_textual(self, tmp_path: Path) -> None:
        """Test watch command when textual is not available."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()
        result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        assert result.exit_code == 1
        assert "textual" in result.output.lower()
        assert "install" in result.output.lower()

    def test_watch_with_invalid_config(self) -> None:
        """Test watch command with invalid config path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["watch", "--config-path", "/nonexistent/config.conf"])

        assert result.exit_code != 0

    def test_watch_with_env_config(self, tmp_path: Path) -> None:
        """Test watch command with config from environment variable."""
        config_file = tmp_path / "env_test.conf"
        config_file.write_text("""
        [repositories.env-test]
        path = "/tmp/env-test"
        enabled = true

        [repositories.env-test.rule]
        type = "supsrc.rules.manual"

        [repositories.env-test.repository]
        type = "supsrc.engines.git"
        """)

        runner = CliRunner()

        with patch.dict("os.environ", {"SUPSRC_CONF": str(config_file)}):
            with patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True):
                with patch("supsrc.cli.watch_cmds.SupsrcTuiApp") as mock_tui_app:
                    mock_app_instance = Mock()
                    mock_tui_app.return_value = mock_app_instance

                    runner.invoke(cli, ["watch"])

                    # Should use config from env var
                    mock_tui_app.assert_called_once()
                    # Config path should be the one from env var
                    call_args = mock_tui_app.call_args
                    assert str(call_args[1]["config_path"]) == str(config_file)

    @patch("supsrc.cli.watch_cmds.log")
    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    def test_watch_logging_setup(self, mock_log: Mock, tmp_path: Path) -> None:
        """Test that watch command sets up logging correctly."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        with patch("supsrc.cli.watch_cmds.SupsrcTuiApp") as mock_tui_app:
            mock_app_instance = Mock()
            mock_tui_app.return_value = mock_app_instance

            runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        # Should log startup message
        mock_log.info.assert_called()

    def test_watch_handles_tui_errors(self, tmp_path: Path) -> None:
        """Test watch command handles TUI errors gracefully."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        with patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True):
            with patch("supsrc.cli.watch_cmds.SupsrcTuiApp") as mock_tui_app:
                # Simulate TUI crash during instantiation
                mock_tui_app.side_effect = Exception("TUI crashed!")

                result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        assert result.exit_code != 0
        # The error is now printed to stderr and captured in result.output
        assert "error" in result.output.lower() or "crashed" in result.output.lower()

    def test_watch_keyboard_interrupt(self, tmp_path: Path) -> None:
        """Test watch command handles keyboard interrupt gracefully."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        with patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True):
            with patch("supsrc.cli.watch_cmds.SupsrcTuiApp") as mock_tui_app:
                mock_app_instance = Mock()
                # Click's runner translates KeyboardInterrupt to a special exception
                # that it then handles by printing "Aborted!"
                mock_app_instance.run.side_effect = KeyboardInterrupt()
                mock_tui_app.return_value = mock_app_instance

                result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        # Assert standard Click behavior for KeyboardInterrupt
        assert result.exit_code == 1
        assert "aborted" in result.output.lower()


# 🧪👀
