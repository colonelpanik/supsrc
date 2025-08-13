# src/supsrc/engines/git/stage.py

"""
Git staging logic (git add) using pygit2.
"""

from pathlib import Path

import pygit2  # type: ignore[import-untyped]
import structlog

from supsrc.protocols import StageResult

from .exceptions import GitStageError  # Updated import
from .runner import run_pygit2_async

log = structlog.get_logger("engines.git.stage")


async def stage_git_changes(
    repo: pygit2.Repository, working_dir: Path, files: list[Path] | None = None
) -> StageResult:
    """
    Stages changes in the Git repository using pygit2 asynchronously.

    Args:
        repo: An initialized pygit2.Repository object.
        working_dir: The repository's working directory path.
        files: A list of specific file paths (relative to repo root) to stage.
               If None, stages all changes (equivalent to `git add .`).

    Returns:
        StageResult indicating success or failure.
    """
    action = "all changes" if files is None else f"{len(files)} specific file(s)"
    log.debug(f"Staging {action}", repo_path=str(working_dir))

    try:
        index = await run_pygit2_async(repo.index)  # Get the index object
        
        # Refresh the index to ensure we have the latest state from disk
        await run_pygit2_async(index.read)

        if files is None:
            # Stage all changes (tracked, untracked, deleted)
            await run_pygit2_async(index.add_all)
            # Need to handle removed files explicitly? repo.status() should show them
            # as WT_DELETED. add_all *should* handle staging deletions, but let's verify.
            # An alternative is to iterate through status and add/remove individually.
            # For simplicity, starting with add_all.
            # If files were deleted, we might need:
            # status = await run_pygit2_async(repo.status)
            # for filepath, flags in status.items():
            #     if flags & pygit2.GIT_STATUS_WT_DELETED:
            #         await run_pygit2_async(index.remove, filepath)
            # Let's assume add_all handles this correctly for now based on typical git behavior.
            log.debug("Staged all detected changes.", repo_path=str(working_dir))
        else:
            # Stage specific files
            # Ensure paths are relative to the working_dir for pygit2
            relative_files = []
            for f in files:
                try:
                    # Convert absolute paths potentially passed in back to relative
                    abs_f = Path(f).resolve()
                    relative_path_str = str(abs_f.relative_to(working_dir))
                    relative_files.append(relative_path_str)
                    await run_pygit2_async(index.add, relative_path_str)
                except ValueError:
                    log.warning(
                        "File path is not relative to working directory, skipping staging.",
                        file=str(f),
                        repo_path=str(working_dir),
                    )
                except KeyError:
                    log.warning(
                        "File path not found in repository index, skipping staging.",
                        file=str(f),
                        repo_path=str(working_dir),
                    )

            log.debug(
                "Staged specific files",
                count=len(relative_files),
                files=relative_files,
                repo_path=str(working_dir),
            )

        # Write the index changes to disk
        await run_pygit2_async(index.write)
        log.debug("Index written successfully.", repo_path=str(working_dir))

        return StageResult(success=True, message=f"Staged {action} successfully.")

    except Exception as e:
        log.error(
            "Failed to stage changes",
            repo_path=str(working_dir),
            error=str(e),
            exc_info=True,
        )
        if isinstance(e, GitStageError):
            raise
        raise GitStageError(
            f"Failed to stage changes: {e}", repo_path=str(working_dir), details=e
        ) from e

# 🔼⚙️
