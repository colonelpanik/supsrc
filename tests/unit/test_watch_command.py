# tests/unit/test_watch_command.py

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
    def test_watch_runs_tui(self, mock_tui_app_class: Mock, tmp_path: Path) -> None:
        """Test watch command runs the TUI application."""
        mock_app_instance = mock_tui_app_class.return_value
        mock_app_instance.run = Mock()

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
        result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])
        
        assert result.exit_code == 0
        mock_tui_app_class.assert_called_once()
        _args, kwargs = mock_tui_app_class.call_args
        assert kwargs["config_path"] == config_file
        assert "cli_shutdown_event" in kwargs
        mock_app_instance.run.assert_called_once()

    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp", None)
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

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_with_env_config(self, mock_tui_app_class: Mock, tmp_path: Path) -> None:
        """Test watch command with config from environment variable."""
        mock_app_instance = mock_tui_app_class.return_value
        mock_app_instance.run = Mock()

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
            result = runner.invoke(cli, ["watch"])
            assert result.exit_code == 0
            mock_tui_app_class.assert_called_once()
            _args, kwargs = mock_tui_app_class.call_args
            assert str(kwargs["config_path"]) == str(config_file)

    @patch("supsrc.cli.watch_cmds.log")
    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_logging_setup(self, mock_tui_app_class: Mock, mock_log: Mock, tmp_path: Path) -> None:
        """Test that watch command sets up logging correctly."""
        mock_app_instance = mock_tui_app_class.return_value
        mock_app_instance.run = Mock()
    
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")
    
        runner = CliRunner()
        runner.invoke(cli, ["watch", "--config-path", str(config_file)])
    
        # Use assert_any_call to check if this was logged at any point.
        mock_log.info.assert_any_call("Initializing interactive dashboard...")

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_handles_tui_errors(self, mock_tui_app_class: Mock, tmp_path: Path) -> None:
        """Test watch command handles TUI errors gracefully."""
        mock_tui_app_class.side_effect = Exception("TUI crashed!")
        
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()
        # Remove the invalid mix_stderr argument from the invoke call.
        result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])
        
        assert result.exit_code != 0
        # Check the combined output stream for the error message.
        assert "error" in result.output.lower()
        assert "crashed" in result.output.lower()

    @patch("supsrc.cli.watch_cmds.TEXTUAL_AVAILABLE", True)
    @patch("supsrc.cli.watch_cmds.SupsrcTuiApp")
    def test_watch_keyboard_interrupt(self, mock_tui_app_class: Mock, tmp_path: Path) -> None:
        """Test watch command handles keyboard interrupt gracefully."""
        mock_app_instance = mock_tui_app_class.return_value
        mock_app_instance.run.side_effect = KeyboardInterrupt()

        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")
        
        runner = CliRunner()
        result = runner.invoke(cli, ["watch", "--config-path", str(config_file)])

        assert result.exit_code == 1
        assert "aborted" in result.output.lower()    

# 🧪👀
