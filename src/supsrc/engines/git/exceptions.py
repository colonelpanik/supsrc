# src/supsrc/engines/git/exceptions.py

"""
Custom exceptions specific to the Git Engine for supsrc.
"""

from supsrc.exceptions import SupsrcError


class GitEngineError(SupsrcError):
    """Base class for Git engine specific errors."""

    def __init__(
        self,
        message: str,
        repo_path: str | None = None,
        details: Exception | None = None,
    ):
        self.repo_path = repo_path
        self.details = details
        full_message = f"[GitEngine] {message}"
        if repo_path:
            full_message += f" (Repo: '{repo_path}')"
        super().__init__(full_message)
        if details and hasattr(self, "add_note"):
            self.add_note(f"Original error: {type(details).__name__}: {details}")


class GitCommandError(GitEngineError):
    """Raised when a specific Git command (via pygit2) fails."""

    pass


class GitStatusError(GitCommandError):
    """Error occurred while checking Git status."""

    pass


class GitStageError(GitCommandError):
    """Error occurred during staging (git add)."""

    pass


class GitCommitError(GitCommandError):
    """Error occurred during commit."""

    pass


class GitPushError(GitCommandError):
    """Error occurred during push."""

    pass


class GitAuthenticationError(GitPushError):
    """Specific error for authentication failures during push."""

    pass


class GitRemoteError(GitPushError):
    """Error related to Git remotes (not found, connection issues etc.)."""

    pass


class GitConflictError(GitStatusError):
    """Raised when an operation cannot proceed due to merge conflicts."""

    pass


# 🔼⚙️
