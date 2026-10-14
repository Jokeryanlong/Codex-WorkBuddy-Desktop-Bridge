from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkBuddy Desktop MCP Bridge")
    parser.add_argument("command", choices=("serve", "doctor", "config"), default="serve", nargs="?")
    args = parser.parse_args()
    if args.command == "doctor":
        from workbuddy_bridge.doctor import diagnose
        result = diagnose()
        print(json.dumps(result, ensure_ascii=True, indent=2))
        raise SystemExit(0 if result["ok"] else 1)
    if args.command == "config":
        print(json.dumps({"mcpServers": {"workbuddy_worker": {
            "command": sys.executable, "args": ["-m", "workbuddy_bridge", "serve"]
        }}}, ensure_ascii=True, indent=2))
        return
    from workbuddy_bridge.server import mcp
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
