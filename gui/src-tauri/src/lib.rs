//! musubi-tier: substrate
//!
//! Tauri desktop shell for the Musubi console.
//!
//! Bridges the webkit-free `musubi-data` core to the React UI:
//!   - `get_state`      -> snapshot of the console state
//!   - `action`         -> mutating actions (chat, profile, pipeline)
//!   - `state://update` -> emitted about once per second as audit.db grows
//!
//! Data source: the configured Musubi `audit.db`. When no database can be
//! resolved, the console opens an empty in-memory schema for first-run setup.

use std::collections::{hash_map::DefaultHasher, HashMap};
use std::hash::{Hash, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OpenFlags};
use tauri::{Emitter, Manager};

struct AppState {
    db: Mutex<Connection>,
    // `pipeline_runs` is state-store data, intentionally kept separate from
    // the append-only audit ledger. The GUI never writes to this connection.
    state_db: Option<Mutex<Connection>>,
    paused: AtomicBool,
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
    chat_agent: Arc<Mutex<ChatAgentRuntime>>,
    // Orchestrator session id (gui-orchestrator-*-<nonce>). Interior-mutable so
    // "New session" can re-mint it at runtime.
    chat_id: Mutex<String>,
    // Pipeline studio session id (gui-pipeline-*-<nonce>). Same single process
    // slot, but its own conversation history + run scope.
    pipeline_chat_id: Mutex<String>,
}

#[derive(Default)]
struct ChatAgentRuntime {
    running: bool,
    child: Option<Arc<Mutex<Child>>>,
    cancel_requested: bool,
    task: String,
    started_at: Option<i64>,
    stdout_tail: String,
    stderr_tail: String,
    // Which surface ('orchestrator' | 'pipeline') the active run belongs to, so
    // the driver reply is written to the matching chat_log surface.
    surface: String,
    pipeline_name: String,
    terminal_status: String,
}

enum TailStream {
    Stdout,
    Stderr,
}

const TAIL_CAP: usize = 64 * 1024;
const ARTIFACT_EXTENSIONS: &[&str] = &[
    "html", "htm", "md", "pdf", "png", "jpg", "jpeg", "svg", "json", "csv", "txt", "xlsx", "docx",
    "pptx",
];

fn epoch_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn chat_timestamp(epoch: i64) -> String {
    format!("epoch:{epoch}")
}

fn insert_chat(
    conn: &Connection,
    role: &str,
    tone: Option<&str>,
    text: &str,
    surface: &str,
) -> Result<(), String> {
    conn.execute(
        "INSERT INTO chat_log(ts,role,tone,text,surface) VALUES(?1,?2,?3,?4,?5)",
        rusqlite::params![chat_timestamp(epoch_secs()), role, tone, text, surface],
    )
    .map(|_| ())
    .map_err(|e| e.to_string())
}

/// Normalize a caller-supplied surface to one of the two known values,
/// defaulting to the Orchestrator.
fn surface_arg(raw: &str) -> &'static str {
    if raw == "pipeline" {
        "pipeline"
    } else {
        "orchestrator"
    }
}

fn terminal_status(cancelled: bool, exit_code: i32, output: &str) -> &'static str {
    if cancelled {
        return "aborted";
    }
    let output = output.to_ascii_lowercase();
    if output.contains("tokenbudgetexhaustederror")
        || output.contains("token budget halt")
        || output.contains("token budget exhausted")
    {
        return "budget_halted";
    }
    if exit_code == 0 {
        "success"
    } else {
        "failed"
    }
}

fn clear_driver_chat_log(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    surface: &str,
) -> Result<(), String> {
    if rt.running {
        return Err(
            "Cannot clear chat while the agent is running. Cancel or wait for it to finish.".into(),
        );
    }
    conn.execute("DELETE FROM chat_log WHERE surface = ?1", [surface])
        .map_err(|e| e.to_string())?;
    rt.stdout_tail.clear();
    rt.stderr_tail.clear();
    rt.task.clear();
    rt.started_at = None;
    rt.cancel_requested = false;
    rt.terminal_status.clear();
    Ok(())
}

fn workspace_root_from_musubi_config(path: &std::path::Path) -> Option<PathBuf> {
    let dir = path.parent()?;
    if dir.file_name().and_then(|s| s.to_str()) != Some(".musubi") {
        return None;
    }
    dir.parent().map(PathBuf::from)
}

fn env_path(env: &HashMap<String, String>, key: &str) -> Option<PathBuf> {
    env.get(key)
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
}

fn project_root_from_audit_db(resolved: &musubi_data::ResolvedAuditDb) -> Option<PathBuf> {
    let audit_file = &resolved.path;
    match resolved.source.as_str() {
        "workspace" => audit_file
            .parent()
            .and_then(|storage| storage.parent())
            .and_then(|package| {
                if package.file_name().and_then(|s| s.to_str()) == Some("musubi") {
                    package.parent().map(PathBuf::from)
                } else {
                    None
                }
            }),
        "package" => audit_file
            .parent()
            .and_then(|storage| storage.parent())
            .map(PathBuf::from),
        _ => None,
    }
}

fn climb_for_workspace_root(start: &Path) -> Option<PathBuf> {
    let mut dir = Some(start);
    while let Some(d) = dir {
        if d.join(".musubi").join("llm.json").is_file()
            || d.join("musubi").join("server.py").is_file()
            || d.join("musubi").join("storage").is_dir()
        {
            return Some(d.to_path_buf());
        }
        dir = d.parent();
    }
    None
}

fn resolve_project_root(
    env: &HashMap<String, String>,
    cwd: &Path,
    audit_db: Option<&musubi_data::ResolvedAuditDb>,
) -> PathBuf {
    if let Some(root) = env_path(env, "MUSUBI_ROOT") {
        return root;
    }
    if let Some(config) =
        env_path(env, "MUSUBI_LLM_CONFIG").and_then(|p| workspace_root_from_musubi_config(&p))
    {
        return config;
    }
    if let Some(root) = audit_db.and_then(project_root_from_audit_db) {
        return root;
    }
    climb_for_workspace_root(cwd).unwrap_or_else(|| cwd.to_path_buf())
}

