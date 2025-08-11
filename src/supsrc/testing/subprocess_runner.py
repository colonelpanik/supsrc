#
# src/supsrc/testing/subprocess_runner.py
#
"""
A generic, source-agnostic test runner using asyncio.subprocess.
"""
import asyncio
from pathlib import Path

import structlog

from supsrc.exceptions import SupsrcError
from supsrc.testing.protocols import TestRunner, TestRunResult

log = structlog.get_logger("testing.runner")


class SubprocessTestRunner(TestRunner):
    """
    Implements the TestRunner protocol by executing a command in a subprocess.
    """
    async def run_tests(
        self,
        command: list[str],
        working_dir: Path,
    ) -> TestRunResult:
        """
        Executes the given test command using asyncio.create_subprocess_exec.
        """
        runner_log = log.bind(
            command=" ".join(command),
            working_dir=str(working_dir),
        )
        runner_log.info("Executing test command")

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )

            stdout_bytes, stderr_bytes = await process.communicate()

            exit_code = process.returncode if process.returncode is not None else -1
            success = exit_code == 0

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            runner_log.info(
                "Test command finished",
                exit_code=exit_code,
                success=success,
            )
            runner_log.debug(
                "Test command output",
                stdout_len=len(stdout),
                stderr_len=len(stderr),
            )

            return TestRunResult(
                success=success,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        except FileNotFoundError:
            runner_log.error("Test command not found", command_executable=command[0])
            raise SupsrcError from FileNotFoundError(f"Test command not found: '{command[0]}'. Is it installed and in the system's PATH?")
        except Exception as e:
            runner_log.exception("An unexpected error occurred while running tests")
            raise SupsrcError(f"An unexpected error occurred during test execution: {e}") from e

# 🔼⚙️
