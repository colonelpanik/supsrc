import asyncio
import io
import pytest
from pathlib import Path
import tempfile
import shutil
from rich.console import Console

from supsrc.config import SupsrcConfig, GlobalConfig, RepositoryConfig, RuleConfig, InactivityRuleConfig
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.state import RepositoryStatus
# Minimal git setup for testing
import subprocess

from supsrc.monitor.events import MonitoredEvent, EventType
# from supsrc.state import RepositoryStatus # Already imported
import time # For simulated delays or unique task names if needed
from typing import cast


# Pytest asyncio marker
pytestmark = pytest.mark.asyncio

async def test_orchestrator_initial_console_output(minimal_config: SupsrcConfig, temp_git_repo: Path):
    captured_output = io.StringIO()
    console = Console(file=captured_output, width=120) # Fixed width for predictable wrapping
    shutdown_event = asyncio.Event()

    # Override the config's path to ensure it uses the temp repo string path
    repo_id = list(minimal_config.repositories.keys())[0]
    minimal_config.repositories[repo_id].path = str(temp_git_repo)


    orchestrator = WatchOrchestrator(
        config_path=Path("dummy_supsrc.conf"), # Not actually loaded by file in this test setup
        shutdown_event=shutdown_event,
        console=console
    )
    orchestrator.config = minimal_config # Directly set the loaded config object

    # We are not calling orchestrator.run() fully to avoid starting monitoring threads
    # Instead, we'll call parts of the startup sequence manually for this initial test.
    # This test focuses on messages generated *before* the main event loop and monitoring starts.

    console.print("[dim]INFO:[/] Initializing standard 'watch' command (non-TUI)...") # From watch_cmds.py
    orchestrator._console_message("Config loaded successfully.", style="dim", emoji="📂")
    orchestrator._console_message("Initializing repositories...", style="dim", emoji="📂")
    
    # Simulate parts of _initialize_repositories relevant to console output
    # This is a simplification; a full run would be more complex to set up here
    # For a real GitEngine summary, more mocking or setup would be needed.
    # Here, we manually call _console_message as the orchestrator would.
    repo_conf = minimal_config.repositories[repo_id]
    orchestrator._console_message(
        f"Watching: {repo_conf.path} (Branch: main, Last Commit: <hash>)", 
        repo_id=repo_id, style="dim", emoji="📂"
    )
    orchestrator._console_message(f"Monitoring active for 1 repositories.", style="dim", emoji="✅")
    orchestrator._console_message("All repositories idle. Awaiting changes... (Press Ctrl+C to exit)", style="dim", emoji="🧼")


    output = captured_output.getvalue()

    # Assertions (example, make these more specific to your UX plan)
    assert "INFO: Initializing standard 'watch' command" in output
    assert "📂 Config loaded successfully." in output
    assert "📂 Initializing repositories..." in output
    assert f"📂 [bold blue]{repo_id}[/]: Watching: {str(temp_git_repo)}" in output # Check for repo path, ensure str comparison
    assert "✅ Monitoring active for 1 repositories." in output
    assert "🧼 All repositories idle." in output
    assert "(Press Ctrl+C to exit)" in output


