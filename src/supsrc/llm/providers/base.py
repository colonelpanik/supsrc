#
# src/supsrc/llm/providers/base.py
#
"""
Defines the base protocol for all LLM providers.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that all LLM provider implementations must adhere to."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        """Initializes the provider with a model name and optional API key."""
        ...

    async def generate_commit_message(self, diff: str, conventional: bool) -> str:
        """
        Generates a commit message based on the provided diff.

        Args:
            diff: The git diff of staged changes.
            conventional: If True, generate a Conventional Commits formatted message.

        Returns:
            The generated commit message string.
        """
        ...

    async def review_changes(self, diff: str) -> tuple[bool, str]:
        """
        Reviews code changes for critical issues.

        Args:
            diff: The git diff of staged changes.

        Returns:
            A tuple containing:
            - bool: True if the commit should be vetoed, False otherwise.
            - str: The reason for the veto, or "OK".
        """
        ...

    async def analyze_test_failure(self, output: str) -> str:
        """
        Analyzes the output of a failed test run.

        Args:
            output: The stdout/stderr from the failed test command.

        Returns:
            A string containing the analysis and suggestions.
        """
        ...

    async def generate_change_fragment(self, diff: str, commit_message: str) -> str:
        """
        Generates a changelog fragment.

        Args:
            diff: The git diff of the committed changes.
            commit_message: The final commit message.

        Returns:
            A single-sentence changelog entry.
        """
        ...

# 🧠📜