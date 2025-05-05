#
# filename: src/supsrc/engines/git/runner.py
#
"""
Helper for running blocking pygit2 operations in a separate thread.
"""

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial, wraps
from typing import Any

import pygit2  # type: ignore
import structlog

log = structlog.get_logger("engines.git.runner")

# Type alias for the runner
Pygit2Runner: type = Callable[..., Coroutine[Any, Any, Any]]

def run_pygit2_async(func: Callable[..., Any]) -> Pygit2Runner:
    """
    Decorator or wrapper to run a blocking pygit2 function
    in the default executor thread pool.
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        # Use partial to bind arguments to the function
        func_call = partial(func, *args, **kwargs)
        log.debug(
            "Running pygit2 function via thread",
            func_name=func.__name__,
            args_len=len(args),
            kwargs_keys=list(kwargs.keys()),
        )
        try:
            result = await loop.run_in_executor(None, func_call)
            log.debug("pygit2 function completed successfully", func_name=func.__name__)
            return result
        except Exception as e:
            # Log the exception but re-raise it so the caller can handle it
            log.error("Error executing pygit2 function in thread", func_name=func.__name__, error=str(e), exc_info=True)
            raise # Re-raise the original exception
    return wrapper

# Example of wrapping a pygit2 function directly if preferred over decoration
async def repo_discover_async(path: str) -> str | None:
    """Async wrapper for pygit2.discover_repository."""
    return await run_pygit2_async(pygit2.discover_repository)(path) # type: ignore

# 🔼⚙️
