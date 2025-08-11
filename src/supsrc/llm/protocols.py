#
# src/supsrc/llm/protocols.py
#
"""
Defines the runtime protocols and data structures for LLM providers.
"""
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import attrs


@attrs.define(frozen=True, slots=True)
class LlmAnalysisResult:
    """
    Structured result from an LLM analysis of code changes and test results.
    """
    is_safe_to_commit: bool
    analysis_type: str  # e.g., "test_failure", "commit_suggestion"
    suggestion: str
    details: dict[str, Any] | None = attrs.field(factory=dict)


@runtime_checkable
class LlmProvider(Protocol):
    """
    Protocol for an LLM provider that can analyze code changes and test results.
    """
    async def analyze_and_suggest(
        self,
        staged_diff: str,
        test_output: str,
        test_exit_code: int,
        repo_path: Path,
    ) -> LlmAnalysisResult:
        """
        Analyzes test results and code diff to provide suggestions.

        Args:
            staged_diff: The git diff of staged changes.
            test_output: The combined stdout/stderr from the test run.
            test_exit_code: The exit code of the test command.
            repo_path: The path to the repository, for context.

        Returns:
            An LlmAnalysisResult with suggestions.
        """
        ...

# 🔼⚙️
