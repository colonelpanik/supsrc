#
# engines/git/base.py
#
"""
Implementation of the RepositoryEngine protocol using pygit2.
"""

import getpass  # For SSH agent username fallback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pygit2
import structlog
from pygit2.credentials import CredentialType

from supsrc.config.models import GlobalConfig
from supsrc.engines.git.info import GitRepoSummary

# Use absolute imports
from supsrc.protocols import (
    CommitResult,
    PushResult,
    RepositoryEngine,
    RepoStatusResult,
    StageResult,
)
from supsrc.state import RepositoryState

log = structlog.get_logger("engines.git.base")

# --- Constants for Change Summary ---
MAX_SUMMARY_FILES = 10
SUMMARY_ADDED_PREFIX = "A "
SUMMARY_MODIFIED_PREFIX = "M "
SUMMARY_DELETED_PREFIX = "D "
SUMMARY_RENAMED_PREFIX = "R "  # R old -> new
SUMMARY_TYPECHANGE_PREFIX = "T "


class GitEngine(RepositoryEngine):
    """Implements RepositoryEngine using pygit2."""

    def __init__(self) -> None:
        self._log = log.bind(engine_id=id(self))
        self._log.debug("GitEngine initialized")

    def _get_repo(self, working_dir: Path) -> pygit2.Repository:
        """Helper to get the pygit2 Repository object."""
        try:
            repo_path = pygit2.discover_repository(str(working_dir))
            if not repo_path:
                raise pygit2.GitError(
                    f"Not a Git repository (or any of the parent directories): {working_dir}"
                )
            repo = pygit2.Repository(repo_path)
            return repo
        except pygit2.GitError as e:
            self._log.error("Failed to open Git repository", path=str(working_dir), error=str(e))
            raise

    def _get_config_value(self, key: str, config: dict[str, Any], default: Any = None) -> Any:
        """Safely gets a value from the engine-specific config dict."""
        return config.get(key, default)

    # --- Authentication Callback ---
    def _credentials_callback(
        self, url: str, username_from_url: str | None, allowed_types: int
    ) -> CredentialType | None:
        """Provides credentials to pygit2, attempting SSH agent first."""
        cred_log = self._log.bind(
            url=url, username_from_url=username_from_url, allowed_types=allowed_types
        )
        cred_log.debug("Credentials callback invoked")

        # 1. Try SSH Agent (KeypairFromAgent) if SSH key is allowed
        if allowed_types & CredentialType.SSH_KEY:
            try:
                ssh_user = username_from_url or getpass.getuser()
                cred_log.debug("Attempting SSH agent authentication", ssh_user=ssh_user)
                credentials = pygit2.KeypairFromAgent(ssh_user)
                cred_log.info("Using SSH agent credentials.")
                return credentials
            except pygit2.GitError as e:
                cred_log.debug("SSH agent authentication failed or not available", error=str(e))
            except Exception as e:
                cred_log.error(
                    "Unexpected error during SSH agent auth attempt",
                    error=str(e),
                    exc_info=True,
                )

        # 2. TODO: Add HTTPS Token/UserPass from Environment Variables
        # if allowed_types & CredentialType.USERPASS_PLAINTEXT:
        #    git_user = os.getenv("GIT_USERNAME")
        #    git_token = os.getenv("GIT_PASSWORD") # Treat as token
        #    if git_user and git_token:
        #        cred_log.info("Using User/Pass credentials from environment variables.")
        #        return pygit2.UserPass(git_user, git_token)
        #    else:
        #        cred_log.debug("GIT_USERNAME or GIT_PASSWORD env vars not set for UserPass.")

        cred_log.warning("No suitable credentials found or configured via callbacks.")
        return None

    async def get_summary(self, working_dir: Path) -> GitRepoSummary:
        """Gets a summary of the repository's HEAD state."""
        # (Implementation remains the same)
        try:
            repo = self._get_repo(working_dir)
            if repo.is_empty:
                return GitRepoSummary(is_empty=True)
            if repo.head_is_unborn:
                return GitRepoSummary(head_ref_name="UNBORN")

            head_ref = repo.head
            head_commit = head_ref.peel()
            commit_msg_summary = (head_commit.message or "").split("\n", 1)[0]
            
            # Convert commit time to datetime with UTC timezone
            from datetime import datetime, timezone
            commit_timestamp = datetime.fromtimestamp(head_commit.commit_time, tz=timezone.utc)

            return GitRepoSummary(
                head_ref_name=head_ref.shorthand,
                head_commit_hash=str(head_commit.id),
                head_commit_message_summary=commit_msg_summary,
                head_commit_timestamp=commit_timestamp,
            )
        except pygit2.GitError as e:
            self._log.error("Failed to get Git summary", path=str(working_dir), error=str(e))
            return GitRepoSummary(head_ref_name="ERROR", head_commit_message_summary=str(e))
        except Exception as e:
            self._log.exception("Unexpected error getting Git summary", path=str(working_dir))
            return GitRepoSummary(
                head_ref_name="ERROR", head_commit_message_summary=f"Unexpected: {e}"
            )

    async def get_status(
        self,
        state: RepositoryState,
        config: dict[str, Any],
        global_config: GlobalConfig,
        working_dir: Path,
    ) -> RepoStatusResult:
        # (Implementation remains the same)
        status_log = self._log.bind(repo_id=state.repo_id, path=str(working_dir))
        status_log.debug(f"get_status called for {working_dir.name}")
        try:
            repo = self._get_repo(working_dir)
            current_branch = "UNBORN" if repo.head_is_unborn else repo.head.shorthand

            if repo.is_bare:
                status_log.warning("Cannot get status for bare repository.")
                return RepoStatusResult(
                    success=False, message="Cannot get status for bare repository"
                )

            if repo.index.conflicts:
                status_log.warning("Repository has merge conflicts.")
                return RepoStatusResult(
                    success=True, is_conflicted=True, current_branch=current_branch
                )

            pygit2_status = repo.status()
            is_clean = not pygit2_status and not repo.head_is_unborn

            if is_clean:
                # For clean repos, we still need to count files
                has_staged = False
                has_unstaged = False
                has_untracked = False
            else:
                has_staged = any(
                    s & pygit2.GIT_STATUS_INDEX_NEW
                    or s & pygit2.GIT_STATUS_INDEX_MODIFIED
                    or s & pygit2.GIT_STATUS_INDEX_DELETED
                    or s & pygit2.GIT_STATUS_INDEX_RENAMED
                    or s & pygit2.GIT_STATUS_INDEX_TYPECHANGE
                    for s in pygit2_status.values()
                )
                has_unstaged = any(
                    s & pygit2.GIT_STATUS_WT_MODIFIED
                    or s & pygit2.GIT_STATUS_WT_DELETED
                    or s & pygit2.GIT_STATUS_WT_TYPECHANGE
                    or s & pygit2.GIT_STATUS_WT_RENAMED
                    for s in pygit2_status.values()
                )
                has_untracked = any(s & pygit2.GIT_STATUS_WT_NEW for s in pygit2_status.values())
                is_clean = not (has_staged or has_unstaged or has_untracked)
            
            # Count file statistics
            added_files = 0
            deleted_files = 0
            modified_files = 0
            
            if not is_clean:
                for filepath, flags in pygit2_status.items():
                    # Count added files (new in working tree or index)
                    if (flags & pygit2.GIT_STATUS_WT_NEW) or (flags & pygit2.GIT_STATUS_INDEX_NEW):
                        added_files += 1
                    # Count deleted files
                    elif (flags & pygit2.GIT_STATUS_WT_DELETED) or (flags & pygit2.GIT_STATUS_INDEX_DELETED):
                        deleted_files += 1
                    # Count modified files (modified, renamed, or type changed)
                    elif ((flags & pygit2.GIT_STATUS_WT_MODIFIED) or (flags & pygit2.GIT_STATUS_INDEX_MODIFIED) or
                          (flags & pygit2.GIT_STATUS_WT_RENAMED) or (flags & pygit2.GIT_STATUS_INDEX_RENAMED) or
                          (flags & pygit2.GIT_STATUS_WT_TYPECHANGE) or (flags & pygit2.GIT_STATUS_INDEX_TYPECHANGE)):
                        modified_files += 1
            
            changed_files = added_files + deleted_files + modified_files
            
            # Count total files in repository
            # Always try to count files - don't skip based on repo state
            total_files = 0
            try:
                # Use git ls-files to count tracked files
                import subprocess
                result = subprocess.run(
                    ['git', 'ls-files'], 
                    cwd=working_dir,
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    # Count non-empty lines
                    files = [f for f in result.stdout.strip().split('\n') if f]
                    total_files = len(files)
                    status_log.debug(f"File count for {working_dir.name}: {total_files}")
                else:
                    status_log.error(f"git ls-files failed for {working_dir.name}: {result.stderr}")
                    # Try alternative if ls-files fails
                    if repo and not repo.is_empty:
                        try:
                            total_files = len(repo.index)
                            status_log.debug(f"Used index length fallback: {total_files} files")
                        except:
                            pass
            except Exception as e:
                status_log.error(f"Error counting files: {e}")
                total_files = 0

            status_log.debug(
                "Repository status check",
                staged=has_staged,
                unstaged=has_unstaged,
                untracked=has_untracked,
                is_clean=is_clean,
                is_unborn=repo.head_is_unborn,
                total_files=total_files,
                changed_files=changed_files,
                added=added_files,
                deleted=deleted_files,
                modified=modified_files,
            )
            return RepoStatusResult(
                success=True,
                is_clean=is_clean,
                is_unborn=repo.head_is_unborn,
                has_staged_changes=has_staged,
                has_unstaged_changes=has_unstaged,
                has_untracked_changes=has_untracked,
                current_branch=current_branch,
                total_files=total_files,
                changed_files=changed_files,
                added_files=added_files,
                deleted_files=deleted_files,
                modified_files=modified_files,
            )

        except pygit2.GitError as e:
            status_log.error("Failed to get Git status", error=str(e))
            return RepoStatusResult(success=False, message=f"Git status error: {e}")
        except Exception as e:
            status_log.exception("Unexpected error getting Git status")
            return RepoStatusResult(success=False, message=f"Unexpected status error: {e}")

    async def stage_changes(
        self,
        files: list[Path] | None,
        state: RepositoryState,
        config: dict[str, Any],
        global_config: GlobalConfig,
        working_dir: Path,
    ) -> StageResult:
        # (Implementation remains the same)
        stage_log = self._log.bind(repo_id=state.repo_id, path=str(working_dir))
        stage_log.info("Staging changes...")
        try:
            repo = self._get_repo(working_dir)
            index = repo.index
            staged_list = []

            if files:
                repo_root = Path(repo.workdir)
                relative_files = []
                for f in files:
                    try:
                        rel_path = str(f.relative_to(repo_root))
                        relative_files.append(rel_path)
                        index.add(rel_path)
                        staged_list.append(rel_path)
                    except ValueError:
                        stage_log.warning(
                            "File path not relative to repo root, cannot stage individually",
                            file=str(f),
                        )
                    except KeyError:
                        stage_log.warning(
                            "File not found in repository index, skipping staging",
                            file=str(f),
                        )
            else:
                status = repo.status()
                files_to_add = []
                files_to_remove = []
                for filepath, flags in status.items():
                    if (
                        flags & pygit2.GIT_STATUS_WT_DELETED
                        or flags & pygit2.GIT_STATUS_INDEX_DELETED
                    ):
                        files_to_remove.append(filepath)
                    elif (
                        flags & pygit2.GIT_STATUS_WT_NEW
                        or flags & pygit2.GIT_STATUS_INDEX_NEW
                        or flags & pygit2.GIT_STATUS_WT_MODIFIED
                        or flags & pygit2.GIT_STATUS_INDEX_MODIFIED
                        or flags & pygit2.GIT_STATUS_WT_RENAMED
                        or flags & pygit2.GIT_STATUS_INDEX_RENAMED
                        or flags & pygit2.GIT_STATUS_WT_TYPECHANGE
                        or flags & pygit2.GIT_STATUS_INDEX_TYPECHANGE
                    ) and not repo.path_is_ignored(filepath):
                        files_to_add.append(filepath)

                if files_to_add:
                    stage_log.debug("Adding files to index", files=files_to_add)
                    index.add_all(files_to_add)
                    staged_list.extend(files_to_add)
                if files_to_remove:
                    stage_log.debug("Removing files from index", files=files_to_remove)
                    index.remove_all(files_to_remove)

            index.write()
            stage_log.info("Staging successful", files_staged=len(staged_list))
            return StageResult(
                success=True,
                message="Changes staged successfully.",
                files_staged=staged_list,
            )

        except pygit2.GitError as e:
            stage_log.error("Failed to stage changes", error=str(e))
            return StageResult(success=False, message=f"Git staging error: {e}")
        except Exception as e:
            stage_log.exception("Unexpected error staging changes")
            return StageResult(success=False, message=f"Unexpected staging error: {e}")

    def _generate_change_summary(self, diff: pygit2.Diff) -> str:
        """Generates a summary string from a pygit2 Diff object."""
        added, modified, deleted, renamed, typechanged = [], [], [], [], []
        for delta in diff.deltas:
            path = (
                delta.new_file.path
                if delta.status != pygit2.GIT_DELTA_DELETED
                else delta.old_file.path
            )
            if delta.status == pygit2.GIT_DELTA_ADDED:
                added.append(path)
            elif delta.status == pygit2.GIT_DELTA_MODIFIED:
                modified.append(path)
            elif delta.status == pygit2.GIT_DELTA_DELETED:
                deleted.append(path)
            elif delta.status == pygit2.GIT_DELTA_RENAMED:
                renamed.append(f"{delta.old_file.path} -> {delta.new_file.path}")
            elif delta.status == pygit2.GIT_DELTA_TYPECHANGE:
                typechanged.append(path)
        # ------------------------------------

        summary_lines = []
        if added:
            summary_lines.append(f"Added ({len(added)}):")
        summary_lines.extend([f"  {SUMMARY_ADDED_PREFIX}{f}" for f in added[:MAX_SUMMARY_FILES]])
        if len(added) > MAX_SUMMARY_FILES:
            summary_lines.append(f"  ... ({len(added) - MAX_SUMMARY_FILES} more)")

        if modified:
            summary_lines.append(f"Modified ({len(modified)}):")
        summary_lines.extend(
            [f"  {SUMMARY_MODIFIED_PREFIX}{f}" for f in modified[:MAX_SUMMARY_FILES]]
        )
        if len(modified) > MAX_SUMMARY_FILES:
            summary_lines.append(f"  ... ({len(modified) - MAX_SUMMARY_FILES} more)")

        if deleted:
            summary_lines.append(f"Deleted ({len(deleted)}):")
        summary_lines.extend(
            [f"  {SUMMARY_DELETED_PREFIX}{f}" for f in deleted[:MAX_SUMMARY_FILES]]
        )
        if len(deleted) > MAX_SUMMARY_FILES:
            summary_lines.append(f"  ... ({len(deleted) - MAX_SUMMARY_FILES} more)")

        if renamed:
            summary_lines.append(f"Renamed ({len(renamed)}):")
        summary_lines.extend(
            [f"  {SUMMARY_RENAMED_PREFIX}{f}" for f in renamed[:MAX_SUMMARY_FILES]]
        )
        if len(renamed) > MAX_SUMMARY_FILES:
            summary_lines.append(f"  ... ({len(renamed) - MAX_SUMMARY_FILES} more)")

        if typechanged:
            summary_lines.append(f"Type Changed ({len(typechanged)}):")
        summary_lines.extend(
            [f"  {SUMMARY_TYPECHANGE_PREFIX}{f}" for f in typechanged[:MAX_SUMMARY_FILES]]
        )
        if len(typechanged) > MAX_SUMMARY_FILES:
            summary_lines.append(f"  ... ({len(typechanged) - MAX_SUMMARY_FILES} more)")

        return "\n".join(summary_lines)

    async def perform_commit(
        self,
        message_template: str,
        state: RepositoryState,
        config: dict[str, Any],
        global_config: GlobalConfig,
        working_dir: Path,
    ) -> CommitResult:
        commit_log = self._log.bind(repo_id=state.repo_id, path=str(working_dir))
        commit_log.info("Performing commit...")
        is_unborn = False  # Will be set after repo object is obtained

        try:
            repo = self._get_repo(working_dir)
            index = repo.index
            is_unborn = repo.head_is_unborn

            # Determine if there are actual changes to commit
            diff: pygit2.Diff | None = None
            try:
                if is_unborn:
                    commit_log.debug("Comparing index to empty tree (unborn HEAD for diff)")
                    diff = index.diff_to_tree(None) if len(index) > 0 else None
                else:
                    head_commit = repo.head.peel()
                    commit_log.debug(
                        "Comparing index to HEAD tree",
                        head_commit_oid=str(head_commit.id),
                    )
                    diff = index.diff_to_tree(head_commit.tree)
            except pygit2.GitError as diff_err:
                commit_log.warning(
                    "Could not diff index to HEAD tree, assuming changes exist for now",
                    error=str(diff_err),
                )

            if diff is not None and len(diff) == 0 and not (is_unborn and len(index) > 0):
                commit_log.info("Commit skipped: No changes detected in diff.")
                return CommitResult(
                    success=True,
                    message="Commit skipped: No changes detected.",
                    commit_hash=None,
                )
            elif diff is None and is_unborn and len(index) == 0:
                commit_log.info("Commit skipped: Unborn HEAD and empty index.")
                return CommitResult(
                    success=True,
                    message="Commit skipped: Unborn HEAD and empty index.",
                    commit_hash=None,
                )

            # Signature
            try:
                signature = repo.default_signature
            except pygit2.GitError:
                commit_log.warning("Git user name/email not configured, using fallback.")
                fallback_name = "Supsrc Automation"
                fallback_email = "supsrc@example.com"
                timestamp = int(datetime.now(UTC).timestamp())
                offset = 0  # UTC
                signature = pygit2.Signature(fallback_name, fallback_email, timestamp, offset)

            # Commit message
            change_summary_str = ""
            if diff is not None:
                change_summary_str = self._generate_change_summary(diff)
                commit_log.debug("Generated change summary", summary_length=len(change_summary_str))
            else:
                commit_log.debug(
                    "Skipping change summary generation as diff was not available/empty."
                )

            commit_message_template_str = self._get_config_value(
                "commit_message_template",
                config,
                "🔼⚙️ [skip ci] auto-commit\n\n{{change_summary}}",
            )
            timestamp_str = datetime.now(UTC).isoformat()
            commit_message = commit_message_template_str.replace("{{timestamp}}", timestamp_str)
            commit_message = commit_message.replace("{{repo_id}}", state.repo_id)
            commit_message = commit_message.replace("{{save_count}}", str(state.save_count))
            commit_message = commit_message.replace("{{change_summary}}", change_summary_str)
            commit_message = commit_message.rstrip()

            # Parents
            parents = [] if is_unborn else [repo.head.target]

            # Tree
            tree_oid = index.write_tree()
            commit_log.debug("Index tree written", tree_oid=str(tree_oid))

            # Pre-create_commit diagnostic logging
            commit_log.debug(
                "Preparing for repo.create_commit",
                author=f"{signature.name} <{signature.email}>",
                committer=f"{signature.name} <{signature.email}>",
                tree_oid_str=str(tree_oid),
                parents_str=[str(p) for p in parents],
                is_unborn_check=is_unborn,
            )
            if tree_oid is None:
                commit_log.error("tree_oid is None before create_commit, this will fail.")
                raise ValueError("Cannot create commit: tree_oid is None after index.write_tree().")

            commit_hash: str
            if is_unborn:
                commit_log.info("Performing explicit initial commit sequence for unborn HEAD.")
                commit_oid_obj = repo.create_commit(
                    None, signature, signature, commit_message, tree_oid, parents
                )
                commit_hash = str(commit_oid_obj)
                commit_log.debug(f"Initial commit object created: {commit_hash}")

                target_branch_ref_name = repo.head.target
                commit_log.debug(
                    f"HEAD is symbolic; target branch for initial commit is: {target_branch_ref_name}"
                )

                repo.create_reference(target_branch_ref_name, commit_oid_obj)
                commit_log.debug(
                    f"Created reference {target_branch_ref_name} pointing to {commit_hash}"
                )

                repo.head.set_target(target_branch_ref_name)
                commit_log.info(
                    f"Initial commit successful. HEAD now points to {target_branch_ref_name} ({commit_hash[:7]})"
                )
            else:
                ref_to_update = "HEAD"
                commit_log.info(f"Performing commit, updating reference: {ref_to_update}")
                commit_oid_obj = repo.create_commit(
                    ref_to_update,
                    signature,
                    signature,
                    commit_message,
                    tree_oid,
                    parents,
                )
                commit_hash = str(commit_oid_obj)
                commit_log.info(f"Commit successful ({commit_hash[:7]})")

            return CommitResult(
                success=True,
                message=f"Commit successful: {commit_hash[:7]}",
                commit_hash=commit_hash,
            )

        except pygit2.GitError as e:
            commit_log.error(
                "Failed to perform commit due to GitError",
                error=str(e),
                is_unborn=is_unborn,
                exc_info=True,
            )
            return CommitResult(success=False, message=f"Git commit error: {e}")
        except ValueError as e:
            commit_log.error(
                "Failed to perform commit due to ValueError",
                error=str(e),
                is_unborn=is_unborn,
                exc_info=True,
            )
            return CommitResult(success=False, message=str(e))
        except Exception as e:
            commit_log.exception("Unexpected error performing commit", is_unborn=is_unborn)
            return CommitResult(success=False, message=f"Unexpected commit error: {e}")

    async def perform_push(
        self,
        state: RepositoryState,
        config: dict[str, Any],
        global_config: GlobalConfig,
        working_dir: Path,
    ) -> PushResult:
        # (Implementation remains the same as previous version)
        push_log = self._log.bind(repo_id=state.repo_id, path=str(working_dir))

        auto_push = self._get_config_value("auto_push", config, False)
        if not auto_push:
            push_log.info("Push skipped (disabled by configuration).")
            return PushResult(success=True, message="Push skipped (disabled).", skipped=True)

        remote_name = self._get_config_value("remote", config, "origin")
        branch_name = self._get_config_value("branch", config, None)

        push_log.info(f"Performing push to remote '{remote_name}'...")
        try:
            repo = self._get_repo(working_dir)
            if repo.head_is_unborn:
                push_log.warning("Cannot push unborn HEAD.")
                return PushResult(
                    success=False,
                    message="Cannot push unborn HEAD.",
                    remote_name=remote_name,
                )

            if not branch_name:
                try:
                    branch_name = repo.head.shorthand
                    push_log.debug(f"Using current branch '{branch_name}' for push.")
                except pygit2.GitError:
                    push_log.error("Could not determine current branch name for push.")
                    return PushResult(
                        success=False,
                        message="Could not determine current branch name.",
                        remote_name=remote_name,
                    )

            remote = repo.remotes[remote_name]
            refspec = f"refs/heads/{branch_name}"

            callbacks = pygit2.RemoteCallbacks(credentials=self._credentials_callback)
            push_log.debug("Attempting push with callbacks", remote=remote_name, refspec=refspec)
            remote.push([refspec], callbacks=callbacks)

            push_log.info(f"Push successful to {remote_name}/{branch_name}.")
            return PushResult(
                success=True,
                message=f"Push successful to {remote_name}/{branch_name}.",
                remote_name=remote_name,
                branch_name=branch_name,
            )

        except KeyError:
            push_log.error(f"Remote '{remote_name}' not found.")
            return PushResult(
                success=False,
                message=f"Remote '{remote_name}' not found.",
                remote_name=remote_name,
            )
        except pygit2.GitError as e:
            push_log.error("Failed to perform push", error=str(e))
            if "authentication required" in str(e).lower():
                msg = (
                    "Git push error: Authentication failed (check SSH agent or credential config)."
                )
            elif "could not resolve host" in str(e).lower():
                msg = "Git push error: Network error or invalid remote."
            elif "rejected" in str(e).lower():
                msg = "Git push error: Push rejected (likely non-fast-forward or permissions)."
            else:
                msg = f"Git push error: {e}"
            return PushResult(
                success=False,
                message=msg,
                remote_name=remote_name,
                branch_name=branch_name,
            )
        except Exception as e:
            push_log.exception("Unexpected error performing push")
            return PushResult(
                success=False,
                message=f"Unexpected push error: {e}",
                remote_name=remote_name,
                branch_name=branch_name,
            )

    def get_commit_history(self, working_dir: Path, limit: int = 10) -> list[str]:
        """Retrieves the last N commit messages from the repository."""
        history_log = self._log.bind(working_dir=str(working_dir), limit=limit)
        history_log.debug("Fetching commit history")
        try:
            repo = self._get_repo(working_dir)
            if repo.is_empty or repo.head_is_unborn:
                return ["Repository is empty or unborn."]

            last_commits = []
            for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_TIME):
                if len(last_commits) >= limit:
                    break
                commit_time = datetime.fromtimestamp(
                    commit.commit_time, tz=datetime.now(UTC).astimezone().tzinfo
                ).strftime("%Y-%m-%d %H:%M:%S")
                # Format: "hash_short - author - date - message_summary"
                summary = (commit.message or "").split("\n", 1)[0]
                if len(summary) > 60:  # Truncate long summaries
                    summary = summary[:57] + "..."

                # Ensure commit.author is not None before accessing its properties
                author_name = commit.author.name if commit.author else "Unknown Author"

                last_commits.append(
                    f"{str(commit.id)[:7]} - {author_name} - {commit_time} - {summary}"
                )

            history_log.debug(f"Retrieved {len(last_commits)} commit history items.")
            return last_commits
        except pygit2.GitError as e:
            history_log.error("Failed to get commit history", error=str(e))
            return [f"Error fetching history: {e}"]
        except Exception as e:
            history_log.exception("Unexpected error getting commit history")
            return [f"Unexpected error fetching history: {e}"]


# 🔼⚙️
