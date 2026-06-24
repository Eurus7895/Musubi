"""Entry point for the musubi CLI.

musubi-tier: substrate
expires-when: never — MCP-server CLI entrypoint; stable surface.


Usage:
    musubi serve     ← start MCP stdio server (used by .vscode/mcp.json)
    musubi setup     ← guided onboarding wizard (deps, LLM endpoint, mcp.json)
"""

import sys


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else ""

    if cmd == "serve":
        from server import serve
        serve()
        return

    if cmd == "setup":
        import setup_wizard
        raise SystemExit(setup_wizard.main(args[1:]))

    print("Usage: musubi {serve|setup}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