fn scoped_chat_id(project_root: &Path, surface: &str, nonce: &str) -> String {
    let root = project_root
        .canonicalize()
        .unwrap_or_else(|_| project_root.to_path_buf())
        .to_string_lossy()
        .to_lowercase();
    let mut hasher = DefaultHasher::new();
    root.hash(&mut hasher);
    // The surface is encoded in the id prefix (gui-orchestrator-* / gui-pipeline-*)
    // so the UI can scope runs to a session without a separate id table. The
    // trailing nonce scopes history to a *session*: "New session" mints a fresh
    // nonce, so the agent's replay (conversation_messages, keyed by chat_id)
    // starts empty while old turns stay under the old id.
    format!("gui-{surface}-{:016x}-{nonce}", hasher.finish())
}

fn session_nonce_key(surface: &str) -> String {
    format!("session_nonce_{surface}")
}

/// A short, unique-enough nonce for a GUI session, derived from the current
/// nanosecond clock so two "New session" clicks do not collide.
fn mint_session_nonce() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut hasher = DefaultHasher::new();
    nanos.hash(&mut hasher);
    format!("{:012x}", hasher.finish() & 0xffff_ffff_ffff)
}

fn store_session_nonce(conn: &Connection, surface: &str, nonce: &str) {
    let _ = conn.execute(
        "INSERT INTO meta(key,value) VALUES(?1,?2) \
         ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rusqlite::params![session_nonce_key(surface), nonce],
    );
}

/// Load the persisted session nonce for `surface`, minting and storing one on
/// first use so restarting the app continues the same session (option a).
fn load_or_mint_session_nonce(conn: &Connection, surface: &str) -> String {
    let key = session_nonce_key(surface);
    if let Ok(nonce) = conn.query_row("SELECT value FROM meta WHERE key=?1", [key.as_str()], |r| {
        r.get::<_, String>(0)
    }) {
        return nonce;
    }
    let nonce = mint_session_nonce();
    store_session_nonce(conn, surface, &nonce);
    nonce
}

/// Start a fresh session on `surface`: mint a new nonce (so the agent's
/// chat_id-keyed replay history starts empty next run), persist it, swap the
/// live chat_id, and clear the display log. Old history is retained under the
/// old chat_id (append-only) for future browsing.
fn new_driver_session(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    chat_id_slot: &Mutex<String>,
    project_root: &Path,
    surface: &str,
) -> Result<(), String> {
    if rt.running {
        return Err(
            "Cannot start a new session while the agent is running. Cancel or wait for it to finish."
                .into(),
        );
    }
    let nonce = mint_session_nonce();
    store_session_nonce(conn, surface, &nonce);
    conn.execute("DELETE FROM chat_log WHERE surface = ?1", [surface])
        .map_err(|e| e.to_string())?;
    let new_id = scoped_chat_id(project_root, surface, &nonce);
    *chat_id_slot.lock().map_err(|e| e.to_string())? = new_id;
    rt.stdout_tail.clear();
    rt.stderr_tail.clear();
    rt.task.clear();
    rt.started_at = None;
    rt.cancel_requested = false;
    Ok(())
}

fn percent_encode(input: &str) -> String {
    let mut out = String::new();
    for b in input.as_bytes() {
        let c = *b as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
            out.push(c);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn is_artifact_file(path: &Path) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .map(|ext| {
            ARTIFACT_EXTENSIONS
                .iter()
                .any(|x| x.eq_ignore_ascii_case(ext))
        })
        .unwrap_or(false)
}

fn should_skip_dir(path: &Path) -> bool {
    matches!(
        path.file_name().and_then(|s| s.to_str()),
        Some(
            ".git" | ".venv" | "node_modules" | "target" | "dist" | ".pytest_cache" | "__pycache__"
        )
    )
}

fn file_modified_epoch(path: &Path) -> Option<i64> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    modified
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|d| d.as_secs() as i64)
}

fn collect_recent_artifacts(root: &Path, since_epoch: i64, limit: usize) -> Vec<PathBuf> {
    fn walk(root: &Path, dir: &Path, since_epoch: i64, depth: usize, out: &mut Vec<PathBuf>) {
        if depth > 5 || should_skip_dir(dir) {
            return;
        }
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(root, &path, since_epoch, depth + 1, out);
                continue;
            }
            if !path.is_file() || !is_artifact_file(&path) {
                continue;
            }
            let Some(modified) = file_modified_epoch(&path) else {
                continue;
            };
            if modified + 2 >= since_epoch && path.starts_with(root) {
                out.push(path);
            }
        }
    }

    let mut paths = Vec::new();
    walk(root, root, since_epoch, 0, &mut paths);
    paths.sort_by_key(|p| std::cmp::Reverse(file_modified_epoch(p).unwrap_or(0)));
    paths.truncate(limit);
    paths
}

fn artifact_name_candidates(answer: &str) -> Vec<String> {
    answer
        .split_whitespace()
        .map(|raw| {
            raw.trim_matches(|c: char| {
                matches!(
                    c,
                    '`' | '"'
                        | '\''
                        | '('
                        | ')'
                        | '['
                        | ']'
                        | '{'
                        | '}'
                        | '<'
                        | '>'
                        | ','
                        | ';'
                        | ':'
                        | '!'
                        | '?'
                )
            })
            .trim_end_matches('.')
            .to_string()
        })
        .filter(|token| {
            !token.is_empty()
                && Path::new(token)
                    .extension()
                    .and_then(|s| s.to_str())
                    .map(|ext| {
                        ARTIFACT_EXTENSIONS
                            .iter()
                            .any(|x| x.eq_ignore_ascii_case(ext))
                    })
                    .unwrap_or(false)
        })
        .collect()
}

