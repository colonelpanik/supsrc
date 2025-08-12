#!/usr/bin/env python3
"""
Direct test of hot reload functionality.
"""

import asyncio
import tempfile
from pathlib import Path

# Ensure we can import supsrc
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from supsrc.config.loader import load_config
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.monitor import MonitoredEvent


async def test_hot_reload():
    print("🧪 Testing Hot Reload Directly")
    print("=" * 50)
    
    # Create temporary config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        config_path = Path(f.name)
        f.write("""
[global]
log_level = "INFO"

[repositories.test]
enabled = true
path = "/tmp/test"

[repositories.test.rule]
type = "supsrc.rules.manual"

[repositories.test.repository]
engine = "supsrc.engines.git"
""")
    
    try:
        # Create orchestrator
        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(config_path, shutdown_event)
        
        # Load initial config
        orchestrator.config = load_config(config_path)
        print(f"✅ Initial config loaded: {len(orchestrator.config.repositories)} repositories")
        print(f"   Log level: {orchestrator.config.global_config.log_level}")
        
        # Test reload_config directly
        print("\n🔄 Testing reload_config method...")
        
        # Modify config file
        config_path.write_text("""
[global]
log_level = "DEBUG"

[repositories.test]
enabled = true
path = "/tmp/test"

[repositories.test.rule]
type = "supsrc.rules.manual"

[repositories.test.repository]
engine = "supsrc.engines.git"

[repositories.test2]
enabled = true
path = "/tmp/test2"

[repositories.test2.rule]
type = "supsrc.rules.manual"

[repositories.test2.repository]
engine = "supsrc.engines.git"
""")
        print("✅ Config file modified")
        
        # Mock required methods
        orchestrator._initialize_repositories = lambda: ["test", "test2"]
        orchestrator._setup_monitoring = lambda x: x
        orchestrator._post_tui_state_update = lambda: None
        orchestrator._console_message = lambda *args, **kwargs: print(f"  Console: {args[0]}")
        orchestrator._post_tui_log = lambda *args, **kwargs: print(f"  TUI Log: {args[2]}")
        orchestrator.monitor_service = type('obj', (object,), {'stop': lambda: None, 'start': lambda: None})()
        
        # Test reload
        result = await orchestrator.reload_config()
        
        print(f"\n📊 Reload result: {'SUCCESS' if result else 'FAILED'}")
        if result:
            print(f"   New config: {len(orchestrator.config.repositories)} repositories")
            print(f"   Log level: {orchestrator.config.global_config.log_level}")
            print(f"   Paused: {orchestrator._is_paused}")
        
        # Test config file change event
        print("\n🔄 Testing config file change event handling...")
        
        # Create a config change event
        config_event = MonitoredEvent(
            repo_id="__config__",
            event_type="modified",
            src_path=config_path,
            is_directory=False,
        )
        
        # Add to queue
        await orchestrator.event_queue.put(config_event)
        
        # Process one event
        orchestrator.reload_config = lambda: asyncio.create_task(mock_reload())
        
        async def mock_reload():
            print("  ✅ reload_config was called!")
            return True
        
        # Run consumer for a short time
        consumer_task = asyncio.create_task(orchestrator._consume_events())
        await asyncio.sleep(1)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(consumer_task, timeout=2)
        except asyncio.TimeoutError:
            pass
        
        print("\n✅ Test completed!")
        
    finally:
        config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(test_hot_reload())


# 🧪✅