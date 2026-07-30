# Console Workspace Picker Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the reviewed fail-open preference path, make environment-only CLI workspace selection consistent with `--workspace`, restore Windows test correctness, and remove the diff hygiene error.

**Architecture:** Keep workspace ownership in the existing Console and standalone CLI boundaries. Console preference helpers distinguish a missing file from a broken existing file and use an atomic sibling-file replacement; the CLI resolves one effective workspace before invoking the existing `_apply_workspace` boundary function.

**Tech Stack:** Rust 2021, Tauri 2, serde_json, Windows `ReplaceFileW`, Python 3.12, pytest.

## Global Constraints

- Preserve `MUSUBI_ROOT` as the packaged runtime location.
- `MUSUBI_WORKSPACE` remains the application filesystem and working-directory boundary.
- Missing preferences retain legacy discovery; malformed existing preferences fail closed.
- Explicit `--workspace` takes precedence over `MUSUBI_WORKSPACE`.
- Do not modify or stage the unrelated `vietnam-weather.html`.

---

### Task 1: Make Console preferences fail closed and atomic

**Files:**
- Modify: `gui/src-tauri/src/lib.rs:83-135`
- Test: `gui/src-tauri/src/lib.rs:2123-2170`

**Interfaces:**
- Consumes: `console_preferences_path() -> PathBuf`
- Produces: `load_console_preferences_from(path: &Path) -> Result<Option<ConsolePreferences>, String>`
- Produces: `save_console_preferences_to(path: &Path, preferences: &ConsolePreferences) -> Result<(), String>`
- Produces: `save_console_preferences_with_replacer(path, preferences, replacer) -> Result<(), String>` for deterministic replacement-failure testing

- [ ] **Step 1: Write failing preference-loading tests**

Add tests using a unique directory under `std::env::temp_dir()`:

```rust
#[test]
fn missing_console_preferences_are_unconfigured() {
    let root = temp_console_preferences_dir("missing");
    let path = root.join("console.json");
    assert!(load_console_preferences_from(&path).unwrap().is_none());
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn malformed_console_preferences_fail_closed() {
    let root = temp_console_preferences_dir("malformed");
    let path = root.join("console.json");
    std::fs::write(&path, "{broken").unwrap();
    let error = load_console_preferences_from(&path).unwrap_err();
    assert!(error.contains("parse"));
    std::fs::remove_dir_all(root).unwrap();
}
```