fn collect_mentioned_artifacts(answer: &str, root: &Path, limit: usize) -> Vec<PathBuf> {
    let root_canon = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let mut paths = Vec::new();
    for token in artifact_name_candidates(answer) {
        let token_path = PathBuf::from(token.trim_start_matches("./"));
        let candidate = if token_path.is_absolute() {
            token_path
        } else {
            root.join(token_path)
        };
        let Ok(canonical) = candidate.canonicalize() else {
            continue;
        };
        if canonical.starts_with(&root_canon)
            && canonical.is_file()
            && is_artifact_file(&canonical)
            && !paths.iter().any(|p| p == &canonical)
        {
            paths.push(canonical);
        }
        if paths.len() >= limit {
            break;
        }
    }
    paths
}

fn artifact_label(path: &Path, root: &Path) -> String {
    let root_canon = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    path.strip_prefix(root)
        .or_else(|_| path.strip_prefix(&root_canon))
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn append_artifact_links(answer: &str, root: &Path, artifacts: &[PathBuf]) -> String {
    let mut linked: Vec<PathBuf> = Vec::new();
    for path in collect_mentioned_artifacts(answer, root, 8)
        .into_iter()
        .chain(artifacts.iter().cloned())
    {
        if !linked.iter().any(|p| p == &path) {
            linked.push(path);
        }
    }

    let mentioned_artifact = !artifact_name_candidates(answer).is_empty();
    if linked.is_empty() && !mentioned_artifact {
        return answer.to_string();
    }
    let mut out = answer.trim_end().to_string();
    out.push_str("\n\nArtifacts:");
    for path in &linked {
        let label = artifact_label(path, root);
        let encoded = percent_encode(&path.to_string_lossy());
        out.push_str(&format!("\n- [{label}](musubi-artifact:{encoded})"));
    }
    if linked.is_empty() {
        let encoded = percent_encode(&root.to_string_lossy());
        out.push_str(&format!(
            "\n- [Workspace folder](musubi-artifact:{encoded})"
        ));
    }
    out
}

fn process_log(stdout_tail: &str, stderr_tail: &str) -> String {
    [
        (!stderr_tail.trim().is_empty()).then(|| format!("stderr:\n{}", stderr_tail.trim())),
        (!stdout_tail.trim().is_empty()).then(|| format!("stdout:\n{}", stdout_tail.trim())),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join("\n\n")
}

fn append_process_log_link(summary: &str, log: &str) -> String {
    if log.trim().is_empty() {
        summary.to_string()
    } else {
        format!("{summary}\n\n[Open process log](musubi-log:last)")
    }
}

fn last_log_line(log: &str) -> String {
    let line = log
        .lines()
        .rev()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("");
    if line.chars().count() <= 260 {
        line.to_string()
    } else {
        format!("{}...", line.chars().take(260).collect::<String>())
    }
}

fn summarize_agent_failure(code: i32, detail: &str) -> String {
    if detail.contains("TokenBudgetExhaustedError") || detail.contains("token budget halt") {
        return [
            "Budget halted before the next model call.".to_string(),
            "Musubi stopped the run because the projected request would exceed the configured token budget. Open the full process log for the exact token counts.".to_string(),
        ]
        .join("\n\n");
    }
    if detail.is_empty() {
        format!("Agent exited with code {code}.")
    } else {
        format!(
            "Agent exited with code {code}.\n\n{}",
            last_log_line(detail)
        )
    }
}

fn shell_display_path(path: &Path) -> String {
    let mut display_path = path.to_string_lossy().to_string();
    if cfg!(windows) {
        if let Some(stripped) = display_path.strip_prefix("\\\\?\\") {
            display_path = stripped.to_string();
        }
    }
    display_path
}

fn workspace_path_key(path: &Path) -> String {
    let mut key = path.to_string_lossy().to_string();
    if cfg!(windows) {
        key = key.replace('/', "\\");
        if let Some(stripped) = key.strip_prefix("\\\\?\\") {
            key = stripped.to_string();
        }
        key = key.trim_end_matches('\\').to_ascii_lowercase();
    } else {
        key = key.trim_end_matches('/').to_string();
    }
    key
}

fn is_inside_workspace(path: &Path, root: &Path) -> bool {
    let path_key = workspace_path_key(path);
    let root_key = workspace_path_key(root);
    if path_key == root_key {
        return true;
    }
    let separator = if cfg!(windows) { "\\" } else { "/" };
    path_key.starts_with(&format!("{root_key}{separator}"))
}

fn open_command_for_path(path: &Path, is_file: bool) -> (String, Vec<String>) {
    let display_path = shell_display_path(path);
    if cfg!(windows) {
        let _ = is_file;
        (
            "cmd".into(),
            vec!["/C".into(), "start".into(), "".into(), display_path],
        )
    } else if cfg!(target_os = "macos") {
        if is_file {
            ("open".into(), vec!["-R".into(), display_path])
        } else {
            ("open".into(), vec![display_path])
        }
    } else {
        ("xdg-open".into(), vec![display_path])
    }
}

fn open_workspace_path(project_root: &Path, raw_path: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(raw_path);
    let canonical = path
        .canonicalize()
        .map_err(|e| format!("cannot open artifact: {e}"))?;
    let root = project_root
        .canonicalize()
        .map_err(|e| format!("cannot resolve project root: {e}"))?;
    if !is_inside_workspace(&canonical, &root) {
        return Err("refusing to open a path outside the project root".into());
    }
    let (program, args) = open_command_for_path(&canonical, canonical.is_file());
    let mut cmd = std::process::Command::new(program);
    cmd.args(args);
    cmd.spawn()
        .map(|_| canonical)
        .map_err(|e| format!("failed to open artifact: {e}"))
}

fn artifact_opened_message(path: &Path) -> String {
    format!(
        "Opened artifact in the system file browser:\n\n- `{}`",
        path.to_string_lossy()
    )
}

fn artifact_open_failed_message(raw_path: &str, error: &str) -> String {
    format!("Could not open artifact.\n\nPath: `{raw_path}`\n\n{error}")
}

fn append_driver_chat(app: &tauri::AppHandle, tone: Option<&str>, text: &str) {
    let state = app.state::<AppState>();
    let surface = state
        .chat_agent
        .lock()
        .map(|rt| {
            if rt.surface.is_empty() {
                "orchestrator".to_string()
            } else {
                rt.surface.clone()
            }
        })
        .unwrap_or_else(|_| "orchestrator".to_string());
    let Ok(conn) = state.db.lock() else {
        return;
    };
    let _ = insert_chat(&conn, "driver", tone, text, &surface);
}

fn pump_stream(
    stream: impl Read + Send + 'static,
    shared: Arc<Mutex<ChatAgentRuntime>>,
    which: TailStream,
) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = stream;
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let chunk = String::from_utf8_lossy(&buf[..n]).into_owned();
                    match which {
                        TailStream::Stdout => eprint!("{chunk}"),
                        TailStream::Stderr => eprint!("{chunk}"),
                    }
                    if let Ok(mut rt) = shared.lock() {
                        let tail = match which {
                            TailStream::Stdout => &mut rt.stdout_tail,
                            TailStream::Stderr => &mut rt.stderr_tail,
                        };
                        musubi_data::push_bounded_tail(tail, &chunk, TAIL_CAP);
                    }
                }
            }
        }
    })
}

