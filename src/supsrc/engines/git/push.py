#
# engines/git/push.py
#
"""
Enhanced Git push logic with improved authentication and error handling.
"""

from pathlib import Path

import pygit2
import structlog

from supsrc.protocols import PushResult
from supsrc.telemetry import StructLogger

from .credentials import GitCredentialManager, RemoteCallbacks
from .errors import GitAuthenticationError, GitPushError, GitRemoteError
from .runner import run_pygit2_async

log: StructLogger = structlog.get_logger("engines.git.push")


async def perform_git_push(
    repo: pygit2.Repository,
    working_dir: Path,
    remote_name: str,
    branch_name: str,
    config: dict,
) -> PushResult:
    """
    Enhanced Git push with comprehensive authentication and error handling.

    Args:
        repo: Initialized pygit2.Repository object
        working_dir: Repository's working directory path
        remote_name: Name of the remote to push to
        branch_name: Name of the local branch to push
        config: Engine-specific configuration dictionary

    Returns:
        PushResult indicating success or failure
    """
    push_log = log.bind(repo_path=str(working_dir), remote=remote_name, branch=branch_name)
    push_log.debug("Attempting git push")

    try:
        # Find the remote
        try:
            remote = await run_pygit2_async(repo.remotes.__getitem__, remote_name)
        except (KeyError, IndexError) as err:
            raise GitRemoteError(
                f"Remote '{remote_name}' not found.", repo_path=str(working_dir)
            ) from err

        push_log.debug("Found remote", remote_url=remote.url)

        # Setup enhanced credential management
        credential_manager = GitCredentialManager(config)
        callbacks = RemoteCallbacks(credential_manager)

        # Construct refspec
        local_ref = f"refs/heads/{branch_name}"
        remote_ref = local_ref
        refspec = f"{local_ref}:{remote_ref}"

        # Verify local ref exists
        try:
            await run_pygit2_async(repo.references.get, local_ref)
        except KeyError as err:
            raise GitPushError(
                f"Local branch '{branch_name}' (ref: {local_ref}) not found.",
                repo_path=str(working_dir),
            ) from err

        push_log.debug("Using refspec for push", refspec=refspec)

        # Perform the push operation
        await run_pygit2_async(remote.push, [refspec], callbacks=callbacks)

        push_log.info("Push completed successfully")
        return PushResult(
            success=True,
            message="Push completed successfully.",
            remote_name=remote_name,
            branch_name=branch_name,
        )

    except pygit2.GitError as e:
        error_msg = str(e).lower()
        push_log.error("Git error during push", error=str(e))

        # Classify error types
        if any(
            auth_term in error_msg
            for auth_term in ["authentication", "permission denied", "access denied"]
        ):
            raise GitAuthenticationError(
                f"Push authentication failed: {e}",
                repo_path=str(working_dir),
                details=e,
            ) from e
        elif any(
            net_term in error_msg
            for net_term in ["network", "connection", "resolve host", "timeout"]
        ):
            raise GitPushError(
                f"Push network error: {e}", repo_path=str(working_dir), details=e
            ) from e
        elif any(
            reject_term in error_msg
            for reject_term in ["rejected", "non-fast-forward", "fetch first"]
        ):
            raise GitPushError(
                f"Push rejected (may need pull/fetch): {e}",
                repo_path=str(working_dir),
                details=e,
            ) from e
        else:
            raise GitPushError(f"Push failed: {e}", repo_path=str(working_dir), details=e) from e

    except Exception as e:
        push_log.error("Unexpected error during push", error=str(e), exc_info=True)
        if isinstance(e, GitPushError):
            raise
        raise GitPushError(
            f"Push failed unexpectedly: {e}", repo_path=str(working_dir), details=e
        ) from e


# 🚀🔐
