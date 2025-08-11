#
# src/supsrc/testing/factory.py
#
"""
Factory for creating TestRunner instances.
"""
import structlog

from supsrc.exceptions import ConfigurationError
from supsrc.testing.protocols import TestRunner
from supsrc.testing.subprocess_runner import SubprocessTestRunner

log = structlog.get_logger("testing.factory")

# As we add more runners (e.g., DockerTestRunner), they will be added here.
RUNNER_MAP = {
    "pytest": SubprocessTestRunner,
    "subprocess": SubprocessTestRunner, # A generic alias
}


def get_test_runner(runner_name: str) -> TestRunner:
    """
    Factory function to get an instance of a TestRunner.
    """
    runner_key = runner_name.lower()
    runner_class = RUNNER_MAP.get(runner_key)

    if not runner_class:
        log.error("Unsupported test runner specified", runner=runner_name)
        raise ConfigurationError(
            f"Unsupported test runner: '{runner_name}'. "
            f"Available runners: {list(RUNNER_MAP.keys())}"
        )

    log.debug("Instantiating test runner", runner=runner_name)
    try:
        return runner_class()
    except Exception as e:
        log.error("Failed to instantiate test runner", runner=runner_name, error=str(e))
        raise ConfigurationError(f"Failed to initialize runner '{runner_name}': {e}") from e

# 🔼⚙️
