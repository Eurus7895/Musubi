# Windows Musubi Installer Bootstrap Design

## Summary

Musubi's Windows desktop installer should be presented as the primary Musubi
installation path, not as a console-only artifact. The first implementation
slice keeps the current Tauri GUI installer and adds a bootstrap contract around
the Python Musubi core: the installer documentation, setup wizard, and GUI
startup diagnostics all treat the `musubi` and `agent` CLIs as required runtime
companions.

This is intentionally not an offline bundled runtime yet. The goal is to make
the existing installer honest and useful: it installs the desktop surface, then
guides the user to install or repair the Python core with the normal package
path.

## Goals

- Make the Windows installer a Musubi installer experience, not a standalone
  demo console.
- Detect whether the Python core commands are available: `musubi` and `agent`.
- Point users to the exact repair command when the Python scripts directory is
  missing from `PATH`.
- Keep the GUI zero-LLM and deterministic; no substrate-side model calls.
- Preserve the local developer path for `npm run tauri:dev`.
- Keep macOS and Linux GUI installer work out of scope.

## Non-Goals

- No offline embedded Python distribution in this slice.
- No PyInstaller or bundled CLI executable in this slice.
- No automatic elevation or system-wide PATH mutation.
- No code signing changes in this slice.
- No direct GUI writes to governed substrate tables beyond the existing
  console-side `chat_log` and `meta` tables.

## Architecture

The product has two installable parts:

- Python core package: the `musubi` project under `musubi/pyproject.toml`,
  exposing `musubi` and `agent` console scripts.
- Desktop GUI package: the Tauri app under `gui/`, exposing the operator console.

The bootstrap installer slice keeps these as separate package technologies but
aligns their UX:

1. `musubi setup` checks and reports whether the CLI pair is usable.
2. The setup wizard explains that the Windows desktop installer is the primary
   GUI path and that local GUI development is optional.
3. The GUI backend reports whether it is reading a real `MUSUBI_DB` or fallback
   demo data.
4. Documentation and release text describe the artifact as the Musubi Windows
   installer bootstrap.

## Data Flow

The GUI remains attached to Musubi through configuration and SQLite:

- `MUSUBI_DB` points the GUI at the real append-only audit database.
- `MUSUBI_LLM_CONFIG` points the GUI at `.musubi/llm.json` for profile display.
- If `MUSUBI_DB` is unset, the GUI uses the existing in-memory demo database and
  surfaces that state clearly.

The Python core remains the source of truth for `musubi setup`, MCP serving,
agent execution, policy, compression, and audit writes.

## Error Handling

The bootstrap should fail soft and tell the user what to do next:

- Missing `musubi` or `agent`: report the missing command and show a Python
  install command.
- Python scripts installed outside `PATH`: show the detected user scripts
  directory and a `setx PATH` repair command.
- Missing GUI build tools: keep the existing Rust/MSVC diagnostics for local
  development only.
- Missing `MUSUBI_DB`: run the demo database, but label it as demo/fallback.

## Testing

Focused tests should cover the setup wizard helpers and scripted flows:

- Core CLI detection succeeds when both scripts are found.
- Core CLI detection reports missing commands with a repair hint.
- Windows scripted setup output describes the desktop installer as the Musubi
  installer bootstrap.
- Non-Windows setup still skips GUI installer guidance.

Rust/Tauri build checks remain CI-owned because local Windows machines may lack
Rust or MSVC. Python setup tests and the React build should stay runnable
locally.

## Follow-Up

The next installer milestone is an offline or mostly-offline bundled runtime:
ship a self-contained Musubi CLI/runtime alongside the GUI, sign the Windows
installer, and make the GUI able to launch `musubi setup` or `agent` directly
through a governed bridge.
