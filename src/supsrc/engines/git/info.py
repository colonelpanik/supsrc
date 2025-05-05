#
# engines/git/info.py
#
"""
Data classes for Git-specific information.
"""


from attrs import define


@define(frozen=True, slots=True)
class GitRepoSummary:
    """Holds summary information about a Git repository's state."""
    is_empty: bool = False
    head_ref_name: str | None = None # e.g., 'main', 'refs/heads/develop', 'UNBORN'
    head_commit_hash: str | None = None # Full commit SHA
    head_commit_message_summary: str | None = None # First line of commit message

# 🔼⚙️
