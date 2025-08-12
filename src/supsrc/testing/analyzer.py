#
# src/supsrc/testing/analyzer.py
#
"""
Orchestrates test execution and LLM-based analysis.
"""
from pathlib import Path

import structlog

from supsrc.llm.protocols import LlmAnalysisResult, LlmProvider
from supsrc.testing.protocols import TestRunner

log = structlog.get_logger("testing.analyzer")


class LlmTestAnalyzer:
    """
    Uses a TestRunner to execute tests and an LlmProvider to analyze the results.
    """
    def __init__(self, runner: TestRunner, llm_provider: LlmProvider):
        self._runner = runner
        self._llm = llm_provider
        self._log = log.bind(
            runner=type(runner).__name__,
            llm_provider=type(llm_provider).__name__,
        )

    async def analyze(
        self,
        staged_diff: str,
        staged_files: list[str],
        command: list[str],
        repo_path: Path,
    ) -> LlmAnalysisResult:
        """
        Runs tests, then invokes the LLM with the results for analysis.

        Args:
            staged_diff: The git diff of staged code changes.
            staged_files: A list of file paths that were staged.
            command: The test command to execute.
            repo_path: The path to the repository being analyzed.

        Returns:
            The analysis and suggestion from the LLM provider.
        """
        self._log.info("Starting test analysis process")

        # 1. Execute the test runner
        test_result = await self._runner.run_tests(command, repo_path)

        # 2. Invoke the LLM provider with the test results and diff
        combined_output = f"--- STDOUT ---\n{test_result.stdout}\n\n--- STDERR ---\n{test_result.stderr}"

        analysis_result = await self._llm.analyze_and_suggest(
            staged_diff=staged_diff,
            staged_files=staged_files,
            test_output=combined_output,
            test_exit_code=test_result.exit_code,
            repo_path=repo_path,
        )

        self._log.info(
            "LLM analysis complete",
            is_safe_to_commit=analysis_result.is_safe_to_commit,
            analysis_type=analysis_result.analysis_type,
        )

        return analysis_result

# 🔼⚙️
