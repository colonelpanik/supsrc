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
from supsrc.engines.git.credentials import GitCredentialManager
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

        # Create and stage changes
        (git_repo_path / "new_file.txt").write_text("New content")
        subprocess.run(["git", "add", "new_file.txt"], cwd=git_repo_path, check=True)

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
        assert len(result.commit_hash) == 40  # Full SHA

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


class TestGitCredentialManager:
    """Test Git credential management functionality."""

    def test_ssh_key_auth_missing_files(self) -> None:
        """Test SSH key authentication with missing key files."""
        config = {"ssh_key_path": "/nonexistent/key"}
        manager = GitCredentialManager(config)

        result = manager.get_credentials(
            "git@github.com:user/repo.git", "git", pygit2.GIT_CREDENTIAL_SSH_KEY
        )

        assert result is None

    @patch("os.getenv")
    def test_userpass_auth_success(self, mock_getenv: Mock) -> None:
        """Test successful username/password authentication."""
        mock_getenv.side_effect = lambda key: {
            "GIT_USERNAME": "testuser",
            "GIT_PASSWORD": "testpass",
        }.get(key)

        config = {}
        manager = GitCredentialManager(config)

        with patch("pygit2.credentials.UserPass") as mock_userpass:
            mock_userpass.return_value = Mock()

            result = manager.get_credentials(
                "https://github.com/user/repo.git",
                None,
                pygit2.GIT_CREDENTIAL_USERPASS_PLAINTEXT,
            )

            assert result is not None
            mock_userpass.assert_called_once_with("testuser", "testpass")

    @patch("os.getenv")
    def test_userpass_auth_missing_env(self, mock_getenv: Mock) -> None:
        """Test username/password authentication with missing environment variables."""
        mock_getenv.return_value = None

        config = {}
        manager = GitCredentialManager(config)

        result = manager.get_credentials(
            "https://github.com/user/repo.git",
            None,
            pygit2.GIT_CREDENTIAL_USERPASS_PLAINTEXT,
        )

        assert result is None

    @patch("pygit2.credentials.KeypairFromAgent")
    def test_ssh_agent_auth_failure(self, mock_agent: Mock) -> None:
        """Test SSH agent authentication failure."""
        mock_agent.side_effect = pygit2.GitError("SSH agent not available")

        config = {}
        manager = GitCredentialManager(config)

        result = manager.get_credentials(
            "git@github.com:user/repo.git", "git", pygit2.GIT_CREDENTIAL_SSH_KEY
        )

        # Should fall back to other methods or return None
        assert result is None


# 🧪🔧
