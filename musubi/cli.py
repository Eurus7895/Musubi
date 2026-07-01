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
        surface = "full"
        rest = args[1:]
        if rest:
            if len(rest) == 2 and rest[0] == "--surface":
                surface = rest[1]
            else:
                print(
                    "Usage: musubi serve [--surface agent|operator|pipeline|full]",
                    file=sys.stderr,
                )
                sys.exit(1)
        from server import serve
        serve(surface=surface)
        return

    if cmd == "setup":
        import setup_wizard
        raise SystemExit(setup_wizard.main(args[1:]))

    print("Usage: musubi {serve|setup}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
