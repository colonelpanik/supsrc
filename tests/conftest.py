#
# tests/conftest.py
#
"""
Enhanced pytest configuration and fixtures for comprehensive testing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from supsrc.config.models import (
    GlobalConfig,
    InactivityRuleConfig,
    RepositoryConfig,
    SupsrcConfig,
)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary Git repository for testing."""
    repo_path = tmp_path / "test_repo"
    if repo_path.exists():  # Robustness: clean up if exists from a previous failed run
        shutil.rmtree(repo_path)
    repo_path.mkdir()

    try:
        # Check if git is installed and accessible
        subprocess.run(["git", "--version"], check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        pytest.skip(f"Git is not available or `git --version` failed: {e}")

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    # Configure dummy user for commits if not globally configured
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

    (repo_path / "README.md").write_text("initial commit")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)
    return repo_path


@pytest.fixture
def minimal_config(temp_git_repo: Path) -> SupsrcConfig:
    """Create a minimal configuration for testing."""
    repo_id = "test_repo_1"
    # Ensure the path is a Path object for proper validation
    repo_path = temp_git_repo

    return SupsrcConfig(
        global_config=GlobalConfig(),
        repositories={
            repo_id: RepositoryConfig(
                path=repo_path,
                enabled=True,
                rule=InactivityRuleConfig(period=30),
                repository={
                    "type": "supsrc.engines.git",
                    "branch": "main",
                },  # Using main as default
            )
        },
    )


# 🧪🔧