- [ ] **Step 2: Run the loading tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml missing_console_preferences_are_unconfigured
cargo test --manifest-path gui/src-tauri/Cargo.toml malformed_console_preferences_fail_closed
```

Expected: compilation fails because `load_console_preferences_from` does not exist.

- [ ] **Step 3: Implement typed preference loading**

Use `std::io::ErrorKind::NotFound` as the only `Ok(None)` case:

```rust
fn load_console_preferences_from(
    path: &Path,
) -> Result<Option<ConsolePreferences>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("read {}: {error}", path.display())),
    };
    serde_json::from_str(&text)
        .map(Some)
        .map_err(|error| format!("parse {}: {error}", path.display()))
}
```

Make `load_console_preferences()` delegate to this helper. In
`open_configured_db`, handle `Ok(Some(preferences))`, `Ok(None)`, and `Err`
separately; the `Err` branch must populate `workspace_error`.

- [ ] **Step 4: Run the loading tests and verify GREEN**

Run the two tests from Step 2. Expected: both pass.

- [ ] **Step 5: Write failing atomic-save tests**

Add one successful replacement test and one injected replacement-failure test:

```rust
#[test]
fn console_preferences_replace_existing_file_atomically() {
    let root = temp_console_preferences_dir("replace");
    let path = root.join("console.json");
    std::fs::write(&path, r#"{"workspace":"old"}"#).unwrap();
    let preferences = ConsolePreferences {
        workspace: "new".into(),
        llm_config: "model.json".into(),
    };
    save_console_preferences_to(&path, &preferences).unwrap();
    let loaded = load_console_preferences_from(&path).unwrap().unwrap();
    assert_eq!(loaded.workspace, "new");
    assert!(root.read_dir().unwrap().all(|entry| {
        entry.unwrap().file_name() == std::ffi::OsStr::new("console.json")
    }));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn failed_console_preference_replace_preserves_previous_file() {
    let root = temp_console_preferences_dir("replace-failure");
    let path = root.join("console.json");
    let before = r#"{"workspace":"old"}"#;
    std::fs::write(&path, before).unwrap();
    let preferences = ConsolePreferences {
        workspace: "new".into(),
        llm_config: String::new(),
    };
    let error = save_console_preferences_with_replacer(
        &path,
        &preferences,
        &|_, _| Err(std::io::Error::other("simulated replace failure")),
    )
    .unwrap_err();
    assert!(error.contains("simulated replace failure"));
    assert_eq!(std::fs::read_to_string(&path).unwrap(), before);
    assert_eq!(root.read_dir().unwrap().count(), 1);
    std::fs::remove_dir_all(root).unwrap();
}
```

- [ ] **Step 6: Run the save tests and verify RED**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml console_preferences_replace_existing_file_atomically
cargo test --manifest-path gui/src-tauri/Cargo.toml failed_console_preference_replace_preserves_previous_file
```

Expected: compilation fails because the path-based save helpers do not exist.

- [ ] **Step 7: Implement atomic preference replacement**

Follow the existing `musubi-data` atomic pipeline writer pattern:

- Generate a unique sibling temporary name using process id, epoch nanoseconds,
  and an `AtomicU64`.
- Open with `OpenOptions::create_new(true)`.
- Write and `sync_all()` before replacement.
- Remove the temporary file on any error.
- Use `std::fs::rename` on non-Windows.
- On Windows, use `ReplaceFileW` when the target exists and
  `std::fs::rename` when it does not.
- Keep `save_console_preferences()` as the fixed-path wrapper used by the Tauri
  action.

- [ ] **Step 8: Normalize the existing Windows canonical-path test**

Change the assertion to compare canonical values:

```rust
assert_eq!(
    canonical_workspace(root.to_str().unwrap()).unwrap(),
    root.canonicalize().unwrap()
);
```

- [ ] **Step 9: Run the complete Console Rust suite**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
```

Expected: 53 tests pass, 0 fail.

### Task 2: Apply environment-only CLI workspaces through the same boundary

**Files:**
- Modify: `musubi/agent/run.py:1777-1784`
- Test: `musubi/tests/test_agent_loop.py:3144-3190`

**Interfaces:**
- Consumes: `args.workspace: Path | None`
- Consumes: `MUSUBI_WORKSPACE`
- Produces: one `Path | None` passed to `_apply_workspace`

- [ ] **Step 1: Write failing environment-fallback tests**

Patch `_resolve_vendor` and `run_agent` so `main` runs without an external
model or MCP server. Create a temporary Musubi directory containing
`server.py`, start outside the selected directory, and assert:

```python
def test_main_applies_workspace_from_environment(tmp_path, monkeypatch, capsys):
    from agent import run as run_mod

    selected = tmp_path / "application"
    runtime = tmp_path / "runtime"
    selected.mkdir()
    runtime.mkdir()
    (runtime / "server.py").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSUBI_WORKSPACE", str(selected))
    monkeypatch.setattr(run_mod, "_resolve_vendor", lambda _: (object(), "test"))

    async def fake_run_agent(*args, **kwargs):
        return "done"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    assert run_mod.main(["task", "--musubi", str(runtime)]) == 0
    assert Path.cwd().resolve() == selected.resolve()
```

Add a sibling test with environment workspace A and `--workspace B`, asserting
that B wins.

- [ ] **Step 2: Run the new CLI tests and verify RED**

Run:

```powershell
python -m pytest musubi/tests/test_agent_loop.py -k "main_applies_workspace_from_environment or explicit_workspace_overrides_environment" -q
```

Expected: the environment-only test fails because the current directory stays
at `tmp_path`.

- [ ] **Step 3: Implement one effective workspace**

Immediately after resolving `musubi_dir`, select the CLI value first and then a
non-empty environment value:

```python
workspace = args.workspace
if workspace is None:
    env_workspace = os.environ.get("MUSUBI_WORKSPACE", "").strip()
    if env_workspace:
        workspace = Path(env_workspace)
if workspace is not None:
    rc = _apply_workspace(workspace)
    if rc:
        return rc
```

- [ ] **Step 4: Run the new and existing workspace tests**

Run:

```powershell
python -m pytest musubi/tests/test_agent_loop.py::test_main_applies_workspace_from_environment musubi/tests/test_agent_loop.py::test_explicit_workspace_overrides_environment musubi/tests/test_agent_loop.py::test_apply_workspace_points_tools_at_the_selected_folder musubi/tests/test_agent_loop.py::test_apply_workspace_rejects_missing_and_non_directories musubi/tests/test_fs_tools.py::test_selected_workspace_overrides_packaged_musubi_root musubi/tests/test_mechanical_gate.py::test_mechanical_root_follows_selected_workspace musubi/tests/test_sub_sessions.py::test_artifacts_verified_anchors_on_selected_workspace -q
```

Expected: 7 tests pass.

### Task 3: Remove hygiene error and verify the branch

**Files:**
- Modify: `docs/superpowers/plans/2026-07-28-console-workspace-picker.md:34`

**Interfaces:**
- Produces: a branch diff with no whitespace errors

- [ ] **Step 1: Remove the extra blank line at EOF**

Leave exactly one newline after the final numbered step.

- [ ] **Step 2: Run deterministic verification**

Run:

```powershell
cargo test --manifest-path gui/src-tauri/Cargo.toml
cargo test --manifest-path gui/src-tauri/musubi-data/Cargo.toml
Push-Location gui; node --test "src/**/*.test.mjs"; Pop-Location
python -m pytest musubi/tests/test_agent_loop.py::test_main_applies_workspace_from_environment musubi/tests/test_agent_loop.py::test_explicit_workspace_overrides_environment musubi/tests/test_agent_loop.py::test_apply_workspace_points_tools_at_the_selected_folder musubi/tests/test_agent_loop.py::test_apply_workspace_rejects_missing_and_non_directories musubi/tests/test_fs_tools.py::test_selected_workspace_overrides_packaged_musubi_root musubi/tests/test_mechanical_gate.py::test_mechanical_root_follows_selected_workspace musubi/tests/test_sub_sessions.py::test_artifacts_verified_anchors_on_selected_workspace -q
git diff --check origin/dev...HEAD
```

Expected:

- Console Rust suite: 0 failures.
- `musubi-data`: 67 tests pass.
- Frontend: 133 tests pass.
- Targeted Python: 7 tests pass.
- `git diff --check`: no output, exit 0.

- [ ] **Step 3: Inspect scope before handoff**

Run:

```powershell
git status --short
git diff --stat
git diff
```

Confirm only the approved preference, CLI, tests, plan hygiene, design, and
implementation-plan files changed; `vietnam-weather.html` remains untracked and
unstaged.
