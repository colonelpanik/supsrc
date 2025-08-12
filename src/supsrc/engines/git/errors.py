#
# engines/git/errors.py
#
"""
Custom exceptions for Git engine operations within supsrc.
"""

from supsrc.exceptions import SupsrcError  # Inherit from base supsrc error


class GitEngineError(SupsrcError):
    """Base exception for errors originating from the GitEngine."""

    def __init__(
        self,
        message: str,
        repo_path: str | None = None,
        details: Exception | None = None,
    ):
        self.repo_path = repo_path
        self.details = details
        full_message = f"GitEngineError: {message}"
        if repo_path:
            full_message += f" (Repo: '{repo_path}')"
        super().__init__(full_message)
        if details and hasattr(self, "add_note"):
            self.add_note(f"Underlying error: {type(details).__name__}: {details}")


class GitStatusError(GitEngineError):
    """Error occurred while checking Git status."""

    pass


class GitStageError(GitEngineError):
    """Error occurred during staging (git add)."""

    pass


class GitCommitError(GitEngineError):
    """Error occurred during commit."""

    pass


class GitPushError(GitEngineError):
    """Error occurred during push."""

    pass


class GitAuthenticationError(GitPushError):
    """Specific error for authentication failures during push."""

    pass


class GitRemoteError(GitEngineError):
    """Error related to Git remotes (not found, connection issues etc.)."""

    pass


# 🔼⚙️