fn start_chat_agent(
    app: tauri::AppHandle,
    state: &AppState,
    task_text: String,
    chat_id: &str,
    surface: &str,
    pipeline_name: Option<&str>,
) -> Result<(), String> {
    let started_at = epoch_secs();
    {
        let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
        if rt.running {
            return Err(
                "Agent is already running. Wait for it to finish before starting another run."
                    .into(),
            );
        }
        rt.running = true;
        rt.child = None;
        rt.cancel_requested = false;
        rt.task = task_text.clone();
        rt.started_at = Some(started_at);
        rt.stdout_tail.clear();
        rt.stderr_tail.clear();
        rt.surface = surface.to_string();
        rt.pipeline_name = pipeline_name.unwrap_or_default().to_string();
        rt.terminal_status.clear();
    }

    let mut env = musubi_data::current_env_map();
    let setup =
        musubi_data::detect_setup_status(&env, &state.project_root, state.audit_db.as_ref());
    let mut launch_root = state.project_root.clone();
    let llm_config_path =
        (!setup.llm_config_path.is_empty()).then(|| PathBuf::from(&setup.llm_config_path));
    if !setup.llm_config_path.is_empty() {
        env.entry("MUSUBI_LLM_CONFIG".into())
            .or_insert_with(|| setup.llm_config_path.clone());
        if let Some(root) =
            workspace_root_from_musubi_config(std::path::Path::new(&setup.llm_config_path))
        {
            launch_root = root;
        }
    }
    let default_profile = llm_config_path
        .as_deref()
        .and_then(musubi_data::read_llm_default_from_path)
        .unwrap_or_default();
    let profile = {
        let conn = state.db.lock().map_err(|e| e.to_string())?;
        musubi_data::read_active_profile_for_config(&conn, llm_config_path.as_deref())
    };
    let mcp_config = launch_root.join(".musubi").join("mcp.json");
    if mcp_config.is_file() {
        env.entry("MUSUBI_MCP_CONFIG".into())
            .or_insert_with(|| mcp_config.to_string_lossy().to_string());
    }
    let agent_path = setup
        .agent_cli
        .found
        .then(|| PathBuf::from(&setup.agent_cli.path));
    let spec = musubi_data::build_agent_launch_spec(
        &task_text,
        &profile,
        &default_profile,
        agent_path.as_deref(),
        &launch_root,
        &env,
        Some(chat_id),
        pipeline_name,
    )?;
    eprintln!(
        "[musubi] launching agent cwd={} args={:?}",
        spec.cwd.display(),
        spec.args
    );

    let spawned = std::process::Command::new(&spec.program)
        .args(&spec.args)
        .current_dir(&spec.cwd)
        .envs(spec.env.iter().cloned())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match spawned {
        Ok(c) => c,
        Err(e) => {
            if let Ok(mut rt) = state.chat_agent.lock() {
                rt.running = false;
                rt.child = None;
            }
            return Err(format!("Failed to launch {}: {e}", spec.program.display()));
        }
    };

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let child = Arc::new(Mutex::new(child));
    {
        let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
        rt.child = Some(child.clone());
    }

    let shared = state.chat_agent.clone();
    let stdout_pump = stdout.map(|out| pump_stream(out, shared.clone(), TailStream::Stdout));
    let stderr_pump = stderr.map(|err| pump_stream(err, shared.clone(), TailStream::Stderr));

    let artifact_root = launch_root.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(200));
        let polled = match child.lock() {
            Ok(mut c) => c.try_wait(),
            Err(_) => break,
        };
        match polled {
            Ok(None) => continue,
            Ok(Some(status)) => {
                if let Some(handle) = stdout_pump {
                    let _ = handle.join();
                }
                if let Some(handle) = stderr_pump {
                    let _ = handle.join();
                }
                let code = status.code().unwrap_or(-1);
                let (stdout_tail, stderr_tail, cancelled) = match shared.lock() {
                    Ok(mut rt) => {
                        let cancelled = rt.cancel_requested;
                        rt.running = false;
                        rt.child = None;
                        rt.cancel_requested = false;
                        (rt.stdout_tail.clone(), rt.stderr_tail.clone(), cancelled)
                    }
                    Err(_) => (String::new(), String::new(), false),
                };
                let terminal =
                    terminal_status(cancelled, code, &format!("{stderr_tail}\n{stdout_tail}"));
                if let Ok(mut rt) = shared.lock() {
                    rt.terminal_status = terminal.to_string();
                }
                let log = process_log(&stdout_tail, &stderr_tail);
                if cancelled {
                    append_driver_chat(
                        &app,
                        Some("deny"),
                        &append_process_log_link("Agent cancelled by user.", &log),
                    );
                    break;
                }
                if code == 0 {
                    let answer = stdout_tail.trim();
                    let artifacts = collect_recent_artifacts(&artifact_root, started_at, 8);
                    if answer.is_empty() {
                        let text = append_artifact_links(
                            "Agent finished without a text answer. Check the audit panels for tool activity.",
                            &artifact_root,
                            &artifacts,
                        );
                        append_driver_chat(&app, None, &append_process_log_link(&text, &log));
                    } else {
                        let text = append_artifact_links(answer, &artifact_root, &artifacts);
                        append_driver_chat(&app, None, &append_process_log_link(&text, &log));
                    }
                } else {
                    let detail = if !stderr_tail.trim().is_empty() {
                        stderr_tail.trim()
                    } else {
                        stdout_tail.trim()
                    };
                    let text = summarize_agent_failure(code, detail);
                    append_driver_chat(&app, Some("deny"), &append_process_log_link(&text, &log));
                }
                break;
            }
            Err(e) => {
                if let Ok(mut rt) = shared.lock() {
                    rt.running = false;
                    rt.child = None;
                    rt.terminal_status = "failed".into();
                }
                append_driver_chat(&app, Some("deny"), &format!("Agent wait failed: {e}"));
                break;
            }
        }
    });

    Ok(())
}

