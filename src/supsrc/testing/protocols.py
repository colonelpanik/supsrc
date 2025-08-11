#
# src/supsrc/testing/protocols.py
#
"""
Defines protocols and data structures for test execution.
"""
from pathlib import Path
from typing import Protocol, runtime_checkable

from attrs import define


@define(frozen=True, slots=True)
class TestRunResult:
    """
    Structured result from a test runner execution.
    """
    success: bool
    exit_code: int
    stdout: str
    stderr: str


@runtime_checkable
class TestRunner(Protocol):
    """
    Protocol for a test runner that can execute a project's test suite.
    """
    async def run_tests(
        self,
        command: list[str],
        working_dir: Path,
    ) -> TestRunResult:
        """
        Runs the configured test command in the specified directory.

        Args:
            command: The command and arguments to execute.
            working_dir: The directory from which to run the command.

        Returns:
            A TestRunResult with the outcome of the test execution.
        """
        ...

# 🔼⚙️
