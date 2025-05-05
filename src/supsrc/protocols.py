#
# supsrc/protocols.py
#
"""
Defines the runtime protocols for supsrc components like Rules, Engines,
and standard result objects. Uses concrete attrs classes for results.
"""

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import attrs  # Import attrs
import structlog

from supsrc.config.models import GlobalConfig  # Assuming config models exist

# Re-export or define core types needed by protocols
from supsrc.state import RepositoryState  # Assuming state.py exists

log = structlog.get_logger("protocols")

# --- Concrete Result Objects (using attrs) ---
# These are returned by engine implementations.

@attrs.define(frozen=True, slots=True)
class PluginResultBase:
    """Base for plugin results, providing common fields."""
    success: bool
    message: str | None = None
    details: dict[str, Any] | None = attrs.field(factory=dict)


@attrs.define(frozen=True, slots=True)
class ConversionResult(PluginResultBase):
    """Concrete result from a conversion step."""
    processed_files: list[Path] | None = attrs.field(factory=list)
    output_data: Any | None = None


@attrs.define(frozen=True, slots=True)
class RepoStatusResult(PluginResultBase):
    """Concrete result from checking repository status."""
    is_clean: bool = False
    has_staged_changes: bool = False
    has_unstaged_changes: bool = False
    has_untracked_changes: bool = False
    is_conflicted: bool = False
    is_unborn: bool = False
    current_branch: str | None = None


@attrs.define(frozen=True, slots=True)
class StageResult(PluginResultBase):
    """Concrete result from staging changes."""
    files_staged: list[str] | None = attrs.field(factory=list)


@attrs.define(frozen=True, slots=True)
class CommitResult(PluginResultBase):
    """Concrete result from performing a commit."""
    commit_hash: str | None = None # None if commit was skipped (e.g., no changes)


@attrs.define(frozen=True, slots=True)
class PushResult(PluginResultBase):
    """Concrete result from performing a push."""
    remote_name: str | None = None
    branch_name: str | None = None
    skipped: bool = False # True if push was skipped due to config


# --- Engine/Rule Protocols ---

@runtime_checkable
class Rule(Protocol):
    """Protocol for a rule that determines if an action should trigger."""

    def check(self, state: RepositoryState, config: Any, global_config: GlobalConfig) -> bool:
        """
        Checks if the rule's condition is met based on state and config.

        Args:
            state: The current RepositoryState.
            config: The specific configuration block for this rule instance
                    (e.g., an InactivityRuleConfig object).
            global_config: The global configuration section.

        Returns:
            True if the condition is met, False otherwise.
        """
        ...

# ConversionStep Protocol remains the same conceptually
@runtime_checkable
class ConversionStep(Protocol):
    """Protocol for a step in the file conversion/processing pipeline."""
    async def process(
        self, files: list[Path], state: RepositoryState, config: Any, global_config: GlobalConfig, working_dir: Path
    ) -> ConversionResult: ...


@runtime_checkable
class RepositoryEngine(Protocol):
    """Protocol for interacting with a repository (VCS or other)."""

    async def get_status(
        self, state: RepositoryState, config: dict[str, Any], global_config: GlobalConfig, working_dir: Path
    ) -> RepoStatusResult: # <- Expects the concrete attrs class
        """Check the current status of the repository (clean, changes, etc.)."""
        ...

    async def stage_changes(
        self, files: list[Path] | None, state: RepositoryState, config: dict[str, Any], global_config: GlobalConfig, working_dir: Path
    ) -> StageResult: # <- Expects the concrete attrs class
        """Stage specified files, or all changes if files is None."""
        ...

    async def perform_commit(
        self, message_template: str, state: RepositoryState, config: dict[str, Any], global_config: GlobalConfig, working_dir: Path
    ) -> CommitResult: # <- Expects the concrete attrs class
        """Perform the commit action with the given message template."""
        ...

    async def perform_push(
        self, state: RepositoryState, config: dict[str, Any], global_config: GlobalConfig, working_dir: Path
    ) -> PushResult: # <- Expects the concrete attrs class
        """Perform the push action."""
        ...

    # --- Optional Methods (Examples) ---
    async def get_summary(self, working_dir: Path) -> Any:
        """Get a summary of the repository's current state (e.g., HEAD commit)."""
        # Define a specific SummaryResult protocol/attrs class if needed
        log.warning("get_summary called on base protocol, implementation needed.")
        return None

# 🔼⚙️
