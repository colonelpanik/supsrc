import pytest
from pathlib import Path
import subprocess
import shutil # Added for robust cleanup, though tmp_path handles it mostly

from supsrc.config import SupsrcConfig, GlobalConfig, RepositoryConfig, RuleConfig, InactivityRuleConfig

@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "test_repo"
    if repo_path.exists(): # Robustness: clean up if exists from a previous failed run
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
    repo_id = "test_repo_1"
    # Ensure the path is a string for Pydantic model validation
    repo_path_str = str(temp_git_repo)

    return SupsrcConfig(
        global_config=GlobalConfig(default_rule_type="inactivity", default_commit_message="Test commit"),
        repositories={
            repo_id: RepositoryConfig(
                name="Test Repository 1",
                path=repo_path_str, 
                enabled=True,
                rule=InactivityRuleConfig(period_seconds=30), 
                repository={"type": "supsrc.engines.git", "branch": "main"} # Using main as default
            )
        },
        config_file_path=Path("dummy_supsrc.conf") 
    )
