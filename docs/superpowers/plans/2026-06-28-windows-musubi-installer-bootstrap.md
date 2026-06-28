# Windows Musubi Installer Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows desktop installer path behave and read like a Musubi installer bootstrap instead of a console-only artifact.

**Architecture:** Keep the Python core package and Tauri GUI as separate package technologies, but align their setup contract. The setup wizard detects the `musubi` and `agent` CLIs and gives PATH repair guidance; the GUI backend surfaces whether it is connected to a real `MUSUBI_DB`; docs and CI artifact copy describe the Windows artifact as a Musubi installer bootstrap.

**Tech Stack:** Python 3.11+, pytest, React/Vite/Tauri, Rust/Tauri IPC, GitHub Actions.

## Global Constraints

- All code, tests, docs, workflow text, and artifact copy must be English.
- macOS and Linux GUI installers remain out of scope.
- No offline embedded Python runtime in this slice.
- No automatic elevation or system-wide PATH mutation.
- No substrate-side LLM calls.
- `musubi setup` remains the first-time setup entry point.

---

### Task 1: Setup Wizard Core CLI Bootstrap Checks

**Files:**
- Modify: `musubi/setup_wizard.py`
- Modify: `musubi/tests/test_setup_wizard.py`

**Interfaces:**
- Consumes: `shutil.which`, `sys.executable`, and Python's user scripts path.
- Produces: `check_core_cli() -> Check` and `python_user_scripts_dir() -> Path | None`.

- [x] **Step 1: Write failing tests**

Add tests that pin both the success and failure paths:

```python
def test_check_core_cli_reports_available_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/bin/{name}" if name in ("musubi", "agent") else None)

    check = sw.check_core_cli()

    assert check.ok is True
    assert check.name == "musubi + agent CLIs"
    assert check.hint == ""


def test_check_core_cli_reports_missing_scripts_with_user_path_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sw.shutil, "which", lambda _name: None)
    monkeypatch.setattr(sw, "python_user_scripts_dir", lambda: Path(r"C:\Users\admin\AppData\Python\Scripts"))

    check = sw.check_core_cli()

    assert check.ok is False
    assert "missing: musubi, agent" in check.hint
    assert "python -m pip install --user musubi" in check.hint
    assert "setx PATH" in check.hint
```

- [x] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest musubi\tests\test_setup_wizard.py::test_check_core_cli_reports_available_scripts musubi\tests\test_setup_wizard.py::test_check_core_cli_reports_missing_scripts_with_user_path_hint -q -p no:cacheprovider
```

Expected: failures because `check_core_cli` does not exist.

- [x] **Step 3: Implement the helper**

Add the helper near the doctor section:

```python
def python_user_scripts_dir() -> Path | None:
    try:
        import site

        return Path(site.getusersitepackages()).parent / "Scripts"
    except Exception:
        return None


def check_core_cli() -> Check:
    missing = [name for name in ("musubi", "agent") if shutil.which(name) is None]
    if not missing:
        return Check("musubi + agent CLIs", True)
    hint = (
        f"missing: {', '.join(missing)}; install the Python core with "
        "python -m pip install --user musubi"
    )
    scripts = python_user_scripts_dir()
    if scripts:
        hint += f"; if scripts are already installed, add them to PATH: setx PATH \"%PATH%;{scripts}\""
    return Check("musubi + agent CLIs", False, hint)
```

Add `check_core_cli()` to `run_doctor()` after the Python version check.

- [x] **Step 4: Run setup wizard tests**

Run:

```powershell
python -m pytest musubi\tests\test_setup_wizard.py -q -p no:cacheprovider
```

Expected: all setup wizard tests pass.

### Task 2: GUI Backend Runtime Source Status

**Files:**
- Modify: `gui/src-tauri/musubi-data/src/lib.rs`
- Modify: `gui/src-tauri/src/lib.rs`
- Modify: `gui/src/model/viewModel.js`
- Modify: `gui/src/components/TrustStrip.jsx`

**Interfaces:**
- Consumes: `MUSUBI_DB` and `MUSUBI_LLM_CONFIG`.
- Produces: `State.runtime_source: String` serialized as `runtimeSource`.

- [x] **Step 1: Write failing Rust data test**

Add a test in `gui/src-tauri/musubi-data/src/lib.rs`:

```rust
#[test]
fn default_runtime_source_is_demo_until_backend_overrides_it() {
    let st = load_state(&demo()).unwrap();
    assert_eq!(st.runtime_source, "demo");
}
```

- [x] **Step 2: Implement state field**

Add to `State`:

```rust
pub runtime_source: String,
```

Initialize it in `load_state`:

```rust
runtime_source: "demo".into(),
```

In `gui/src-tauri/src/lib.rs`, set it after loading:

```rust
st.runtime_source = if std::env::var("MUSUBI_DB").ok().filter(|s| !s.is_empty()).is_some() {
    "musubi-db".into()
} else {
    "demo".into()
};
```

- [x] **Step 3: Surface status in the trust strip**

In `gui/src/model/viewModel.js`, add:

```js
runtimeSourceLabel: s.runtimeSource === 'musubi-db' ? 'real audit.db' : 'demo data',
```

In `gui/src/components/TrustStrip.jsx`, append that label to the trust pills.

- [x] **Step 4: Run frontend build**

Run:

```powershell
npm run build
```

Expected: build completes.

### Task 3: Docs, Workflow Copy, and Roadmap

**Files:**
- Modify: `.github/workflows/desktop.yml`
- Modify: `README.md`
- Modify: `docs/guide.md`
- Modify: `docs/roadmap.md`
- Modify: `gui/README.md`

**Interfaces:**
- Consumes: implemented setup wizard and runtime status behavior.
- Produces: English docs that describe the Windows artifact as the Musubi installer bootstrap.

- [x] **Step 1: Update workflow artifact and release copy**

Change the workflow comments, artifact name, release name, and release body from console-only language to Windows Musubi installer bootstrap language. Keep the job Windows-only and keep bundle targets as `msi,nsis`.

- [x] **Step 2: Update user docs**

Update README, guide, and GUI README so the primary path is:

```text
Install the Windows Musubi installer artifact, then run musubi setup if the Python core is missing or needs profile configuration.
```

Keep local GUI development as optional and Windows-only.

- [x] **Step 3: Update roadmap**

In the operator console section, change "standalone Windows desktop app" to "Windows installer bootstrap for the Musubi core plus console GUI". Track bundled/offline runtime and signing as follow-up work.

- [x] **Step 4: Run final checks**

Run:

```powershell
python -m pytest musubi\tests\test_setup_wizard.py -q -p no:cacheprovider
npm run build
git diff --check
```

Expected: pytest passes, frontend build passes, and diff check has no errors.
