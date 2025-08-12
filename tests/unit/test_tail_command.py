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
from supsrc.config.loader import load_config


class TestTailCommand:
    """Test the tail command functionality."""

    def test_tail_command_exists(self) -> None:
        """Test that tail command exists in CLI."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "tail" in result.output
        assert (
            "Follow repository changes" in result.output
            or "Tail repository changes" in result.output
        )

    def test_tail_help(self) -> None:
        """Test tail command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["tail", "--help"])

        assert result.exit_code == 0
        assert "tail" in result.output
        assert "--config-path" in result.output
        # Should NOT have --tui flag
        assert "--tui" not in result.output

    @patch("supsrc.cli.tail_cmds.WatchOrchestrator")
    @patch("supsrc.config.loader.load_config")
    def test_tail_basic_operation(
        self, mock_load_config: Mock, mock_orchestrator_class: Mock, tmp_path: Path
    ) -> None:
        """Test tail command basic operation."""
        # Mock configuration
        mock_config = Mock()
        mock_load_config.return_value = mock_config

        # Mock orchestrator
        mock_orchestrator = Mock()
        mock_orchestrator.run = Mock()
        mock_orchestrator_class.return_value = mock_orchestrator

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

        # Mock the async event loop to prevent hanging
        with patch("asyncio.get_event_loop_policy") as mock_policy:
            mock_loop = Mock()
            mock_policy.return_value.get_event_loop.return_value = mock_loop
            mock_loop.is_closed.return_value = False
            mock_loop.run_until_complete.return_value = None

            runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        # Should load config and create orchestrator
        mock_load_config.assert_called_once()
        mock_orchestrator_class.assert_called_once_with(mock_config, is_tui=False)

    def test_tail_with_invalid_config(self) -> None:
        """Test tail command with invalid config path."""
        runner = CliRunner()
        result = runner.invoke(cli, ["tail", "--config-path", "/nonexistent/config.conf"])

        assert result.exit_code != 0
        assert "Error" in result.output or "does not exist" in result.output

    def test_tail_with_env_config(self, tmp_path: Path) -> None:
        """Test tail command with config from environment variable."""
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

        # Test with SUPSRC_CONF environment variable
        with patch.dict("os.environ", {"SUPSRC_CONF": str(config_file)}):
            with patch("supsrc.cli.tail_cmds.WatchOrchestrator"):
                with patch("supsrc.config.loader.load_config") as mock_load_config:
                    mock_load_config.return_value = Mock()

                    runner.invoke(cli, ["tail"])

                    # Should use config from env var
                    assert mock_load_config.called

    @patch("supsrc.telemetry.logger.base.log")
    def test_tail_logging_setup(self, mock_logger: Mock, tmp_path: Path) -> None:
        """Test that tail command sets up logging correctly."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        with patch("supsrc.cli.tail_cmds.WatchOrchestrator"):
            with patch("supsrc.config.loader.load_config") as mock_load_config:
                mock_load_config.return_value = Mock()

                runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        # Should log startup message
        mock_logger.info.assert_called()

    def test_tail_interrupt_handling(self, tmp_path: Path) -> None:
        """Test tail command handles keyboard interrupt gracefully."""
        config_file = tmp_path / "test.conf"
        config_file.write_text("[repositories]")

        runner = CliRunner()

        with patch("supsrc.cli.tail_cmds.WatchOrchestrator") as mock_orchestrator_class:
            mock_orchestrator = Mock()
            mock_orchestrator.run.side_effect = KeyboardInterrupt()
            mock_orchestrator_class.return_value = mock_orchestrator

            with patch("supsrc.config.loader.load_config") as mock_load_config:
                mock_load_config.return_value = Mock()

                result = runner.invoke(cli, ["tail", "--config-path", str(config_file)])

        # Should handle interrupt gracefully
        assert "stopping" in result.output.lower() or "interrupted" in result.output.lower()


# 🧪🏃‍♂️
