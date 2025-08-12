#
# supsrc/engines/git/exceptions.py
#
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


class PushRejectedError(GitCommandError):
    """Raised specifically when a push operation is rejected."""

    pass


class AuthenticationError(GitCommandError):
    """Raised when authentication fails during a remote operation."""

    pass


class NetworkError(GitCommandError):
    """Raised for network-related issues during remote operations."""

    pass


class ConflictError(GitCommandError):
    """Raised when an operation cannot proceed due to merge conflicts."""

    pass


class NoRemoteError(GitEngineError):
    """Raised when the configured remote cannot be found."""

    pass


# 🔼⚙️
