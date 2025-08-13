# src/supsrc/engines/git/commit.py

"""
Git commit logic using pygit2, including basic templating.
"""

from datetime import UTC, datetime
from pathlib import Path

import pygit2  # type: ignore[import-untyped]
import structlog

from supsrc.protocols import CommitResult
from supsrc.state import RepositoryState  # Needed for potential template vars

from .exceptions import GitCommitError  # Updated import
from .runner import run_pygit2_async

log = structlog.get_logger("engines.git.commit")


def _render_commit_template(template: str, state: RepositoryState, config: dict) -> str:
    """Basic commit message templating."""
    message = template
    # Use UTC time for consistency
    now_utc = datetime.now(UTC)
    replacements = {
        "{{timestamp}}": now_utc.isoformat(timespec="seconds"),  # ISO format UTC
        "{{timestamp_unix}}": str(int(now_utc.timestamp())),
        "{{repo_id}}": state.repo_id,
        "{{save_count}}": str(state.save_count),
        # Add more placeholders as needed (e.g., from config, state)
        # "{{trigger_type}}": config.get("trigger", {}).get("type", "unknown"), # Example
    }
    for placeholder, value in replacements.items():
        message = message.replace(placeholder, value)
    return message


async def perform_git_commit(
    repo: pygit2.Repository,
    working_dir: Path,
    message_template: str,
    state: RepositoryState,
    config: dict,  # Pass engine-specific config section
) -> CommitResult:
    """
    Performs a Git commit using pygit2 asynchronously.

    Args:
        repo: An initialized pygit2.Repository object.
        working_dir: The repository's working directory path.
        message_template: The commit message template string.
        state: The current RepositoryState for templating.
        config: The engine-specific configuration dictionary.

    Returns:
        CommitResult indicating success and the new commit hash.
    """
    log.debug("Attempting git commit", repo_path=str(working_dir))

    try:
        # Determine author and committer - use repo config or sensible defaults
        # pygit2 uses the config from the repository by default if available
        # You might want explicit config options for author/committer name/email
        try:
            author = await run_pygit2_async(repo.default_signature)
            committer = author  # Use same by default
            log.debug(
                "Using default signature from git config",
                author=f"{author.name} <{author.email}>",
            )
        except KeyError:
            # Fallback if no user.name/user.email is configured in git
            log.warning(
                "Git user.name/user.email not configured, using fallback.",
                repo_path=str(working_dir),
            )
            fallback_name = config.get("committer_name", "supsrc")
            fallback_email = config.get("committer_email", "supsrc@localhost")
            author = pygit2.Signature(fallback_name, fallback_email)
            committer = author

        # Render the commit message
        message = _render_commit_template(message_template, state, config)
        log.debug("Rendered commit message", template=message_template, result=message)

        # Get necessary references
        index = await run_pygit2_async(repo.index)
        
        # IMPORTANT: Refresh the index to ensure we have the latest state
        # This helps avoid race conditions where HEAD changes between operations
        await run_pygit2_async(index.read)
        
        tree_oid = await run_pygit2_async(index.write_tree)  # Create tree object from index
        log.debug("Created tree for commit", oid=str(tree_oid))

        try:
            # Check if HEAD exists and get parent commit
            # Re-read HEAD to ensure we have the latest reference
            head_ref = await run_pygit2_async(repo.head.name)  # e.g., 'refs/heads/main'
            parent_commit_oid = await run_pygit2_async(
                repo.head.target
            )  # OID of the commit HEAD points to
            parents = [parent_commit_oid]
            log.debug("Found parent commit", oid=str(parent_commit_oid), ref=head_ref)
        except pygit2.GitError as head_error:
            # Handle case for initial commit (no HEAD or unborn branch)
            log.warning("Could not resolve HEAD, likely initial commit.", error=str(head_error))
            parents = []  # No parents for the first commit
            # Determine the ref to update (e.g., refs/heads/main) - might need config
            head_ref = config.get("initial_commit_ref", "refs/heads/main")
            log.debug("Using ref for initial commit", ref=head_ref)

        # Check if tree differs from parent to avoid empty commits (optional but good practice)
        if parents:
            parent_commit = await run_pygit2_async(repo.get, parents[0])
            parent_tree_oid = parent_commit.tree_id
            if tree_oid == parent_tree_oid:
                log.info(
                    "No changes detected between index and parent commit, skipping empty commit.",
                    repo_path=str(working_dir),
                )
                return CommitResult(success=True, message="No changes to commit.", commit_hash=None)

        # Create the commit object
        try:
            commit_oid = await run_pygit2_async(
                repo.create_commit,
                head_ref,  # The reference to update (e.g., refs/heads/main)
                author,
                committer,
                message,
                tree_oid,
                parents,  # List of parent commit OIDs
            )
            commit_hash = str(commit_oid)
            log.info("Commit created successfully", repo_path=str(working_dir), hash=commit_hash)

            return CommitResult(success=True, message="Commit successful.", commit_hash=commit_hash)
        except pygit2.GitError as git_error:
            error_msg = str(git_error)
            if "current tip is not the first parent" in error_msg:
                # This indicates a race condition where HEAD changed between our read and commit
                log.warning(
                    "HEAD changed during commit operation, repository may have been modified externally",
                    repo_path=str(working_dir),
                    error=error_msg,
                )
                # Try to refresh and get latest HEAD info for debugging
                try:
                    current_head = await run_pygit2_async(repo.head.target)
                    log.debug(
                        "Current HEAD after failed commit",
                        expected_parent=str(parents[0]) if parents else "None",
                        actual_head=str(current_head),
                    )
                except Exception:
                    pass  # Ignore errors during debug info gathering
                
                raise GitCommitError(
                    "Failed to create commit: Repository HEAD changed during commit operation. "
                    "This may happen if another process modified the repository.",
                    repo_path=str(working_dir),
                    details=git_error,
                ) from git_error
            else:
                # Re-raise other git errors
                raise

    except Exception as e:
        log.error(
            "Failed to perform commit",
            repo_path=str(working_dir),
            error=str(e),
            exc_info=True,
        )
        if isinstance(e, GitCommitError):
            raise
        raise GitCommitError(f"Failed to commit: {e}", repo_path=str(working_dir), details=e) from e

# 🔼⚙️