async def test_orchestrator_file_change_and_action_console_output(
    minimal_config: SupsrcConfig, 
    temp_git_repo: Path, 
    event_loop: asyncio.AbstractEventLoop # pytest-asyncio provides this
):
    captured_output = io.StringIO()
    console = Console(file=captured_output, width=120, color_system="truecolor") # Use a color system for more realistic capture
    shutdown_event = asyncio.Event()

    repo_id = list(minimal_config.repositories.keys())[0]
    # Ensure repo path in config is the string version of the temp path
    minimal_config.repositories[repo_id].path = str(temp_git_repo)
    # Use a very short inactivity period for faster testing
    # Need to cast to tell type checker this is an InactivityRuleConfig
    rule_config = cast(InactivityRuleConfig, minimal_config.repositories[repo_id].rule)
    rule_config.period_seconds = 1 

    orchestrator = WatchOrchestrator(
        config_path=Path("dummy_supsrc.conf"), # Not loaded by file
        shutdown_event=shutdown_event,
        console=console,
        # app=None # Explicitly
    )
    orchestrator.config = minimal_config # Pre-set config

    # Start the orchestrator run method as a background task
    orchestrator_task = event_loop.create_task(orchestrator.run(), name="OrchestratorRunTest")

    # Allow orchestrator to initialize (config loaded, repos initialized, monitoring starts)
    # This needs to be long enough for the initial messages to print.
    await asyncio.sleep(0.5) 

    # Simulate a file change event being put onto the queue
    # This bypasses the actual file system monitoring for more direct control in this test
    test_file_path = temp_git_repo / "new_file.txt"
    test_file_path.write_text("Hello, world!")
    
    # Manually create and queue a MonitoredEvent
    # The MonitoringService would normally do this
    event = MonitoredEvent(
        repo_id=repo_id,
        event_type=EventType.MODIFY, # Or CREATE
        src_path=test_file_path,
        is_directory=False,
        timestamp=time.time()
    )
    # Ensure event_queue is initialized before putting item
    # This might happen if orchestrator.run() hasn't progressed far enough
    # or if monitor_service wasn't fully initialized in this test setup
    while orchestrator.event_queue is None: # Should be rare in real code, but defensive for test
        await asyncio.sleep(0.01)
    await orchestrator.event_queue.put(event)
    
    # Allow time for the event to be processed, rule to trigger, and actions to complete
    # This duration needs to account for the inactivity rule (1s) + action time
    await asyncio.sleep(2.5) # Increased to allow for full cycle

    # Signal shutdown
    shutdown_event.set()
    
    # Wait for the orchestrator task to complete
    try:
        await asyncio.wait_for(orchestrator_task, timeout=5.0)
    except asyncio.TimeoutError:
        if not orchestrator_task.done(): # Check if not already done
            orchestrator_task.cancel() # Ensure it's cancelled if timeout occurs
            # Wait for cancellation to complete
            await asyncio.gather(orchestrator_task, return_exceptions=True)
        pytest.fail("Orchestrator task timed out during shutdown.")
    except asyncio.CancelledError:
        # This can happen if shutdown_event.set() leads to a clean cancellation.
        # Check if it was truly due to our shutdown_event or other reasons.
        if not shutdown_event.is_set():
            pytest.fail("Orchestrator task was cancelled unexpectedly before shutdown event was fully processed.")
        pass # Task was cancelled, which is fine if shutdown_event was set.


    output = captured_output.getvalue()
    
    # --- Assertions ---
    # Initial messages
    assert "📂 Config loaded successfully." in output
    assert f"📂 [bold blue]{repo_id}[/]: Watching: {str(temp_git_repo)}" in output
    assert "✅ Monitoring active for 1 repositories." in output
    # assert "🧼 All repositories idle." in output # This might be quickly replaced by change detected

    # Event processing
    assert f"✏️ [bold blue]{repo_id}[/]: Change detected: new_file.txt" in output
    assert f"🧪 [bold blue]{repo_id}[/]: Evaluating inactivity rule" in output # or specific rule type
    
    # Action messages (order can sometimes vary slightly with async, be a bit flexible or check for presence)
    # Depending on exact timing and if the "waiting for inactivity" message appears before action
    # It's possible the "Rule triggered" message appears very quickly.
    assert f"✅ [bold blue]{repo_id}[/]: Rule triggered: Commit due." in output

    # Check for key action phases
    assert f"🔄 [bold blue]{repo_id}[/]: Checking repository status..." in output
    assert f"🔄 [bold blue]{repo_id}[/]: Staging changes..." in output
    assert f"✅ [bold blue]{repo_id}[/]: Staged 1 file(s)." in output # GitEngine specific
    assert f"🔄 [bold blue]{repo_id}[/]: Performing commit..." in output
    assert f"✅ [bold blue]{repo_id}[/]: Commit complete. Hash:" in output # Check for start of message

    # Push might be skipped or succeed depending on default test git config
    # For now, let's assume it's configured to push or skip cleanly.
    # This depends on the default global_config behavior for push_on_commit
    # If GitEngine's perform_push has console output for skipped, it would be good to check.
    # Example: (assuming default is to skip if no remote or push_on_commit is false)
    # assert f"🚫 [bold blue]{repo_id}[/]: Push skipped" in output or f"✅ [bold blue]{repo_id}[/]: Push successful." in output


    # Shutdown messages
    assert "Shutdown requested..." in output # Style might be dim
    assert "Cleaning up..." in output        # Style might be dim
    assert "Monitoring stopped." in output   # Style might be dim
    assert "Cleanup complete." in output     # Style might be dim