fn cancel_chat_agent(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    let child = {
        let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
        if !rt.running {
            return Ok(());
        }
        rt.cancel_requested = true;
        rt.child.clone()
    };

    let Some(child) = child else {
        let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
        rt.running = false;
        rt.child = None;
        rt.cancel_requested = false;
        append_driver_chat(app, Some("deny"), "Agent cancelled by user.");
        return Ok(());
    };

    let pid = child.lock().map_err(|e| e.to_string())?.id();

    #[cfg(windows)]
    {
        let pid_s = pid.to_string();
        let status = std::process::Command::new("taskkill")
            .args(["/PID", pid_s.as_str(), "/T", "/F"])
            .status();
        if status.as_ref().map(|s| s.success()).unwrap_or(false) {
            return Ok(());
        }
    }

    let mut guard = child.lock().map_err(|e| e.to_string())?;
    guard
        .kill()
        .map_err(|e| format!("failed to cancel agent: {e}"))?;
    Ok(())
}

fn open_db() -> Connection {
    match std::env::var("MUSUBI_DB") {
        Ok(path) if !path.is_empty() => {
            let conn = Connection::open(&path).expect("open MUSUBI_DB");
            let _ = musubi_data::init_schema(&conn);
            eprintln!("[musubi] reading audit.db at {path}");
            conn
        }
        _ => {
            let conn = Connection::open_in_memory().expect("open in-memory db");
            musubi_data::init_schema(&conn).expect("init empty schema");
            eprintln!("[musubi] MUSUBI_DB not set - using empty in-memory state");
            conn
        }
    }
}

struct OpenedDb {
    conn: Connection,
    state_db: Option<Connection>,
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
}

fn open_configured_db() -> OpenedDb {
    let env = musubi_data::current_env_map();
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if let Some(resolved) = musubi_data::resolve_audit_db_path(&env, &cwd) {
        let project_root = resolve_project_root(&env, &cwd, Some(&resolved));
        let conn = Connection::open(&resolved.path).expect("open Musubi audit db");
        let _ = musubi_data::init_schema(&conn);
        let state_db = open_state_db(&resolved);
        eprintln!(
            "[musubi] reading audit.db at {} ({}) project_root={}",
            resolved.path.display(),
            resolved.source,
            project_root.display()
        );
        return OpenedDb {
            conn,
            state_db,
            project_root,
            audit_db: Some(resolved),
        };
    }

    let conn = open_db();
    eprintln!("[musubi] no audit.db source found; using empty in-memory state");
    OpenedDb {
        conn,
        state_db: None,
        project_root: resolve_project_root(&env, &cwd, None),
        audit_db: None,
    }
}

fn open_state_db(audit_db: &musubi_data::ResolvedAuditDb) -> Option<Connection> {
    let state_db = musubi_data::resolve_state_db_path(audit_db)?;
    Connection::open_with_flags(state_db.path, OpenFlags::SQLITE_OPEN_READ_ONLY).ok()
}

fn snapshot(state: &AppState) -> Result<musubi_data::State, String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    let state_conn = state
        .state_db
        .as_ref()
        .map(|db| db.lock().map_err(|e| e.to_string()))
        .transpose()?;
    let mut st = musubi_data::load_state_with_pipeline_runs(&conn, state_conn.as_deref())
        .map_err(|e| e.to_string())?;
    st.orchestrator_chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
    st.pipeline_chat_id = state
        .pipeline_chat_id
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    st.pipeline_catalog = musubi_data::read_studio_pipeline_catalog(&state.project_root);
    st.paused = state.paused.load(Ordering::Relaxed);
    st.runtime_source = state
        .audit_db
        .as_ref()
        .map(|r| r.source.clone())
        .unwrap_or_else(|| "none".into());
    st.setup_status = musubi_data::detect_setup_status(
        &musubi_data::current_env_map(),
        &state.project_root,
        state.audit_db.as_ref(),
    );
    let llm_config_path = (!st.setup_status.llm_config_path.is_empty())
        .then(|| PathBuf::from(&st.setup_status.llm_config_path));
    st.active_profile =
        musubi_data::read_active_profile_for_config(&conn, llm_config_path.as_deref());
    if let Some(path) = llm_config_path {
        st.profiles = musubi_data::read_llm_profiles_from_path(path);
    }
    if let Ok(rt) = state.chat_agent.lock() {
        st.driver_status = musubi_data::DriverStatus {
            running: rt.running,
            surface: surface_arg(&rt.surface).to_string(),
            pipeline_name: rt.pipeline_name.clone(),
            terminal_status: rt.terminal_status.clone(),
            task: rt.task.clone(),
            started_at: rt.started_at,
            stdout_tail: rt.stdout_tail.clone(),
            stderr_tail: rt.stderr_tail.clone(),
        };
    }
    Ok(st)
}

