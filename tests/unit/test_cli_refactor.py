#
# tests/unit/test_cli_refactor.py
#
"""
Tests to ensure the CLI refactoring is complete and correct.
"""

from click.testing import CliRunner

from supsrc.cli.main import cli


class TestCLIRefactoring:
    """Test that the CLI refactoring from watch/tui to tail/watch is complete."""

    def test_no_tui_flag_in_any_command(self) -> None:
        """Test that --tui flag is completely removed from all commands."""
        runner = CliRunner()

        # Check main help
        result = runner.invoke(cli, ["--help"])
        assert "--tui" not in result.output

        # Check tail command help (if it exists)
        result = runner.invoke(cli, ["tail", "--help"])
        if result.exit_code == 0:
            assert "--tui" not in result.output

        # Check watch command help (if it exists)
        result = runner.invoke(cli, ["watch", "--help"])
        if result.exit_code == 0:
            assert "--tui" not in result.output

    def test_no_old_tui_command(self) -> None:
        """Test that the old 'tui' command no longer exists."""
        runner = CliRunner()

        # Try to run old tui command
        result = runner.invoke(cli, ["tui"])

        # Should fail because command doesn't exist
        assert result.exit_code != 0
        assert "No such command" in result.output or "Error" in result.output

    def test_new_commands_exist(self) -> None:
        """Test that new tail and watch commands exist."""
        runner = CliRunner()

        # Check main help
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "tail" in result.output
        assert "watch" in result.output

        # tail should be for following changes
        assert "tail" in result.output
        assert "follow" in result.output.lower() or "changes" in result.output.lower()

        # watch should be for interactive monitoring
        assert "watch" in result.output
        assert "interactive" in result.output.lower() or "dashboard" in result.output.lower()

    def test_command_descriptions_are_clear(self) -> None:
        """Test that command descriptions clearly differentiate tail vs watch."""
        runner = CliRunner()

        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

        output = result.output

        # Find the tail and watch descriptions
        lines = output.split("\n")
        tail_desc = ""
        watch_desc = ""

        for i, line in enumerate(lines):
            if "tail" in line and i + 1 < len(lines):
                # Usually description is on same line or next line
                tail_desc = line + " " + lines[i + 1]
            if "watch" in line and i + 1 < len(lines):
                watch_desc = line + " " + lines[i + 1]

        # Ensure descriptions are different and meaningful
        assert tail_desc != watch_desc
        assert len(tail_desc) > 10  # Has actual description
        assert len(watch_desc) > 10  # Has actual description

    def test_old_watch_behavior_moved_to_tail(self) -> None:
        """Test that the old watch (non-TUI) behavior is now in tail command."""
        runner = CliRunner()

        # Test tail command has config-path option
        result = runner.invoke(cli, ["tail", "--help"])
        assert result.exit_code == 0
        assert "--config-path" in result.output

        # Test tail command doesn't have TUI-related options
        assert "--tui" not in result.output
        assert "textual" not in result.output.lower()

    def test_new_watch_is_interactive_only(self) -> None:
        """Test that new watch command is for interactive UI only."""
        runner = CliRunner()

        # Test watch command help mentions interactive/UI
        result = runner.invoke(cli, ["watch", "--help"])
        assert result.exit_code == 0

        # Should mention it's interactive
        assert (
            "interactive" in result.output.lower()
            or "ui" in result.output.lower()
            or "dashboard" in result.output.lower()
        )


# 🧪🔄
