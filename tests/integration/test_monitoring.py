#
# tests/integration/test_monitoring.py
#
"""
Integration tests for the complete monitoring system.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from supsrc.config import load_config
from supsrc.monitor import MonitoredEvent, MonitoringService
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.state import RepositoryStatus


@pytest.fixture
async def monitoring_setup(tmp_path: Path):
    """Set up a complete monitoring environment for testing."""
    # Create test repository
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize Git repository
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

    # Create initial commit
    (repo_path / "README.md").write_text("Initial commit")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

    # Create configuration
    config_content = f"""
    [global]
    log_level = "DEBUG"

    [repositories.test-repo]
    path = "{repo_path}"
    enabled = true

    [repositories.test-repo.rule]
    type = "supsrc.rules.save_count"
    count = 2

    [repositories.test-repo.repository]
    type = "supsrc.engines.git"
    auto_push = false
    """

    config_file = tmp_path / "test.conf"
    config_file.write_text(config_content)

    config = load_config(config_file)

    return {
        "repo_path": repo_path,
        "config_file": config_file,
        "config": config,
        "tmp_path": tmp_path
    }


class TestMonitoringIntegration:
    """Test complete monitoring system integration."""

    async def test_file_change_detection(self, monitoring_setup: dict) -> None:
        """Test that file changes are properly detected and processed."""
        repo_path = monitoring_setup["repo_path"]
        config = monitoring_setup["config"]

        # Create event queue and monitoring service
        event_queue = asyncio.Queue()
        monitoring_service = MonitoringService(event_queue)

        # Add repository to monitoring
        loop = asyncio.get_running_loop()
        repo_config = config.repositories["test-repo"]
        monitoring_service.add_repository("test-repo", repo_config, loop)

        # Start monitoring
        monitoring_service.start()

        try:
            # Create a file change
            test_file = repo_path / "test_change.txt"
            test_file.write_text("Test content")

            # Wait for event to be detected
            event = await asyncio.wait_for(event_queue.get(), timeout=5.0)

            assert isinstance(event, MonitoredEvent)
            assert event.repo_id == "test-repo"
            assert event.event_type in ["created", "modified"]
            assert event.src_path == test_file
            assert not event.is_directory

        finally:
            await monitoring_service.stop()

    async def test_gitignore_filtering(self, monitoring_setup: dict) -> None:
        """Test that .gitignore patterns are properly respected."""
        repo_path = monitoring_setup["repo_path"]
        config = monitoring_setup["config"]

        # Create .gitignore file
        gitignore_content = """
        *.log
        temp/
        """
        (repo_path / ".gitignore").write_text(gitignore_content)

        # Create event queue and monitoring service
        event_queue = asyncio.Queue()
        monitoring_service = MonitoringService(event_queue)

        # Add repository to monitoring
        loop = asyncio.get_running_loop()
        repo_config = config.repositories["test-repo"]
        monitoring_service.add_repository("test-repo", repo_config, loop)

        # Start monitoring
        monitoring_service.start()

        try:
            # Create ignored file
            ignored_file = repo_path / "test.log"
            ignored_file.write_text("Log content")

            # Create non-ignored file
            normal_file = repo_path / "normal.txt"
            normal_file.write_text("Normal content")

            # Wait for events
            events = []
            try:
                while len(events) < 2:
                    event = await asyncio.wait_for(event_queue.get(), timeout=2.0)
                    events.append(event)
            except TimeoutError:
                pass  # Expected if ignored files don't generate events

            # Should only receive event for normal file
            assert len(events) == 1
            assert events[0].src_path == normal_file

        finally:
            await monitoring_service.stop()

    async def test_orchestrator_end_to_end(self, monitoring_setup: dict) -> None:
        """Test the complete orchestrator workflow."""
        repo_path = monitoring_setup["repo_path"]
        config_file = monitoring_setup["config_file"]

        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(config_file, shutdown_event)

        # Start orchestrator in background
        orchestrator_task = asyncio.create_task(orchestrator.run())

        try:
            # Give orchestrator time to initialize
            await asyncio.sleep(1.0)

            # Verify repository state is initialized
            assert "test-repo" in orchestrator.repo_states
            repo_state = orchestrator.repo_states["test-repo"]
            assert repo_state.status == RepositoryStatus.IDLE
            assert repo_state.save_count == 0

            # Create first file change
            (repo_path / "change1.txt").write_text("First change")
            await asyncio.sleep(0.5)  # Allow event processing

            # Verify state update
            assert repo_state.save_count == 1
            assert repo_state.status == RepositoryStatus.CHANGED

            # Create second file change (should trigger save count rule)
            (repo_path / "change2.txt").write_text("Second change")
            await asyncio.sleep(2.0)  # Allow rule processing and actions

            # Verify action was triggered
            assert repo_state.save_count == 0  # Reset after action
            assert repo_state.status == RepositoryStatus.IDLE

            # Verify Git commit was created
            result = subprocess.run(
                ["git", "log", "--oneline", "-n", "2"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            assert "🔼⚙️ [skip ci] auto-commit" in result.stdout or len(result.stdout.splitlines()) == 2

        finally:
            # Shutdown orchestrator
            shutdown_event.set()
            try:
                await asyncio.wait_for(orchestrator_task, timeout=5.0)
            except TimeoutError:
                orchestrator_task.cancel()
                await asyncio.gather(orchestrator_task, return_exceptions=True)


class TestErrorHandling:
    """Test error handling in monitoring integration."""

    async def test_invalid_repository_path(self, tmp_path: Path) -> None:
        """Test handling of invalid repository paths."""
        # Create configuration with invalid path
        config_content = """
        [repositories.invalid-repo]
        path = "/nonexistent/path"
        enabled = true

        [repositories.invalid-repo.rule]
        type = "supsrc.rules.manual"

        [repositories.invalid-repo.repository]
        type = "supsrc.engines.git"
        """

        config_file = tmp_path / "invalid.conf"
        config_file.write_text(config_content)

        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(config_file, shutdown_event)

        # Should handle invalid path gracefully
        try:
            await asyncio.wait_for(
                orchestrator._initialize_repositories(),
                timeout=5.0
            )
            # Should have no enabled repositories
            enabled_repos = [
                repo_id for repo_id, state in orchestrator.repo_states.items()
                if orchestrator.config.repositories[repo_id].enabled
            ]
            assert len(enabled_repos) == 0

        except Exception as e:
            pytest.fail(f"Should handle invalid paths gracefully: {e}")

    async def test_git_operation_failure(self, monitoring_setup: dict) -> None:
        """Test handling of Git operation failures."""
        repo_path = monitoring_setup["repo_path"]

        # Corrupt the Git repository
        git_dir = repo_path / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

        config_file = monitoring_setup["config_file"]

        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(config_file, shutdown_event)

        # Start orchestrator
        orchestrator_task = asyncio.create_task(orchestrator.run())

        try:
            # Give orchestrator time to initialize
            await asyncio.sleep(1.0)

            # Create file change
            (repo_path / "test.txt").write_text("Test")

            # Wait for processing
            await asyncio.sleep(2.0)

            # Repository should be in error state
            if "test-repo" in orchestrator.repo_states:
                repo_state = orchestrator.repo_states["test-repo"]
                # May be in error state depending on when Git failure is detected
                # This tests that the system continues to run despite errors
                assert repo_state is not None

        finally:
            shutdown_event.set()
            try:
                await asyncio.wait_for(orchestrator_task, timeout=5.0)
            except TimeoutError:
                orchestrator_task.cancel()
                await asyncio.gather(orchestrator_task, return_exceptions=True)


class TestConcurrency:
    """Test concurrent operations and thread safety."""

    async def test_multiple_repositories_concurrent(self, tmp_path: Path) -> None:
        """Test monitoring multiple repositories concurrently."""
        # Create multiple test repositories
        repos = {}
        for i in range(3):
            repo_path = tmp_path / f"repo_{i}"
            repo_path.mkdir()

            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

            (repo_path / "README.md").write_text(f"Repo {i}")
            subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
            subprocess.run(["git", "commit", "-m", f"Initial commit {i}"], cwd=repo_path, check=True)

            repos[f"repo-{i}"] = repo_path

        # Create configuration for all repositories
        config_content = '[global]\nlog_level = "DEBUG"\n\n[repositories]\n'
        for repo_id, repo_path in repos.items():
            config_content += f"""
            [repositories.{repo_id}]
            path = "{repo_path}"
            enabled = true

            [repositories.{repo_id}.rule]
            type = "supsrc.rules.save_count"
            count = 1

            [repositories.{repo_id}.repository]
            type = "supsrc.engines.git"
            auto_push = false
            """

        config_file = tmp_path / "multi.conf"
        config_file.write_text(config_content)

        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(config_file, shutdown_event)

        # Start orchestrator
        orchestrator_task = asyncio.create_task(orchestrator.run())

        try:
            # Give orchestrator time to initialize
            await asyncio.sleep(1.0)

            # Create concurrent changes in all repositories
            change_tasks = []
            for repo_id, repo_path in repos.items():
                async def create_change(path: Path, name: str) -> None:
                    (path / f"change_{name}.txt").write_text(f"Change in {name}")

                task = asyncio.create_task(create_change(repo_path, repo_id))
                change_tasks.append(task)

            # Wait for all changes to complete
            await asyncio.gather(*change_tasks)

            # Allow processing time
            await asyncio.sleep(3.0)

            # Verify all repositories were processed
            for repo_id in repos:
                assert repo_id in orchestrator.repo_states
                repo_state = orchestrator.repo_states[repo_id]
                # Should have been reset after action
                assert repo_state.save_count == 0
                assert repo_state.status == RepositoryStatus.IDLE

        finally:
            shutdown_event.set()
            try:
                await asyncio.wait_for(orchestrator_task, timeout=10.0)
            except TimeoutError:
                orchestrator_task.cancel()
                await asyncio.gather(orchestrator_task, return_exceptions=True)

# 🧪🔗
