
<div align="center">

# 🔼⚙️ `supsrc`

**Keep your work safe, effortlessly.**

Automated Git commit/push utility based on filesystem events and rules.

[![PyPI Version](https://img.shields.io/pypi/v/supsrc?style=flat-square)](https://pypi.org/project/supsrc/) <!-- Placeholder -->
[![Python Versions](https://img.shields.io/pypi/pyversions/supsrc?style=flat-square)](https://pypi.org/project/supsrc/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](https://opensource.org/licenses/Apache-2.0)
[![Package Manager: uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/refs/heads/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![Powered by Structlog](https://img.shields.io/badge/powered%20by-structlog-lightgrey.svg?style=flat-square)](https://www.structlog.org/)

---

**Never forget to commit again!** `supsrc` watches your specified repositories for changes and automatically stages, commits, and (optionally) pushes them according to rules you define. Perfect for frequent checkpointing, synchronizing work-in-progress, or ensuring volatile experiments are saved.

</div>

## 🤔 Why `supsrc`?

*   **Automated Checkpoints:** Working on something complex or experimental? `supsrc` can automatically commit your changes after a period of inactivity or after a certain number of saves, creating a safety net without interrupting your flow.
*   **Effortless Syncing:** Keep a work-in-progress branch automatically pushed to a remote for backup or collaboration, without manual `git add/commit/push` steps.
*   **Simple Configuration:** Define your repositories and rules in a clear TOML file.
*   **Focused:** Designed specifically for the "watch and sync" workflow, aiming to be simpler than custom scripting or more complex backup solutions for this specific task.

## ✨ Features

*   **📂 Directory Monitoring:** Watches specified repository directories recursively for file changes using `watchdog`.
*   **📜 Rule-Based Triggers:**
    *   **Inactivity:** Trigger actions after a configurable period of no file changes (e.g., `30s`, `5m`).
    *   **Save Count:** Trigger actions after a specific number of file save events.
    *   **Manual:** Disable automatic triggers (useful for testing or specific setups).
*   **⚙️ Git Engine:**
    *   Interacts with Git repositories using `pygit2`.
    *   Automatically stages modified, added, and deleted files (respecting `.gitignore`).
    *   Performs commits with customizable message templates (including timestamps, save counts, and change summaries).
    *   Optionally pushes changes to a configured remote and branch.
    *   Handles SSH Agent authentication and basic HTTPS (user/token via env vars).
*   **📝 TOML Configuration:** Easy-to-understand configuration file (`supsrc.conf`).
*   **🕶️ `.gitignore` Respect:** Automatically ignores files specified in the repository's `.gitignore`.
*   **📊 Structured Logging:** Detailed logging using `structlog` for observability (JSON or colored console output).
*   **🖥️ Optional TUI:** An interactive Terminal User Interface (built with `textual`) for monitoring repository status and logs in real-time.
*   **📟 Tail Mode:** A headless, non-interactive mode for monitoring repositories without terminal control issues (useful for scripts and automation).

## 🚀 Installation

### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install supsrc
uv pip install supsrc

# Install with TUI support
uv pip install 'supsrc[tui]'
```

### Using pip

Ensure you have Python 3.11 or later installed:

```bash
pip install supsrc

# With TUI support
pip install 'supsrc[tui]'
```

## 💡 Usage

1.  **Create a Configuration File:** By default, `supsrc` looks for `supsrc.conf` in the current directory. See the [Configuration](#-configuration) section below for details.

2.  **Run the Watcher:**

    ```bash
    # Run in standard console mode
    supsrc watch

    # Specify a different config file
    supsrc watch -c /path/to/your/config.toml

    # Increase log verbosity
    supsrc watch --log-level DEBUG

    # Run in tail mode (headless, non-interactive)
    supsrc tail
    supsrc tail -c /path/to/your/config.toml
    ```

3.  **Check Configuration:** Validate and display the loaded configuration (including environment variable overrides):

    ```bash
    supsrc config show
    supsrc config show -c path/to/config.toml
    ```

4.  **Stop the Watcher:** Press `Ctrl+C` to stop the watcher gracefully.

## ⚙️ Configuration (`supsrc.conf`)

Create a file named `supsrc.conf` (or specify another path using `-c`). Here's an example:

```toml
# Example supsrc.conf

# Global settings (can be overridden by environment variables like SUPSRC_LOG_LEVEL)
[global]
log_level = "INFO" # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Define repositories to monitor
[repositories]

  # Unique identifier for this repository monitoring task
  [repositories.my-project]
    # Path to the Git repository (use '~' for home directory)
    path = "~/dev/my-project"
    # Set to false to temporarily disable monitoring for this repo
    enabled = true

    # Define the rule that triggers actions
    [repositories.my-project.rule]
      # Trigger after 5 minutes of inactivity
      type = "supsrc.rules.inactivity" # Built-in rule type
      period = "5m" # Format: XhYmZs (e.g., "30s", "10m", "1h5m")

      # --- OR ---
      # Trigger after every 10 save events
      # type = "supsrc.rules.save_count"
      # count = 10

      # --- OR ---
      # Disable automatic triggers (requires external mechanism if actions are needed)
      # type = "supsrc.rules.manual"

    # Configure the repository engine (currently only Git)
    [repositories.my-project.repository]
      type = "supsrc.engines.git" # Must be specified

      # --- Git Engine Specific Options ---
      # Automatically push after successful commit? (Default: false)
      auto_push = true
      # Remote to push to (Default: 'origin')
      remote = "origin"
      # Branch to push (Default: uses the current checked-out branch)
      # branch = "main"
      # Commit message template (Go template syntax)
      # Available placeholders: {{timestamp}}, {{repo_id}}, {{save_count}}, {{change_summary}}
      commit_message_template = "feat: Auto-sync changes at {{timestamp}}\n\n{{change_summary}}"

  [repositories.another-repo]
    path = "/path/to/another/repo"
    enabled = true
    [repositories.another-repo.rule]
      type = "supsrc.rules.inactivity"
      period = "30s"
    [repositories.another-repo.repository]
      type = "supsrc.engines.git"
      auto_push = false # Keep commits local for this one
```

### Environment Variable Overrides

*   `SUPSRC_CONF`: Path to the configuration file.
*   `SUPSRC_LOG_LEVEL`: Sets the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
*   `SUPSRC_LOG_FILE`: Path to write JSON logs to a file.
*   `SUPSRC_JSON_LOGS`: Set to `true`, `1`, `yes`, or `on` to output console logs as JSON.

## 룰 Rules Explained

The `[repositories.*.rule]` section defines when `supsrc` should trigger its actions (stage, commit, push).

*   **`type = "supsrc.rules.inactivity"`**
    *   Triggers when no filesystem changes have been detected in the repository for the duration specified by `period`.
    *   `period`: A string defining the inactivity duration (e.g., `"30s"`, `"5m"`, `"1h"`).
*   **`type = "supsrc.rules.save_count"`**
    *   Triggers when the number of detected save events reaches the specified `count`. The count resets after a successful action sequence.
    *   `count`: A positive integer (e.g., `10`).
*   **`type = "supsrc.rules.manual"`**
    *   Disables automatic triggering by `supsrc`. Actions would need to be initiated externally if this rule is used (primarily for testing or advanced scenarios).

## 🔩 Engines

`supsrc` uses engines to interact with different types of repositories.

*   **`type = "supsrc.engines.git"`**
    *   The primary engine for interacting with Git repositories.
    *   Uses the `pygit2` library.
    *   Supports status checks, staging (respecting `.gitignore`), committing, and pushing.

### Git Engine Authentication

The Git engine currently supports:

1.  **SSH Agent:** If your remote URL is SSH-based (e.g., `git@github.com:...`), `supsrc` will attempt to use `pygit2.KeypairFromAgent` to authenticate via a running SSH agent. Ensure your agent is running and the correct key is loaded.
2.  **HTTPS (Environment Variables):** For HTTPS URLs (e.g., `https://github.com/...`), `supsrc` will look for the following environment variables:
    *   `GIT_USERNAME`: Your Git username.
    *   `GIT_PASSWORD`: Your Git password **or preferably a Personal Access Token (PAT)**.

*Note: Storing credentials directly is generally discouraged. Using an SSH agent or short-lived tokens is recommended.*

## 🖥️ Textual TUI (Optional)

If installed (`pip install 'supsrc[tui]'`) and run with `supsrc watch --tui`, a terminal user interface provides:

*   A live-updating table showing the status, last change time, save count, and errors for each monitored repository.
*   A scrolling log view displaying messages from `supsrc`.


## 🤝 Contributing

Contributions are welcome! Please feel free to open an issue to report bugs, suggest features, or ask questions. Pull requests are greatly appreciated.

### Development Setup

We use `uv` for development:

```bash
# Clone the repository
git clone https://github.com/provide-io/supsrc.git
cd supsrc

# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode with TUI support
uv pip install -e ".[tui]"

# Install development tools
uv pip install pytest ruff mypy

# Run tests
uv run pytest

# Run linting
uv run ruff check .
uv run ruff format .
```

## 📜 License

This project is licensed under the **Apache License 2.0**. See the [LICENSE](LICENSE) file for details. <!-- Ensure LICENSE file exists -->

## 🙏 Acknowledgements

`supsrc` builds upon several fantastic open-source libraries, including:

*   [`pygit2`](https://www.pygit2.org/) for Git interactions.
*   [`watchdog`](https://github.com/gorakhargosh/watchdog) for filesystem monitoring.
*   [`structlog`](https://www.structlog.org/) for structured logging.
*   [`attrs`](https://www.attrs.org/) & [`cattrs`](https://catt.rs/) for data classes and configuration structuring.
*   [`click`](https://click.palletsprojects.com/) for the command-line interface.
*   [`textual`](https://github.com/Textualize/textual) for the optional TUI.
*   [`pathspec`](https://github.com/cpburnz/python-path-specification) for `.gitignore` handling.
