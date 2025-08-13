#
# tests/unit/test_git_engine.py
#
"""
Comprehensive tests for the Git engine implementation.
"""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pygit2
import pytest

from supsrc.config.models import GlobalConfig
from supsrc.engines.git import GitEngine, GitRepoSummary
from supsrc.protocols import CommitResult, PushResult, RepoStatusResult, StageResult
from supsrc.state import RepositoryState


@pytest.fixture
def git_engine() -> GitEngine:
    """Create a GitEngine instance for testing."""
    return GitEngine()


@pytest.fixture
def mock_repo_state() -> RepositoryState:
    """Create a mock repository state for testing."""
    return RepositoryState(repo_id="test-repo")


@pytest.fixture
def mock_global_config() -> GlobalConfig:
    """Create a mock global configuration for testing."""
    return GlobalConfig()


@pytest.fixture
def git_repo_path(tmp_path: Path) -> Path:
    """Create a temporary Git repository for testing."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("Git not available for testing")

    # Initialize repository
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

    # Create initial commit
    (repo_path / "README.md").write_text("Initial commit")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    return repo_path


class TestGitEngine:
    """Test GitEngine functionality."""

    async def test_get_summary_normal_repo(
        self, git_engine: GitEngine, git_repo_path: Path
    ) -> None:
        """Test getting summary from a normal repository."""
        summary = await git_engine.get_summary(git_repo_path)

        assert isinstance(summary, GitRepoSummary)
        assert not summary.is_empty
        assert summary.head_ref_name in ["main", "master"]  # Git defaults vary
        assert summary.head_commit_hash is not None
        assert len(summary.head_commit_hash) == 40  # Full SHA
        assert summary.head_commit_message_summary == "Initial commit"

    async def test_get_summary_nonexistent_repo(
        self, git_engine: GitEngine, tmp_path: Path
    ) -> None:
        """Test getting summary from a non-existent repository."""
        nonexistent_path = tmp_path / "nonexistent"

        summary = await git_engine.get_summary(nonexistent_path)

        assert summary.head_ref_name == "ERROR"
        assert "Not a Git repository" in summary.head_commit_message_summary

    async def test_get_status_clean_repo(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test getting status from a clean repository."""
        config = {"type": "supsrc.engines.git"}

        result = await git_engine.get_status(
            mock_repo_state, config, mock_global_config, git_repo_path
        )

        assert isinstance(result, RepoStatusResult)
        assert result.success
        assert result.is_clean
        assert not result.has_staged_changes
        assert not result.has_unstaged_changes
        assert not result.has_untracked_changes
        assert not result.is_conflicted

    async def test_get_status_with_changes(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test getting status from a repository with changes."""
        config = {"type": "supsrc.engines.git"}

        # Create an untracked file
        (git_repo_path / "new_file.txt").write_text("New content")

        # Modify existing file
        (git_repo_path / "README.md").write_text("Modified content")

        result = await git_engine.get_status(
            mock_repo_state, config, mock_global_config, git_repo_path
        )

        assert result.success
        assert not result.is_clean
        assert result.has_untracked_changes
        assert result.has_unstaged_changes
        assert not result.has_staged_changes

    async def test_stage_changes_all(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test staging all changes."""
        config = {"type": "supsrc.engines.git"}

        # Create changes
        (git_repo_path / "new_file.txt").write_text("New content")
        (git_repo_path / "README.md").write_text("Modified content")

        result = await git_engine.stage_changes(
            None, mock_repo_state, config, mock_global_config, git_repo_path
        )

        assert isinstance(result, StageResult)
        assert result.success
        assert len(result.files_staged) == 2
        assert "new_file.txt" in result.files_staged
        assert "README.md" in result.files_staged

    async def test_perform_commit_success(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test successful commit operation."""
        config = {
            "type": "supsrc.engines.git",
            "commit_message_template": "Test commit: {{timestamp}}",
        }

        # Create a file and stage it using pygit2 directly to ensure
        # the index is in a known state for the commit test.
        (git_repo_path / "new_file.txt").write_text("New content")
        repo = pygit2.Repository(pygit2.discover_repository(str(git_repo_path)))
        repo.index.add("new_file.txt")
        repo.index.write()

        result = await git_engine.perform_commit(
            "Test commit: {{timestamp}}",
            mock_repo_state,
            config,
            mock_global_config,
            git_repo_path,
        )

        assert isinstance(result, CommitResult)
        assert result.success
        assert result.commit_hash is not None

    async def test_perform_commit_no_changes(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test commit with no staged changes."""
        config = {"type": "supsrc.engines.git"}

        result = await git_engine.perform_commit(
            "Test commit", mock_repo_state, config, mock_global_config, git_repo_path
        )

        assert result.success
        assert result.commit_hash is None
        assert "No changes" in result.message

    async def test_perform_push_disabled(
        self,
        git_engine: GitEngine,
        git_repo_path: Path,
        mock_repo_state: RepositoryState,
        mock_global_config: GlobalConfig,
    ) -> None:
        """Test push when auto_push is disabled."""
        config = {"type": "supsrc.engines.git", "auto_push": False}

        result = await git_engine.perform_push(
            mock_repo_state, config, mock_global_config, git_repo_path
        )

        assert isinstance(result, PushResult)
        assert result.success
        assert result.skipped
        assert "disabled" in result.message

# 🧪🔧
