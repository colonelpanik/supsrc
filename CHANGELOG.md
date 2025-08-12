# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PowerShell environment script (`env.ps1`) for Windows support
- Working environment configuration file (`wrkenv.toml`) 
- Timer cancellation messages in TUI log with clock emoji (🕐)
- Descriptive file change messages in TUI showing specific file actions
- Countdown display with hand emojis for last 10 seconds in TUI
- Relative time formatting for recent commits in TUI
- File statistics refresh functionality without UI flashing
- Move event deduplication to prevent duplicate delete events
- Examples directory with demo scripts moved from root

### Changed
- Enhanced `env.sh` script with improved functionality and robustness
- Simplified `pyproject.toml` - removed many optional dependencies
- Updated dependency versions in `uv.lock`
- Improved terminal control handling in tail command - removed Rich console to fix signal issues
- Changed timer cancellation emoji from stop (🛑) to clock (🕐) for better clarity
- Enhanced TUI app with better timer management and lifecycle handling
- Improved orchestrator with stats refresh and move event handling
- Better state management with timer cancellation when repository becomes clean
- Enhanced file monitoring with proper move/rename detection

### Fixed
- Ctrl+C not working properly in tail command due to Rich terminal control
- Terminal control issues in tail command by removing Rich console usage
- Duplicate delete events when files are moved/renamed
- Timer not cancelling when repository becomes clean
- Config watcher issues causing UI problems
- TUI cursor positioning problems
- Inactivity timer not properly cancelling on clean repository state
- Missing environment variable support for logging options (SUPSRC_LOG_LEVEL, SUPSRC_LOG_FILE, SUPSRC_JSON_LOGS)
- Excessive warning logs for events outside repository path (changed to debug level)

### Removed
- Obsolete documentation files from docs directory:
  - `2025-05-23-supsrc-busted-tui.pdf`
  - `code-review-2025-05-23-claude.md`
  - `scraps/log-format.txt`
  - `TODO.md` (moved to root)
- Test file `test_cty_models.py` (447 lines removed)
- Session continuation text file from previous development

### Development
- Extensive test updates for new functionality
- Improved integration tests for monitoring features
- Enhanced unit tests for CLI commands, config, orchestrator, state, and TUI
- Better test coverage for tail and watch commands

### Internal
- Major refactoring of orchestrator runtime (836 lines changed)
- Significant TUI app improvements (431 lines changed)
- Enhanced state management system
- Improved Git engine implementations for better performance
- Better error handling and logging throughout the codebase