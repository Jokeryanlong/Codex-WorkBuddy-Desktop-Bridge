"""Real MCP stdio handshake from another cwd; no WorkBuddy model calls."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class StdioTests(unittest.TestCase):
    def test_handshake_schema_validation_and_tools(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as cwd:
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "workbuddy_bridge", "serve"],
                    cwd=cwd,
                    env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                )
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        initialized = await session.initialize()
                        self.assertEqual(initialized.serverInfo.name, "workbuddy_worker")
                        listed = await session.list_tools()
                        names = {tool.name for tool in listed.tools}
                        self.assertEqual(names, {
                            "workbuddy_start", "workbuddy_wait", "workbuddy_status",
                            "workbuddy_cancel", "workbuddy_list", "workbuddy_result", "workbuddy_doctor",
                        })
                        invalid = await session.call_tool("workbuddy_start", {"prompt": "test", "timeout_seconds": 0})
                        self.assertFalse(json.loads(invalid.content[0].text)["ok"])
                        tasks = await session.call_tool("workbuddy_list", {})
                        self.assertEqual(json.loads(tasks.content[0].text)["tasks"], [])
                        missing = await session.call_tool("workbuddy_result", {"task_id": "missing"})
                        self.assertFalse(json.loads(missing.content[0].text)["ok"])
        asyncio.run(asyncio.wait_for(scenario(), 15))


if __name__ == "__main__":
    unittest.main()