#[tauri::command]
fn get_state(state: tauri::State<AppState>) -> Result<musubi_data::State, String> {
    snapshot(&state)
}

#[tauri::command]
fn action(
    kind: String,
    args: Vec<serde_json::Value>,
    app: tauri::AppHandle,
    state: tauri::State<AppState>,
) -> Result<(), String> {
    let str_arg = |i: usize| {
        args.get(i)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    match kind.as_str() {
        "send_chat" => {
            let text = str_arg(0);
            if text.trim().is_empty() {
                return Ok(());
            }
            {
                let conn = state.db.lock().map_err(|e| e.to_string())?;
                insert_chat(&conn, "you", None, &text, "orchestrator")?;
            }
            let chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
            if let Err(e) =
                start_chat_agent(app, state.inner(), text, &chat_id, "orchestrator", None)
            {
                let conn = state.db.lock().map_err(|err| err.to_string())?;
                insert_chat(&conn, "driver", Some("deny"), &e, "orchestrator")?;
            }
        }
        // Pipeline studio session: same single process slot, its own chat_id +
        // conversation history + run scope.
        "send_pipeline_task" => {
            let catalog = musubi_data::read_studio_pipeline_catalog(&state.project_root);
            let requested_text = str_arg(0);
            let requested_pipeline = str_arg(1);
            let (text, pipeline_name) =
                match prepare_pipeline_launch(&requested_text, &requested_pipeline, &catalog) {
                    Ok(prepared) => prepared,
                    Err(error) => {
                        let conn = state.db.lock().map_err(|e| e.to_string())?;
                        insert_chat(&conn, "driver", Some("deny"), &error, "pipeline")?;
                        return Ok(());
                    }
                };
            {
                let conn = state.db.lock().map_err(|e| e.to_string())?;
                insert_chat(&conn, "you", None, &text, "pipeline")?;
            }
            let chat_id = state
                .pipeline_chat_id
                .lock()
                .map_err(|e| e.to_string())?
                .clone();
            if let Err(e) = start_chat_agent(
                app,
                state.inner(),
                text,
                &chat_id,
                "pipeline",
                Some(&pipeline_name),
            ) {
                let conn = state.db.lock().map_err(|err| err.to_string())?;
                insert_chat(&conn, "driver", Some("deny"), &e, "pipeline")?;
            }
        }
        "pipeline_hint" => {
            let text = str_arg(0);
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            if !text.trim().is_empty() {
                insert_chat(&conn, "you", None, &text, "orchestrator")?;
            }
            insert_chat(
                &conn,
                "driver",
                None,
                "Choose a pipeline preset in Pipeline studio before running. A bare `pipeline` command does not start an agent or consume model tokens.",
                "orchestrator",
            )?;
        }
        "select_profile" => {
            let name = str_arg(0);
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('active_profile',?1) \
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [&name],
            )
            .map_err(|e| e.to_string())?;
        }
        "toggle_pause" => {
            let cur = state.paused.load(Ordering::Relaxed);
            state.paused.store(!cur, Ordering::Relaxed);
        }
        "cancel_agent" => {
            cancel_chat_agent(&app, state.inner())?;
        }
        "clear_driver_chat" => {
            let surface = surface_arg(&str_arg(0));
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            clear_driver_chat_log(&conn, &mut rt, surface)?;
        }
        // Fresh session: re-mint the surface's chat_id so the agent's replay
        // history starts empty, and clear the display. Old turns stay under the
        // previous chat_id.
        "new_session" => {
            let surface = surface_arg(&str_arg(0));
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            let slot = if surface == "pipeline" {
                &state.pipeline_chat_id
            } else {
                &state.chat_id
            };
            new_driver_session(&conn, &mut rt, slot, &state.project_root, surface)?;
        }
        "open_artifact" => {
            let raw_path = str_arg(0);
            let surface = surface_arg(&str_arg(1));
            match open_workspace_path(&state.project_root, &raw_path) {
                Ok(opened) => {
                    let conn = state.db.lock().map_err(|e| e.to_string())?;
                    insert_chat(
                        &conn,
                        "driver",
                        None,
                        &artifact_opened_message(&opened),
                        surface,
                    )?;
                }
                Err(e) => {
                    let conn = state.db.lock().map_err(|err| err.to_string())?;
                    insert_chat(
                        &conn,
                        "driver",
                        Some("deny"),
                        &artifact_open_failed_message(&raw_path, &e),
                        surface,
                    )?;
                }
            }
        }
        // Pipeline Studio launches registered recipes through
        // `send_pipeline_task`; edited client-only compositions remain drafts
        // and never reach this backend.
        other => eprintln!("[musubi] unknown action: {other}"),
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let opened = open_configured_db();
    // Existing audit DBs predate chat_log.surface; add it if missing so the
    // per-surface inserts/reads work. Errors (incl. "duplicate column") ignored.
    {
        let _ = opened.conn.execute(
            "ALTER TABLE chat_log ADD COLUMN surface TEXT NOT NULL DEFAULT 'orchestrator'",
            [],
        );
    }
    // Continue the persisted session for each surface (option a: restart resumes
    // the current session); mint on first use.
    let chat_nonce = load_or_mint_session_nonce(&opened.conn, "orchestrator");
    let pipe_nonce = load_or_mint_session_nonce(&opened.conn, "pipeline");
    let chat_id = scoped_chat_id(&opened.project_root, "orchestrator", &chat_nonce);
    let pipeline_chat_id = scoped_chat_id(&opened.project_root, "pipeline", &pipe_nonce);
    tauri::Builder::default()
        .manage(AppState {
            db: Mutex::new(opened.conn),
            state_db: opened.state_db.map(Mutex::new),
            paused: AtomicBool::new(false),
            project_root: opened.project_root,
            audit_db: opened.audit_db,
            chat_agent: Arc::new(Mutex::new(ChatAgentRuntime::default())),
            chat_id: Mutex::new(chat_id),
            pipeline_chat_id: Mutex::new(pipeline_chat_id),
        })
        .invoke_handler(tauri::generate_handler![get_state, action])
        .setup(|app| {
            // Poll the audit.db and push live snapshots to the UI.
            let handle = app.handle().clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(Duration::from_millis(1100));
                let state = handle.state::<AppState>();
                if let Ok(st) = snapshot(&state) {
                    let _ = handle.emit("state://update", st);
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Musubi console");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clear_driver_chat_deletes_chat_and_idle_runtime_tails() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello", "orchestrator").unwrap();
        insert_chat(&conn, "driver", None, "hi", "orchestrator").unwrap();
        let mut rt = ChatAgentRuntime {
            stdout_tail: "stdout text".into(),
            stderr_tail: "stderr text".into(),
            task: "old task".into(),
            started_at: Some(123),
            ..ChatAgentRuntime::default()
        };

        clear_driver_chat_log(&conn, &mut rt, "orchestrator").unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM chat_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
        assert_eq!(rt.stdout_tail, "");
        assert_eq!(rt.stderr_tail, "");
        assert_eq!(rt.task, "");
        assert_eq!(rt.started_at, None);
    }

    #[test]
    fn new_session_rerolls_chat_id_and_clears_display() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello", "orchestrator").unwrap();
        let root = Path::new("/tmp/musubi-new-session-test");
        let old_nonce = load_or_mint_session_nonce(&conn, "orchestrator");
        let old_id = scoped_chat_id(root, "orchestrator", &old_nonce);
        let slot = Mutex::new(old_id.clone());
        let mut rt = ChatAgentRuntime {
            stdout_tail: "x".into(),
            ..ChatAgentRuntime::default()
        };

        new_driver_session(&conn, &mut rt, &slot, root, "orchestrator").unwrap();

        // The live chat_id changed, so the agent replays no prior history.
        let new_id = slot.lock().unwrap().clone();
        assert_ne!(new_id, old_id);
        assert!(new_id.starts_with("gui-orchestrator-"));
        // Display log for this surface is cleared and the tail reset.
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM chat_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 0);
        assert_eq!(rt.stdout_tail, "");
        // The new nonce is persisted, so a restart continues this new session.
        let persisted = load_or_mint_session_nonce(&conn, "orchestrator");
        assert_eq!(scoped_chat_id(root, "orchestrator", &persisted), new_id);
    }

    #[test]
    fn session_nonce_is_stable_across_reloads() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let first = load_or_mint_session_nonce(&conn, "orchestrator");
        let second = load_or_mint_session_nonce(&conn, "orchestrator");
        assert_eq!(first, second, "restart must continue the same session");
        // Surfaces are independent sessions.
        let pipe = load_or_mint_session_nonce(&conn, "pipeline");
        assert_ne!(first, pipe);
    }

    #[test]
    fn new_session_refuses_while_agent_runs() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello", "orchestrator").unwrap();
        let slot = Mutex::new("gui-orchestrator-abc-1".to_string());
        let mut rt = ChatAgentRuntime {
            running: true,
            ..ChatAgentRuntime::default()
        };

        let err = new_driver_session(&conn, &mut rt, &slot, Path::new("/tmp/x"), "orchestrator")
            .unwrap_err();

        assert!(err.contains("running"));
        assert_eq!(slot.lock().unwrap().clone(), "gui-orchestrator-abc-1");
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM chat_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn clear_driver_chat_refuses_while_agent_runs() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello", "orchestrator").unwrap();
        let mut rt = ChatAgentRuntime {
            running: true,
            ..ChatAgentRuntime::default()
        };

        let err = clear_driver_chat_log(&conn, &mut rt, "orchestrator").unwrap_err();

        assert!(err.contains("running"));
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM chat_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn artifact_links_include_existing_mentioned_file() {
        let root = std::env::temp_dir().join(format!("musubi-artifact-test-{}", epoch_secs()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("weather-dashboard.html");
        std::fs::write(&file, "<html></html>").unwrap();

        let text =
            append_artifact_links("Open `weather-dashboard.html` in your browser.", &root, &[]);

        assert!(text.contains("[weather-dashboard.html](musubi-artifact:"));
        let _ = std::fs::remove_file(file);
        let _ = std::fs::remove_dir(root);
    }

    #[test]
    fn artifact_open_command_reveals_files_on_windows() {
        let file = Path::new(r"C:\Workspace\Projects\Musubi\nyc-weather-dashboard.html");
        let (program, args) = open_command_for_path(file, true);

        if cfg!(windows) {
            assert_eq!(program, "cmd");
            assert_eq!(
                args,
                vec![
                    "/C",
                    "start",
                    "",
                    r"C:\Workspace\Projects\Musubi\nyc-weather-dashboard.html"
                ]
            );
        } else {
            assert!(!program.is_empty());
            assert!(!args.is_empty());
        }
    }

    #[test]
    fn project_root_uses_workspace_audit_parent_not_gui_cwd() {
        let env = HashMap::new();
        let cwd = Path::new(r"C:\Workspace\Projects\Musubi\gui");
        let audit = musubi_data::ResolvedAuditDb {
            path: PathBuf::from(r"C:\Workspace\Projects\Musubi\musubi\storage\audit.db"),
            source: "workspace".into(),
        };

        assert_eq!(
            resolve_project_root(&env, cwd, Some(&audit)),
            PathBuf::from(r"C:\Workspace\Projects\Musubi")
        );
    }

    #[test]
    fn state_connection_uses_read_only_sibling_database() {
        let root = std::env::temp_dir().join(format!("musubi-state-db-{}", epoch_secs()));
        let storage = root.join("storage");
        std::fs::create_dir_all(&storage).unwrap();
        let state_path = storage.join("musubi.db");
        Connection::open(&state_path).unwrap();
        let audit = musubi_data::ResolvedAuditDb {
            path: storage.join("audit.db"),
            source: "workspace".into(),
        };

        let state = open_state_db(&audit).expect("state connection");
        assert!(state
            .execute_batch("CREATE TABLE must_fail (id INTEGER)")
            .is_err());

        let _ = std::fs::remove_file(state_path);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn terminal_status_marks_budget_halts_without_claiming_liveness() {
        assert_eq!(
            terminal_status(false, 1, "TokenBudgetExhaustedError"),
            "budget_halted"
        );
        assert_eq!(terminal_status(false, 0, ""), "success");
        assert_eq!(terminal_status(true, 1, ""), "aborted");
        assert_eq!(terminal_status(false, 1, "other failure"), "failed");
    }

    #[test]
    fn process_log_link_describes_the_retained_log_scope() {
        let message = append_process_log_link("failed", "stderr: detail");
        assert!(message.contains("[Open process log](musubi-log:last)"));
        assert!(!message.contains("Open full process log"));
    }

    #[test]
    fn artifact_open_command_strips_windows_verbatim_prefix() {
        let file = Path::new(r"\\?\C:\Workspace\Projects\Musubi\america-facts-dashboard.html");
        let (program, args) = open_command_for_path(file, true);

        if cfg!(windows) {
            assert_eq!(program, "cmd");
            assert_eq!(
                args,
                vec![
                    "/C",
                    "start",
                    "",
                    r"C:\Workspace\Projects\Musubi\america-facts-dashboard.html"
                ]
            );
        } else {
            assert!(!program.is_empty());
            assert!(!args.is_empty());
        }
    }

    #[test]
    fn artifact_open_command_opens_folders_on_windows() {
        let folder = Path::new(r"C:\Workspace\Projects\Musubi");
        let (program, args) = open_command_for_path(folder, false);

        if cfg!(windows) {
            assert_eq!(program, "cmd");
            assert_eq!(
                args,
                vec!["/C", "start", "", r"C:\Workspace\Projects\Musubi"]
            );
        } else {
            assert!(!program.is_empty());
            assert!(!args.is_empty());
        }
    }

    #[test]
    fn workspace_boundary_allows_windows_verbatim_child_path() {
        let root = Path::new(r"C:\Workspace\Projects\Musubi");
        let file = Path::new(r"\\?\C:\Workspace\Projects\Musubi\america-facts-dashboard.html");

        assert!(is_inside_workspace(file, root));
    }

    #[test]
    fn workspace_boundary_rejects_windows_sibling_prefix_path() {
        let root = Path::new(r"C:\Workspace\Projects\Musubi");
        let file =
            Path::new(r"\\?\C:\Workspace\Projects\Musubi-other\america-facts-dashboard.html");

        assert!(!is_inside_workspace(file, root));
    }

    #[test]
    fn artifact_open_messages_are_user_visible() {
        let opened = artifact_opened_message(Path::new(r"C:\Workspace\Projects\Musubi\a.html"));
        assert!(opened.contains("Opened artifact"));
        assert!(opened.contains("a.html"));

        let failed = artifact_open_failed_message("missing.html", "cannot open artifact");
        assert!(failed.contains("Could not open artifact"));
        assert!(failed.contains("missing.html"));
        assert!(failed.contains("cannot open artifact"));
    }

    #[test]
    fn pipeline_launch_validates_registered_runnable_recipe() {
        let catalog = vec![musubi_data::PipelineCatalogEntry {
            name: "feature-dev".into(),
            description: "Ship a feature".into(),
            stages: vec!["planner".into(), "coder".into()],
            runnable: true,
            blocked_reason: String::new(),
        }];

        assert_eq!(
            prepare_pipeline_launch(" ship it ", "feature-dev", &catalog).unwrap(),
            ("ship it".to_string(), "feature-dev".to_string())
        );
        assert!(prepare_pipeline_launch("", "feature-dev", &catalog)
            .unwrap_err()
            .contains("empty"));
        assert!(prepare_pipeline_launch("ship", "../feature-dev", &catalog)
            .unwrap_err()
            .contains("invalid"));
        assert!(prepare_pipeline_launch("ship", "missing", &catalog)
            .unwrap_err()
            .contains("not registered"));
    }
}

fn prepare_pipeline_launch(
    brief: &str,
    pipeline_name: &str,
    catalog: &[musubi_data::PipelineCatalogEntry],
) -> Result<(String, String), String> {
    let brief = brief.trim();
    if brief.is_empty() {
        return Err("Pipeline brief is empty — describe the task to run.".into());
    }
    let pipeline_name = pipeline_name.trim();
    if !musubi_data::valid_pipeline_name(pipeline_name) {
        return Err(format!("invalid pipeline name: {pipeline_name:?}"));
    }
    let Some(entry) = catalog.iter().find(|entry| entry.name == pipeline_name) else {
        return Err(format!(
            "Pipeline {pipeline_name:?} is not registered for Studio."
        ));
    };
    if !entry.runnable || entry.stages.len() < 2 {
        return Err(if entry.blocked_reason.is_empty() {
            format!("Pipeline {pipeline_name:?} is not runnable in Studio.")
        } else {
            entry.blocked_reason.clone()
        });
    }
    Ok((brief.to_string(), pipeline_name.to_string()))
}
