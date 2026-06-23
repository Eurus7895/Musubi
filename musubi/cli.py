"""Entry point for the musubi CLI.

musubi-tier: substrate
expires-when: never — MCP-server CLI entrypoint; stable surface.


Usage:
    musubi serve     ← start MCP stdio server (used by .vscode/mcp.json)
"""

import sys


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        print("Usage: musubi serve", file=sys.stderr)
        sys.exit(1)
    from server import serve
    serve()


if __name__ == "__main__":
    main()
