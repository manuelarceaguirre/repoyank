# tests/test_smoke.py

import pytest
import asyncio
from pathlib import Path

from repopacker.app import RepoPackerApp
from repopacker.app import PathInputScreen # Import for type checking

@pytest.mark.asyncio
async def test_app_initialization_and_headless_exit():
    """
    Test that the RepoPackerApp can be initialized and exits cleanly in a headless environment.
    """
    app = RepoPackerApp(initial_path=None)

    async def short_run_then_exit():
        # Allow the app to initialize and potentially show its first screen
        await asyncio.sleep(0.2)
        
        # Attempt to gracefully quit the app
        if app.is_running and not app.is_exiting:
            # If PathInputScreen is active, sending escape should close it.
            # Check the type of the active screen before calling action_cancel
            active_screen = app.screen_stack[-1]
            if isinstance(active_screen, PathInputScreen):
                 await active_screen.action_cancel()
                 await asyncio.sleep(0.1) # allow screen to process dismissal
            
            if app.is_running and not app.is_exiting:
                await app.action_quit()

    try:
        await app.run_test(
            run_before=short_run_then_exit, 
            headless=True, 
            size=None, 
            wait_for_idle_timeout=5.0
        )
    except asyncio.TimeoutError:
        if app.is_running and not app.is_exiting:
            await app.action_quit() # Attempt to force quit if timed out
        pytest.fail("App run_test timed out, indicating it did not exit cleanly or got stuck.")
    except Exception as e:
        pytest.fail(f"App initialization or run_test raised an exception: {e}")
    
    assert True # If no exceptions, test passes
