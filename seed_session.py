#!/usr/bin/env python3
"""Session Seeder: Manually log in to capture the Golden Ticket."""

import asyncio
import os
import sys
from pathlib import Path

# Reuse the client logic from the existing library
try:
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession
except ImportError:
    print("CRITICAL: MCP library not found. Ensure you are in the 'venv'.")
    sys.exit(1)

SERVER_SCRIPT = Path(__file__).resolve().parent / "server.py"
STATE_PATH = Path(__file__).resolve().parent / "data/auth_state.json"

async def run():
    if not SERVER_SCRIPT.exists():
        print(f"Error: Server script not found at {SERVER_SCRIPT}")
        return

    # Connect to the Swarm's Hands (Server)
    server_params = StdioServerParameters(
        command=os.environ.get("MCP_SERVER_PYTHON", "python"),
        args=[str(SERVER_SCRIPT)],
    )

    print("--- SESSION SEEDER PROTOCOL ---")
    print("1. Launching Browser...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Launch Headful (Visible) so you can interact
            await session.call_tool("browser_launch", arguments={"headless": False})

            # Go to Login
            print("2. Navigating to Login Page...")
            await session.call_tool("browser_navigate", arguments={"url": "https://secure.indeed.com/account/login"})

            print("\n" + "="*60)
            print("   COMMAND: LOG IN MANUALLY NOW")
            print("   1. Enter your Apple ID / Email in the browser.")
            print("   2. Handle the 2FA popup on your Mac.")
            print("   3. Wait until you see the Indeed Dashboard/Home page.")
            print("="*60 + "\n")

            # The Wait
            input(">>> Press ENTER here once you are fully logged in...")

            # The Capture
            print("3. Capturing Session State...")
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            result = await session.call_tool("browser_save_state", arguments={"path": str(STATE_PATH)})
            print(f"   [OK] {result}")

            await session.call_tool("browser_close")
            print("--- SESSION SECURED ---")
            print(f"Ticket saved to: {STATE_PATH}")
            print("You may now run 'python orchestrator.py'.")

if __name__ == "__main__":
    asyncio.run(run())
