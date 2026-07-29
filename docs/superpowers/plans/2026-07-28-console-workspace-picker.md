# Console workspace picker

## Context

The Console currently derives one project root at process startup. Operators
who launch the installed GUI from the Musubi directory therefore cannot point
Orchestrator at a sibling application without leaving the GUI and arranging
environment variables manually.

## Goal

Add a Settings control that chooses and persists an existing application
folder, then restarts the Console with that folder as the governed workspace.
Keep the Musubi installation and LLM profile locations independent from the
selected application root.

## Tech stack

- React Settings view
- Tauri 2 dialog plugin and Rust command bridge
- JSON preference under the operating-system user config directory
- `MUSUBI_WORKSPACE` as the dedicated filesystem/working-directory boundary

## Steps

1. Introduce `MUSUBI_WORKSPACE`, preferred by filesystem tools without changing
   the existing meaning of `MUSUBI_ROOT` as the packaged Musubi location.
2. Persist a canonical selected directory and the current LLM config path.
3. Restart the Console after selection so database, session, pipeline, and
   audit ownership all move together rather than partially hot-swapping state.
4. Surface Browse/Apply controls in Settings and block switching during a run.
5. Cover resolution, persistence validation, launch environment, and frontend
   action wiring with deterministic tests.
