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
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc, Mutex,
};
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::Deserialize;
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
    // Optional read-only history focus. Never owns a running process or future
    // messages; it only chooses which orchestrator chat snapshot is displayed.
    viewed_orchestrator_chat_id: Mutex<Option<String>>,
    // Set when a persisted workspace could not be honoured at startup. While
    // set the Console is outside the operator's chosen boundary, so agent
    // launches are refused rather than silently retargeted at the runtime.
    workspace_error: Option<String>,
}

#[derive(Default)]
struct ChatAgentRuntime {
    running: bool,
    request_id: String,
    child: Option<Arc<Mutex<Child>>>,
    cancel_requested: bool,
    // Exact conversation session that owns this process and its retained log.
    chat_id: String,
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

#[derive(Clone, Copy)]
enum TailStream {
    Stdout,
    Stderr,
}

const TAIL_CAP: usize = 64 * 1024;
const RUNTIME_LOG_PREFIX: &str = "\u{1e}MUSUBI_LOG ";
static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);
const ARTIFACT_EXTENSIONS: &[&str] = &[
    "html", "htm", "md", "pdf", "png", "jpg", "jpeg", "svg", "json", "csv", "txt", "xlsx", "docx",
    "pptx",
];

fn canonical_workspace(raw: &str) -> Result<PathBuf, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("Choose a workspace folder first.".into());
    }
    let path = PathBuf::from(raw)
        .canonicalize()
        .map_err(|e| format!("Workspace folder is not accessible: {e}"))?;
    if !path.is_dir() {
        return Err("The selected workspace is not a directory.".into());
    }
    Ok(path)
}

#[tauri::command]
fn choose_workspace() -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut command = std::process::Command::new("powershell.exe");
        command.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = 'Choose the application workspace for Musubi'; if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }",
        ]);
        command
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = std::process::Command::new("osascript");
        command.args(["-e", "POSIX path of (choose folder with prompt \"Choose the application workspace for Musubi\")"]);
        command
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = std::process::Command::new("zenity");
        command.args([
            "--file-selection",
            "--directory",
            "--title=Choose the application workspace for Musubi",
        ]);
        command
    };
    let output = command
        .output()
        .map_err(|e| format!("open folder picker: {e}"))?;
    if !output.status.success() {
        return Ok(None);
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((!path.is_empty()).then_some(path))
}

fn epoch_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn new_request_id() -> String {
    let serial = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("request-{}-{serial}", epoch_secs())
}

#[derive(Deserialize)]
struct FramedRuntimeLog {
    request_id: String,
    role: String,
    agent_handle: Option<String>,
    category: String,
    message: String,
}

fn append_runtime_log_event(
    conn: &Connection,
    request_id: &str,
    chat_id: &str,
    source: &str,
    stream: &str,
    agent_handle: Option<&str>,
    role: &str,
    category: &str,
    message: &str,
) -> Result<(), String> {
    conn.execute(
        "INSERT INTO runtime_log_events(
           request_id,chat_id,seq,ts,source,stream,agent_handle,role,category,message
         ) VALUES(
           ?1,?2,
           COALESCE((SELECT MAX(seq)+1 FROM runtime_log_events WHERE request_id=?1),1),
           ?3,?4,?5,?6,?7,?8,?9
         )",
        params![
            request_id,
            chat_id,
            chat_timestamp(epoch_secs()),
            source,
            stream,
            agent_handle,
            role,
            category,
            message,
        ],
    )
    .map(|_| ())
    .map_err(|e| e.to_string())
}

fn persist_runtime_line(
    app: &tauri::AppHandle,
    launch_request_id: &str,
    chat_id: &str,
    which: TailStream,
    raw_line: &str,
) -> String {
    let raw_line = raw_line.trim_end_matches('\r');
    let parsed = raw_line
        .strip_prefix(RUNTIME_LOG_PREFIX)
        .and_then(|json| serde_json::from_str::<FramedRuntimeLog>(json).ok())
        .filter(|event| event.request_id == launch_request_id);
    let (source, handle, role, category, message) = match parsed {
        Some(event) => (
            if event.agent_handle.is_some() {
                "worker"
            } else {
                "root"
            },
            event.agent_handle,
            event.role,
            event.category,
            event.message,
        ),
        None => (
            "root",
            None,
            "root".to_string(),
            "output".to_string(),
            raw_line.to_string(),
        ),
    };
    let stream = match which {
        TailStream::Stdout => "stdout",
        TailStream::Stderr => "stderr",
    };
    let state = app.state::<AppState>();
    if let Ok(conn) = state.db.lock() {
        let _ = append_runtime_log_event(
            &conn,
            launch_request_id,
            chat_id,
            source,
            stream,
            handle.as_deref(),
            &role,
            &category,
            &message,
        );
    }
    message
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
    chat_id: &str,
) -> Result<(), String> {
    conn.execute(
        "INSERT INTO chat_log(ts,role,tone,text,surface,chat_id) VALUES(?1,?2,?3,?4,?5,?6)",
        rusqlite::params![
            chat_timestamp(epoch_secs()),
            role,
            tone,
            text,
            surface,
            chat_id
        ],
    )
    .map(|_| ())
    .map_err(|e| e.to_string())
}

/// Normalize persisted legacy labels while keeping all new mutations owned by
/// the Orchestrator action paths below.
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

fn set_runtime_owner(
    runtime: &mut ChatAgentRuntime,
    chat_id: &str,
    surface: &str,
    pipeline_name: &str,
    task: &str,
    started_at: i64,
) {
    runtime.chat_id = chat_id.to_string();
    runtime.surface = surface_arg(surface).to_string();
    runtime.pipeline_name = pipeline_name.to_string();
    runtime.task = task.to_string();
    runtime.started_at = Some(started_at);
}

fn claim_runtime_owner(
    runtime: &mut ChatAgentRuntime,
    chat_id: &str,
    surface: &str,
    pipeline_name: &str,
    task: &str,
    started_at: i64,
) -> Result<(), String> {
    if runtime.running {
        let owner_surface = surface_arg(&runtime.surface);
        return Err(format!(
            "This project already has an active {owner_surface} run in session {}. Cancel it or wait for it to finish.",
            runtime.chat_id
        ));
    }
    runtime.running = true;
    runtime.child = None;
    runtime.cancel_requested = false;
    runtime.stdout_tail.clear();
    runtime.stderr_tail.clear();
    runtime.terminal_status.clear();
    set_runtime_owner(runtime, chat_id, surface, pipeline_name, task, started_at);
    Ok(())
}

fn ensure_runtime_owner(runtime: &ChatAgentRuntime, requested_chat_id: &str) -> Result<(), String> {
    if runtime.chat_id == requested_chat_id {
        Ok(())
    } else {
        Err(format!(
            "Session {requested_chat_id} is read-only while session {} owns the active run.",
            runtime.chat_id
        ))
    }
}

fn authorize_cancel_request(
    requested_chat_id: &str,
    active_chat_id: &str,
    viewed_chat_id: Option<&str>,
    runtime: &ChatAgentRuntime,
) -> Result<String, String> {
    let displayed_chat_id = viewed_chat_id.unwrap_or(active_chat_id);
    let requested_chat_id = if requested_chat_id.trim().is_empty() {
        displayed_chat_id
    } else {
        requested_chat_id
    };
    if requested_chat_id != displayed_chat_id {
        return Err(format!(
            "Session {requested_chat_id} is not the currently displayed Orchestrator session {displayed_chat_id}."
        ));
    }
    ensure_runtime_owner(runtime, displayed_chat_id)?;
    Ok(displayed_chat_id.to_string())
}

fn clear_driver_chat_log(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    surface: &str,
    chat_id: &str,
) -> Result<(), String> {
    if rt.running {
        return Err(
            "Cannot clear chat while the agent is running. Cancel or wait for it to finish.".into(),
        );
    }
    conn.execute(
        "DELETE FROM chat_log WHERE surface = ?1 AND chat_id = ?2",
        rusqlite::params![surface, chat_id],
    )
    .map_err(|e| e.to_string())?;
    if rt.chat_id == chat_id {
        rt.stdout_tail.clear();
        rt.stderr_tail.clear();
        rt.task.clear();
        rt.started_at = None;
        rt.cancel_requested = false;
        rt.terminal_status.clear();
    }
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

fn store_session_nonce(conn: &Connection, surface: &str, nonce: &str) -> Result<(), String> {
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?1,?2) \
         ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rusqlite::params![session_nonce_key(surface), nonce],
    )
    .map(|_| ())
    .map_err(|e| e.to_string())
}

/// Load the persisted session nonce for `surface`, minting and storing one on
/// first use so restarting the app continues the same session (option a).
fn load_or_mint_session_nonce(conn: &Connection, surface: &str) -> Result<String, String> {
    let key = session_nonce_key(surface);
    if let Ok(nonce) = conn.query_row("SELECT value FROM meta WHERE key=?1", [key.as_str()], |r| {
        r.get::<_, String>(0)
    }) {
        return Ok(nonce);
    }
    let nonce = mint_session_nonce();
    store_session_nonce(conn, surface, &nonce)?;
    Ok(nonce)
}

/// Start a fresh session on `surface`: mint a new nonce (so the agent's
/// chat_id-keyed replay history starts empty next run), persist it, swap the
/// live chat_id. Old display and replay history remain under the old chat_id
/// for future browsing.
fn new_driver_session(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    chat_id_slot: &Mutex<String>,
    viewed_chat_id_slot: &Mutex<Option<String>>,
    project_root: &Path,
    surface: &str,
) -> Result<(), String> {
    if rt.running {
        return Err(
            "Cannot start a new session while the agent is running. Cancel or wait for it to finish."
                .into(),
        );
    }
    let old_id = chat_id_slot.lock().map_err(|e| e.to_string())?.clone();
    let nonce = mint_session_nonce();
    store_session_nonce(conn, surface, &nonce)?;
    let new_id = scoped_chat_id(project_root, surface, &nonce);
    *chat_id_slot.lock().map_err(|e| e.to_string())? = new_id;
    if surface == "orchestrator" {
        *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? = None;
    }
    if rt.chat_id == old_id {
        rt.stdout_tail.clear();
        rt.stderr_tail.clear();
        rt.task.clear();
        rt.started_at = None;
        rt.cancel_requested = false;
        rt.terminal_status.clear();
    }
    Ok(())
}

fn select_driver_session(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    chat_id_slot: &Mutex<String>,
    viewed_chat_id_slot: &Mutex<Option<String>>,
    surface: &str,
    requested_chat_id: &str,
) -> Result<(), String> {
    let surface = surface_arg(surface);
    let current_id = chat_id_slot.lock().map_err(|e| e.to_string())?.clone();
    let (current_scope, _) = current_id
        .rsplit_once('-')
        .ok_or_else(|| "Current project session ID is invalid.".to_string())?;
    let (requested_scope, requested_nonce) = requested_chat_id
        .rsplit_once('-')
        .ok_or_else(|| "Requested project session ID is invalid.".to_string())?;
    if requested_chat_id.is_empty() || current_scope != requested_scope {
        return Err("Requested session does not belong to this project and surface.".into());
    }
    let exists: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM chat_log WHERE surface=?1 AND chat_id=?2",
            rusqlite::params![surface, requested_chat_id],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())?;
    if exists == 0 {
        return Err("Requested session was not found in this project.".into());
    }

    if rt.running {
        *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? =
            (requested_chat_id != current_id).then(|| requested_chat_id.to_string());
        return Ok(());
    }

    store_session_nonce(conn, surface, requested_nonce)?;
    *chat_id_slot.lock().map_err(|e| e.to_string())? = requested_chat_id.to_string();
    *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? = None;
    Ok(())
}

fn resolve_orchestrator_history_target(
    conn: &Connection,
    current_chat_id: &str,
    requested_chat_id: &str,
) -> Result<String, String> {
    if requested_chat_id.trim().is_empty() || requested_chat_id == current_chat_id {
        return Ok(current_chat_id.to_string());
    }
    let (current_scope, _) = current_chat_id
        .rsplit_once('-')
        .ok_or_else(|| "Current project session ID is invalid.".to_string())?;
    let (requested_scope, _) = requested_chat_id
        .rsplit_once('-')
        .ok_or_else(|| "Requested project session ID is invalid.".to_string())?;
    if current_scope != requested_scope {
        return Err("Requested session does not belong to this project and surface.".into());
    }
    let exists: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM chat_log WHERE surface='orchestrator' AND chat_id=?1",
            [requested_chat_id],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    if exists == 0 {
        return Err("Requested session was not found in this project.".into());
    }
    Ok(requested_chat_id.to_string())
}

/// Validate, persist, promote, and claim while the caller holds the one runtime
/// lease. The helper accepts already-borrowed state so it cannot acquire DB and
/// runtime mutexes in a reverse nested order.
fn prepare_orchestrator_send(
    conn: &mut Connection,
    rt: &mut ChatAgentRuntime,
    active_chat_id: &mut String,
    viewed_chat_id: &mut Option<String>,
    requested_chat_id: &str,
    text: &str,
    pipeline_name: Option<&str>,
    started_at: i64,
) -> Result<String, String> {
    if rt.running {
        let owner_surface = surface_arg(&rt.surface);
        return Err(format!(
            "This project already has an active {owner_surface} run in session {}. Cancel it or wait for it to finish.",
            rt.chat_id
        ));
    }
    let target = resolve_orchestrator_history_target(conn, active_chat_id, requested_chat_id)?;
    let promotion_nonce = (target != *active_chat_id)
        .then(|| target.rsplit_once('-').map(|(_, nonce)| nonce.to_string()))
        .flatten();
    let tx = conn.transaction().map_err(|e| e.to_string())?;
    if let Some(nonce) = promotion_nonce.as_deref() {
        store_session_nonce(&tx, "orchestrator", nonce)?;
    }
    insert_chat(&tx, "you", None, text, "orchestrator", &target)?;
    tx.commit().map_err(|e| e.to_string())?;

    claim_runtime_owner(
        rt,
        &target,
        "orchestrator",
        pipeline_name.unwrap_or_default(),
        text,
        started_at,
    )?;
    if target != *active_chat_id {
        *active_chat_id = target.clone();
        *viewed_chat_id = None;
    }
    Ok(target)
}

#[cfg(test)]
fn resolve_orchestrator_send_session(
    conn: &Connection,
    rt: &mut ChatAgentRuntime,
    chat_id_slot: &Mutex<String>,
    viewed_chat_id_slot: &Mutex<Option<String>>,
    requested_chat_id: &str,
) -> Result<String, String> {
    let current_id = chat_id_slot.lock().map_err(|e| e.to_string())?.clone();
    if requested_chat_id.trim().is_empty() || requested_chat_id == current_id {
        return Ok(current_id);
    }
    if rt.running {
        return Err("Cannot resume a historical session while another agent is running.".into());
    }
    select_driver_session(
        conn,
        rt,
        chat_id_slot,
        viewed_chat_id_slot,
        "orchestrator",
        requested_chat_id,
    )?;
    chat_id_slot
        .lock()
        .map_err(|e| e.to_string())
        .map(|id| id.clone())
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
        // The two sources normalize paths differently: the mentioned-artifact
        // scan canonicalizes (on Windows a verbatim `\\?\C:\…` path), while the
        // manifest paths arrive raw. Comparing raw PathBufs would miss that the
        // same file appears in both forms and list it twice. Canonicalize each
        // to one normal form (falling back to the raw path when the file cannot
        // be resolved) before the dedup check.
        let key = path.canonicalize().unwrap_or(path);
        if !linked.iter().any(|p| p == &key) {
            linked.push(key);
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

fn artifact_open_failed_message(raw_path: &str, error: &str) -> String {
    format!("Could not open artifact.\n\nPath: `{raw_path}`\n\n{error}")
}

fn append_driver_chat_to_conn(
    conn: &Connection,
    surface: &str,
    chat_id: &str,
    tone: Option<&str>,
    text: &str,
) -> Result<(), String> {
    insert_chat(conn, "driver", tone, text, surface, chat_id)
}

fn append_driver_chat_to(
    app: &tauri::AppHandle,
    surface: &str,
    chat_id: &str,
    tone: Option<&str>,
    text: &str,
) {
    let state = app.state::<AppState>();
    if chat_id.is_empty() {
        return;
    }
    let Ok(conn) = state.db.lock() else {
        return;
    };
    let _ = append_driver_chat_to_conn(&conn, surface, chat_id, tone, text);
}

fn pump_stream(
    stream: impl Read + Send + 'static,
    shared: Arc<Mutex<ChatAgentRuntime>>,
    which: TailStream,
    app: tauri::AppHandle,
    request_id: String,
    chat_id: String,
) -> JoinHandle<()> {
    std::thread::spawn(move || {
        let mut reader = stream;
        let mut buf = [0u8; 4096];
        let mut pending = String::new();
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => {
                    if !pending.is_empty() {
                        let display =
                            persist_runtime_line(&app, &request_id, &chat_id, which, &pending);
                        if let Ok(mut rt) = shared.lock() {
                            let tail = match which {
                                TailStream::Stdout => &mut rt.stdout_tail,
                                TailStream::Stderr => &mut rt.stderr_tail,
                            };
                            musubi_data::push_bounded_tail(tail, &display, TAIL_CAP);
                        }
                    }
                    break;
                }
                Ok(n) => {
                    let chunk = String::from_utf8_lossy(&buf[..n]).into_owned();
                    pending.push_str(&chunk);
                    while let Some(newline) = pending.find('\n') {
                        let raw_line = pending[..newline].to_string();
                        pending.drain(..=newline);
                        let display =
                            persist_runtime_line(&app, &request_id, &chat_id, which, &raw_line);
                        eprintln!("{display}");
                        if let Ok(mut rt) = shared.lock() {
                            let tail = match which {
                                TailStream::Stdout => &mut rt.stdout_tail,
                                TailStream::Stderr => &mut rt.stderr_tail,
                            };
                            musubi_data::push_bounded_tail(tail, &format!("{display}\n"), TAIL_CAP);
                        }
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
    pipeline_name: Option<&str>,
) -> Result<(), String> {
    // Fail closed: the operator picked a boundary and it is not in effect.
    // Launching anyway would export the runtime checkout as the workspace and
    // let the agent edit Musubi's own install.
    if let Some(reason) = state.workspace_error.as_ref() {
        return Err(reason.clone());
    }
    let started_at = epoch_secs();
    let request_id = new_request_id();
    let launch_chat_id = chat_id.to_string();
    let launch_surface = {
        let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
        ensure_runtime_owner(&rt, &launch_chat_id)?;
        rt.request_id = request_id.clone();
        surface_arg(&rt.surface).to_string()
    };
    let fixed_root = state
        .project_root
        .canonicalize()
        .map_err(|e| format!("Musubi root is unavailable: {e}"))?;
    let request_manifest = {
        let mut conn = state.db.lock().map_err(|e| e.to_string())?;
        let session_grants =
            musubi_data::list_session_folder_grants(&conn, chat_id).map_err(|e| e.to_string())?;
        let mut aliases = std::collections::HashSet::new();
        let mut paths = vec![fixed_root.clone()];
        for grant in session_grants {
            let alias = normalize_folder_alias(&grant.alias)?;
            if alias != grant.alias || !aliases.insert(alias) {
                return Err(format!(
                    "Folder alias {} is invalid or duplicated.",
                    grant.alias
                ));
            }
            let stored = PathBuf::from(&grant.canonical_path);
            let current = stored
                .canonicalize()
                .map_err(|e| format!("Folder {} is unavailable: {e}", grant.alias))?;
            if !current.is_dir() {
                return Err(format!("Folder {} is no longer a directory.", grant.alias));
            }
            if workspace_path_key(&current) != workspace_path_key(&stored) {
                return Err(format!(
                    "Folder {} changed since it was attached; remove and add it again.",
                    grant.alias
                ));
            }
            if paths.iter().any(|other| {
                is_inside_workspace(&current, other) || is_inside_workspace(other, &current)
            }) {
                return Err(format!(
                    "Folder {} overlaps another request root.",
                    grant.alias
                ));
            }
            paths.push(current);
        }
        musubi_data::snapshot_request_folder_grants(
            &mut conn,
            &request_id,
            chat_id,
            &fixed_root.display().to_string(),
            &epoch_secs().to_string(),
        )
        .map_err(|e| e.to_string())?;
        musubi_data::list_request_folder_grants(&conn, &request_id).map_err(|e| e.to_string())?
    };

    let mut env = musubi_data::current_env_map();
    let setup =
        musubi_data::detect_setup_status(&env, &state.project_root, state.audit_db.as_ref());
    let launch_root = fixed_root;
    env.insert(
        "MUSUBI_ROOT".into(),
        launch_root.to_string_lossy().to_string(),
    );
    env.insert(
        "MUSUBI_FOLDER_GRANTS_JSON".into(),
        serde_json::to_string(&request_manifest).map_err(|e| e.to_string())?,
    );
    if let Some(audit_db) = state.audit_db.as_ref() {
        env.insert(
            "MUSUBI_DB".into(),
            audit_db.path.to_string_lossy().to_string(),
        );
        if let Some(parent) = audit_db.path.parent() {
            env.insert(
                "MUSUBI_STATE_DB".into(),
                parent.join("musubi.db").to_string_lossy().to_string(),
            );
        }
    }
    let llm_config_path =
        (!setup.llm_config_path.is_empty()).then(|| PathBuf::from(&setup.llm_config_path));
    if !setup.llm_config_path.is_empty() {
        env.entry("MUSUBI_LLM_CONFIG".into())
            .or_insert_with(|| setup.llm_config_path.clone());
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
        musubi_data::AgentLaunchScope {
            chat_id: Some(chat_id),
            pipeline_name,
            request_id: Some(&request_id),
        },
    )?;
    {
        let conn = state.db.lock().map_err(|e| e.to_string())?;
        let db_path = state
            .audit_db
            .as_ref()
            .map(|db| db.path.display().to_string())
            .unwrap_or_else(|| "<memory>".into());
        let db_source = state
            .audit_db
            .as_ref()
            .map(|db| db.source.as_str())
            .unwrap_or("memory");
        append_runtime_log_event(
            &conn,
            &request_id,
            chat_id,
            "host",
            "host",
            None,
            "host",
            "host",
            &format!(
                "[musubi] reading audit.db at {db_path} ({db_source}) project_root={}",
                launch_root.display()
            ),
        )?;
        append_runtime_log_event(
            &conn,
            &request_id,
            chat_id,
            "host",
            "host",
            None,
            "host",
            "host",
            &format!(
                "[musubi] launching agent cwd={} args={:?}",
                spec.cwd.display(),
                spec.args
            ),
        )?;
    }
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
            if let Ok(conn) = state.db.lock() {
                let _ = append_runtime_log_event(
                    &conn,
                    &request_id,
                    chat_id,
                    "host",
                    "host",
                    None,
                    "host",
                    "host",
                    &format!("[musubi] launch failed: {e}"),
                );
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
    let stdout_pump = stdout.map(|out| {
        pump_stream(
            out,
            shared.clone(),
            TailStream::Stdout,
            app.clone(),
            request_id.clone(),
            launch_chat_id.clone(),
        )
    });
    let stderr_pump = stderr.map(|err| {
        pump_stream(
            err,
            shared.clone(),
            TailStream::Stderr,
            app.clone(),
            request_id.clone(),
            launch_chat_id.clone(),
        )
    });

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
                        let stdout_tail = rt.stdout_tail.clone();
                        let stderr_tail = rt.stderr_tail.clone();
                        let terminal = terminal_status(
                            cancelled,
                            code,
                            &format!("{stderr_tail}\n{stdout_tail}"),
                        );
                        rt.terminal_status = terminal.to_string();
                        (stdout_tail, stderr_tail, cancelled)
                    }
                    Err(_) => (String::new(), String::new(), false),
                };
                let log = process_log(&stdout_tail, &stderr_tail);
                {
                    let state = app.state::<AppState>();
                    if let Ok(conn) = state.db.lock() {
                        let event = if cancelled {
                            "[musubi] agent cancelled by user".to_string()
                        } else {
                            format!("[musubi] agent exited status={code}")
                        };
                        let _ = append_runtime_log_event(
                            &conn,
                            &request_id,
                            &launch_chat_id,
                            "host",
                            "host",
                            None,
                            "host",
                            "host",
                            &event,
                        );
                    };
                }
                if cancelled {
                    append_driver_chat_to(
                        &app,
                        &launch_surface,
                        &launch_chat_id,
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
                        append_driver_chat_to(
                            &app,
                            &launch_surface,
                            &launch_chat_id,
                            None,
                            &append_process_log_link(&text, &log),
                        );
                    } else {
                        let text = append_artifact_links(answer, &artifact_root, &artifacts);
                        append_driver_chat_to(
                            &app,
                            &launch_surface,
                            &launch_chat_id,
                            None,
                            &append_process_log_link(&text, &log),
                        );
                    }
                } else {
                    let detail = if !stderr_tail.trim().is_empty() {
                        stderr_tail.trim()
                    } else {
                        stdout_tail.trim()
                    };
                    let text = summarize_agent_failure(code, detail);
                    append_driver_chat_to(
                        &app,
                        &launch_surface,
                        &launch_chat_id,
                        Some("deny"),
                        &append_process_log_link(&text, &log),
                    );
                }
                break;
            }
            Err(e) => {
                if let Ok(mut rt) = shared.lock() {
                    rt.running = false;
                    rt.child = None;
                    rt.terminal_status = "failed".into();
                }
                append_driver_chat_to(
                    &app,
                    &launch_surface,
                    &launch_chat_id,
                    Some("deny"),
                    &format!("Agent wait failed: {e}"),
                );
                break;
            }
        }
    });

    Ok(())
}

struct CancelClaim {
    child: Option<Arc<Mutex<Child>>>,
    owner_surface: String,
    owner_chat_id: String,
}

fn prepare_cancel_claim_with_hook<BeforeRuntimeLock>(
    runtime: &Mutex<ChatAgentRuntime>,
    active_chat_id: &Mutex<String>,
    viewed_chat_id: &Mutex<Option<String>>,
    requested_chat_id: &str,
    before_runtime_lock: BeforeRuntimeLock,
) -> Result<Option<CancelClaim>, String>
where
    BeforeRuntimeLock: FnOnce(),
{
    before_runtime_lock();
    let mut rt = runtime.lock().map_err(|e| e.to_string())?;
    if !rt.running {
        return Ok(None);
    }
    let active_chat_id = active_chat_id.lock().map_err(|e| e.to_string())?.clone();
    let viewed_chat_id = viewed_chat_id.lock().map_err(|e| e.to_string())?.clone();
    let owner_chat_id = authorize_cancel_request(
        requested_chat_id,
        &active_chat_id,
        viewed_chat_id.as_deref(),
        &rt,
    )?;
    let claim = CancelClaim {
        child: rt.child.clone(),
        owner_surface: surface_arg(&rt.surface).to_string(),
        owner_chat_id,
    };
    if claim.child.is_some() {
        rt.cancel_requested = true;
    } else {
        rt.running = false;
        rt.child = None;
        rt.cancel_requested = false;
    }
    Ok(Some(claim))
}

fn cancel_chat_agent(
    app: &tauri::AppHandle,
    state: &AppState,
    requested_chat_id: &str,
) -> Result<(), String> {
    let Some(claim) = prepare_cancel_claim_with_hook(
        &state.chat_agent,
        &state.chat_id,
        &state.viewed_orchestrator_chat_id,
        requested_chat_id,
        || {},
    )?
    else {
        return Ok(());
    };

    let Some(child) = claim.child else {
        append_driver_chat_to(
            app,
            &claim.owner_surface,
            &claim.owner_chat_id,
            Some("deny"),
            "Agent cancelled by user.",
        );
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
    /// Why the persisted workspace could not be honoured, if it could not.
    ///
    /// Set means the Console is NOT running inside the boundary the operator
    /// selected. Execution stays blocked until they choose a valid folder —
    /// silently reverting to the runtime checkout would let an agent modify
    /// Musubi's own install while the operator believes their application is
    /// the target.
    workspace_error: Option<String>,
}

fn open_configured_db() -> OpenedDb {
    let mut env = musubi_data::current_env_map();
    env.remove("MUSUBI_WORKSPACE");
    std::env::remove_var("MUSUBI_WORKSPACE");
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if let Some(resolved) = musubi_data::resolve_audit_db_path(&env, &cwd) {
        match prepare_audit_connection(&resolved) {
            Ok(conn) => {
                let project_root = resolve_project_root(&env, &cwd, Some(&resolved));
                let state_db = open_state_db(&resolved);
                std::env::set_var("MUSUBI_ROOT", &project_root);
                return OpenedDb {
                    conn,
                    state_db,
                    project_root,
                    audit_db: Some(resolved),
                    workspace_error: None,
                };
            }
            Err(reason) => {
                eprintln!("[musubi] cannot open audit.db: {reason}");
            }
        }
    }
    let project_root = resolve_project_root(&env, &cwd, None);
    std::env::set_var("MUSUBI_ROOT", &project_root);
    OpenedDb {
        conn: open_db(),
        state_db: None,
        project_root,
        audit_db: None,
        workspace_error: None,
    }
}

/// Create the parent directory and open the audit DB, reporting failure
/// instead of aborting the process.
fn prepare_audit_connection(resolved: &musubi_data::ResolvedAuditDb) -> Result<Connection, String> {
    if let Some(parent) = resolved.path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    let conn = Connection::open(&resolved.path).map_err(|e| e.to_string())?;
    let _ = musubi_data::init_schema(&conn);
    Ok(conn)
}

fn open_state_db(audit_db: &musubi_data::ResolvedAuditDb) -> Option<Connection> {
    let state_db = musubi_data::resolve_state_db_path(audit_db)?;
    Connection::open_with_flags(state_db.path, OpenFlags::SQLITE_OPEN_READ_ONLY).ok()
}

fn snapshot_session_ids(state: &AppState) -> Result<(String, Option<String>), String> {
    let orchestrator_chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
    let viewed_orchestrator_chat_id = state
        .viewed_orchestrator_chat_id
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    Ok((orchestrator_chat_id, viewed_orchestrator_chat_id))
}

fn load_legacy_pipeline_chat_id(conn: &Connection) -> Result<String, String> {
    conn.query_row(
        "SELECT chat_id FROM chat_log WHERE surface='pipeline' ORDER BY id DESC LIMIT 1",
        [],
        |row| row.get::<_, String>(0),
    )
    .optional()
    .map(|value| value.unwrap_or_default())
    .map_err(|e| e.to_string())
}

fn snapshot(state: &AppState) -> Result<musubi_data::State, String> {
    // Snapshot the independently guarded session selectors before opening the
    // database read boundary. Mutation paths may hold one of these guards
    // before acquiring `db`; nesting them in the opposite order can deadlock.
    let (orchestrator_chat_id, viewed_orchestrator_chat_id) = snapshot_session_ids(state)?;
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    let pipeline_chat_id = load_legacy_pipeline_chat_id(&conn)?;
    let state_conn = state
        .state_db
        .as_ref()
        .map(|db| db.lock().map_err(|e| e.to_string()))
        .transpose()?;
    let mut st = musubi_data::load_state_with_pipeline_runs(&conn, state_conn.as_deref())
        .map_err(|e| e.to_string())?;
    let displayed_orchestrator_chat_id = viewed_orchestrator_chat_id
        .as_deref()
        .unwrap_or(&orchestrator_chat_id);
    st.chat =
        musubi_data::load_chat_for_session(&conn, "orchestrator", displayed_orchestrator_chat_id)
            .map_err(|e| e.to_string())?;
    st.session_folder_grants =
        musubi_data::list_session_folder_grants(&conn, displayed_orchestrator_chat_id)
            .map_err(|e| e.to_string())?;
    st.pipe_chat = musubi_data::load_chat_for_session(&conn, "pipeline", &pipeline_chat_id)
        .map_err(|e| e.to_string())?;
    st.orchestrator_chat_id = orchestrator_chat_id;
    st.viewed_orchestrator_chat_id = viewed_orchestrator_chat_id.unwrap_or_default();
    st.pipeline_chat_id = pipeline_chat_id;
    st.pipeline_catalog = musubi_data::read_studio_pipeline_catalog(&state.project_root);
    st.pipeline_builder_catalog = musubi_data::read_pipeline_builder_catalog(&state.project_root);
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
    st.workspace_blocked_reason.clear();
    let llm_config_path = (!st.setup_status.llm_config_path.is_empty())
        .then(|| PathBuf::from(&st.setup_status.llm_config_path));
    st.active_profile =
        musubi_data::read_active_profile_for_config(&conn, llm_config_path.as_deref());
    if let Some(path) = llm_config_path {
        st.profiles = musubi_data::read_llm_profiles_from_path(path);
    }
    drop(state_conn);
    drop(conn);
    if let Ok(rt) = state.chat_agent.lock() {
        st.driver_status = musubi_data::DriverStatus {
            running: rt.running,
            request_id: rt.request_id.clone(),
            chat_id: rt.chat_id.clone(),
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
fn load_pipeline_recipe(
    name: String,
    state: tauri::State<AppState>,
) -> Result<musubi_data::PipelineRecipe, String> {
    load_pipeline_recipe_at(&state.project_root, &name)
}

fn load_pipeline_recipe_at(
    project_root: &Path,
    name: &str,
) -> Result<musubi_data::PipelineRecipe, String> {
    musubi_data::read_pipeline_recipe(project_root, name)
}

#[tauri::command]
fn validate_pipeline_recipe(
    recipe: musubi_data::PipelineRecipe,
    state: tauri::State<AppState>,
) -> Result<Vec<musubi_data::PipelineFinding>, String> {
    Ok(validate_pipeline_recipe_at(&state.project_root, &recipe))
}

fn validate_pipeline_recipe_at(
    project_root: &Path,
    recipe: &musubi_data::PipelineRecipe,
) -> Vec<musubi_data::PipelineFinding> {
    musubi_data::validate_pipeline_recipe(project_root, recipe)
}

#[tauri::command]
fn save_pipeline_recipe(
    recipe: musubi_data::PipelineRecipe,
    state: tauri::State<AppState>,
) -> Result<musubi_data::PipelineSaveResult, String> {
    Ok(save_pipeline_recipe_at(&state.project_root, &recipe))
}

fn save_pipeline_recipe_at(
    project_root: &Path,
    recipe: &musubi_data::PipelineRecipe,
) -> musubi_data::PipelineSaveResult {
    musubi_data::save_pipeline_recipe(project_root, recipe)
}

fn resolve_orchestrator_launch(
    text: &str,
    mode: &str,
    pipeline_name: &str,
    catalog: &[musubi_data::PipelineCatalogEntry],
) -> Result<(String, Option<String>), String> {
    let task = text.trim();
    if task.is_empty() {
        return Err("Task is empty — describe the work to run.".into());
    }
    match mode {
        "direct" => Ok((task.to_string(), None)),
        "pipeline" => {
            let pipeline_name = pipeline_name.trim();
            if !musubi_data::valid_pipeline_name(pipeline_name) {
                return Err(format!("invalid pipeline name: {pipeline_name:?}"));
            }
            let Some(entry) = catalog.iter().find(|entry| entry.name == pipeline_name) else {
                return Err(format!(
                    "Pipeline {pipeline_name:?} is not registered for Orchestrator."
                ));
            };
            if !entry.runnable || entry.stages.len() < 2 {
                return Err(if entry.blocked_reason.is_empty() {
                    format!("Pipeline {pipeline_name:?} is not runnable.")
                } else {
                    entry.blocked_reason.clone()
                });
            }
            Ok((task.to_string(), Some(pipeline_name.to_string())))
        }
        _ => Err(format!("Unknown Orchestrator launch mode: {mode:?}.")),
    }
}

fn dispatch_orchestrator_send<Prepare, Launch>(
    text: &str,
    requested_chat_id: &str,
    mode: &str,
    pipeline_name: &str,
    catalog: &[musubi_data::PipelineCatalogEntry],
    prepare: Prepare,
    launch: Launch,
) -> Result<(), String>
where
    Prepare: FnOnce(&str, &str, Option<&str>) -> Result<String, String>,
    Launch: FnOnce(&str, &str, Option<&str>) -> Result<(), String>,
{
    let (task, pipeline_name) = resolve_orchestrator_launch(text, mode, pipeline_name, catalog)?;
    let chat_id = prepare(&task, requested_chat_id, pipeline_name.as_deref())?;
    launch(&task, &chat_id, pipeline_name.as_deref())
}

fn normalize_folder_alias(raw: &str) -> Result<String, String> {
    let alias = raw.trim().to_ascii_lowercase();
    let valid = !alias.is_empty()
        && alias.len() <= 32
        && alias.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_lowercase()
            } else {
                byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'-' | b'_')
            }
        });
    if !valid || alias == "musubi" {
        return Err("Folder alias must match [a-z][a-z0-9_-]{0,31}; musubi is reserved.".into());
    }
    Ok(alias)
}

fn default_folder_alias(path: &Path, used: &std::collections::HashSet<String>) -> String {
    let raw = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("folder")
        .to_ascii_lowercase();
    let mut base = raw
        .chars()
        .map(|ch| {
            if ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '-' | '_') {
                ch
            } else {
                '-'
            }
        })
        .collect::<String>()
        .trim_matches(&['-', '_'][..])
        .to_string();
    if base.is_empty() || !base.as_bytes()[0].is_ascii_lowercase() {
        base = format!("folder-{base}");
    }
    base.truncate(32);
    let mut alias = base.clone();
    let mut suffix = 2;
    while alias == "musubi" || used.contains(&alias) {
        let tail = format!("-{suffix}");
        alias = format!("{}{}", &base[..base.len().min(32 - tail.len())], tail);
        suffix += 1;
    }
    alias
}

fn displayed_folder_chat_id(state: &AppState, requested: &str) -> Result<String, String> {
    let (active, viewed) = snapshot_session_ids(state)?;
    let displayed = viewed.unwrap_or(active);
    if !requested.trim().is_empty() && requested != displayed {
        return Err("Folder grants may only edit the displayed session.".into());
    }
    Ok(displayed)
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
        "add_session_folder" => {
            let runtime = state.chat_agent.lock().map_err(|e| e.to_string())?;
            if runtime.running {
                return Err("Cannot edit session folders while an agent is running.".into());
            }
            let folder = canonical_workspace(&str_arg(0))?;
            let chat_id = displayed_folder_chat_id(state.inner(), &str_arg(1))?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            let existing = musubi_data::list_session_folder_grants(&conn, &chat_id)
                .map_err(|e| e.to_string())?;
            let fixed = state
                .project_root
                .canonicalize()
                .map_err(|e| e.to_string())?;
            if is_inside_workspace(&folder, &fixed) || is_inside_workspace(&fixed, &folder) {
                return Err("Attached folders may not overlap the Musubi root.".into());
            }
            for grant in &existing {
                let other = PathBuf::from(&grant.canonical_path);
                if is_inside_workspace(&folder, &other) || is_inside_workspace(&other, &folder) {
                    return Err(format!(
                        "Attached folder overlaps existing root {}.",
                        grant.alias
                    ));
                }
            }
            let used = existing
                .iter()
                .map(|grant| grant.alias.clone())
                .collect::<std::collections::HashSet<_>>();
            let alias = default_folder_alias(&folder, &used);
            musubi_data::insert_session_folder_grant(
                &conn,
                &musubi_data::FolderGrant {
                    chat_id,
                    grant_id: format!("folder-{}", new_request_id()),
                    alias,
                    canonical_path: folder.display().to_string(),
                    ordinal: existing.len() as i64,
                },
                &epoch_secs().to_string(),
            )
            .map_err(|e| e.to_string())?;
            drop(runtime);
        }
        "rename_session_folder" => {
            let runtime = state.chat_agent.lock().map_err(|e| e.to_string())?;
            if runtime.running {
                return Err("Cannot edit session folders while an agent is running.".into());
            }
            let chat_id = displayed_folder_chat_id(state.inner(), &str_arg(0))?;
            let alias = normalize_folder_alias(&str_arg(2))?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            if !musubi_data::rename_session_folder_grant(
                &conn,
                &chat_id,
                &str_arg(1),
                &alias,
                &epoch_secs().to_string(),
            )
            .map_err(|e| e.to_string())?
            {
                return Err("Folder grant no longer exists.".into());
            }
            drop(runtime);
        }
        "remove_session_folder" => {
            let runtime = state.chat_agent.lock().map_err(|e| e.to_string())?;
            if runtime.running {
                return Err("Cannot edit session folders while an agent is running.".into());
            }
            let chat_id = displayed_folder_chat_id(state.inner(), &str_arg(0))?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            if !musubi_data::remove_session_folder_grant(&conn, &chat_id, &str_arg(1))
                .map_err(|e| e.to_string())?
            {
                return Err("Folder grant no longer exists.".into());
            }
            drop(runtime);
        }
        "send_chat" => {
            let catalog = musubi_data::read_studio_pipeline_catalog(&state.project_root);
            let text = str_arg(0);
            let requested_chat_id = str_arg(1);
            dispatch_orchestrator_send(
                &text,
                &requested_chat_id,
                &str_arg(2),
                &str_arg(3),
                &catalog,
                |task, requested_chat_id, pipeline_name| {
                    let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
                    let mut conn = state.db.lock().map_err(|e| e.to_string())?;
                    let mut active = state.chat_id.lock().map_err(|e| e.to_string())?;
                    let mut viewed = state
                        .viewed_orchestrator_chat_id
                        .lock()
                        .map_err(|e| e.to_string())?;
                    prepare_orchestrator_send(
                        &mut conn,
                        &mut rt,
                        &mut active,
                        &mut viewed,
                        requested_chat_id,
                        task,
                        pipeline_name,
                        epoch_secs(),
                    )
                },
                |task, chat_id, pipeline_name| {
                    if let Err(e) = start_chat_agent(
                        app,
                        state.inner(),
                        task.to_string(),
                        chat_id,
                        pipeline_name,
                    ) {
                        if let Ok(mut rt) = state.chat_agent.lock() {
                            if rt.chat_id == chat_id {
                                rt.running = false;
                                rt.child = None;
                            }
                        }
                        let conn = state.db.lock().map_err(|err| err.to_string())?;
                        insert_chat(&conn, "driver", Some("deny"), &e, "orchestrator", chat_id)?;
                    }
                    Ok(())
                },
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
            let requested_chat_id = str_arg(0);
            cancel_chat_agent(&app, state.inner(), &requested_chat_id)?;
        }
        "select_session" => {
            let requested_chat_id = str_arg(0);
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            select_driver_session(
                &conn,
                &mut rt,
                &state.chat_id,
                &state.viewed_orchestrator_chat_id,
                "orchestrator",
                &requested_chat_id,
            )?;
        }
        "clear_driver_chat" => {
            let chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            clear_driver_chat_log(&conn, &mut rt, "orchestrator", &chat_id)?;
        }
        // Fresh Orchestrator session: re-mint its chat_id so the agent's replay
        // history starts empty. Old turns stay under the previous chat_id.
        "new_session" => {
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            new_driver_session(
                &conn,
                &mut rt,
                &state.chat_id,
                &state.viewed_orchestrator_chat_id,
                &state.project_root,
                "orchestrator",
            )?;
        }
        "open_artifact" => {
            let raw_path = str_arg(0);
            match open_workspace_path(&state.project_root, &raw_path) {
                Ok(_) => {}
                Err(e) => {
                    let chat_id = state.chat_id.lock().map_err(|err| err.to_string())?.clone();
                    let conn = state.db.lock().map_err(|err| err.to_string())?;
                    insert_chat(
                        &conn,
                        "driver",
                        Some("deny"),
                        &artifact_open_failed_message(&raw_path, &e),
                        "orchestrator",
                        &chat_id,
                    )?;
                }
            }
        }
        other => eprintln!("[musubi] unknown action: {other}"),
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let opened = open_configured_db();
    // Existing audit DBs predate chat_log.surface/chat_id; add either column
    // when missing. Errors (including "duplicate column") are ignored.
    {
        let _ = opened.conn.execute(
            "ALTER TABLE chat_log ADD COLUMN surface TEXT NOT NULL DEFAULT 'orchestrator'",
            [],
        );
        let _ = opened.conn.execute(
            "ALTER TABLE chat_log ADD COLUMN chat_id TEXT NOT NULL DEFAULT ''",
            [],
        );
    }
    // Continue the persisted Orchestrator session (option a: restart resumes
    // the current session); mint on first use. Pipeline Studio is builder-only
    // and therefore owns no live session nonce.
    let chat_nonce = load_or_mint_session_nonce(&opened.conn, "orchestrator")
        .expect("failed to persist orchestrator session nonce");
    let chat_id = scoped_chat_id(&opened.project_root, "orchestrator", &chat_nonce);
    // Pre-session rows can only belong to the session that was active when the
    // migration ran. Backfill once; future rows are written with their owner.
    let _ = opened.conn.execute(
        "UPDATE chat_log SET chat_id=?1 WHERE surface='orchestrator' AND chat_id=''",
        [&chat_id],
    );
    tauri::Builder::default()
        .manage(AppState {
            db: Mutex::new(opened.conn),
            state_db: opened.state_db.map(Mutex::new),
            paused: AtomicBool::new(false),
            project_root: opened.project_root,
            audit_db: opened.audit_db,
            chat_agent: Arc::new(Mutex::new(ChatAgentRuntime::default())),
            chat_id: Mutex::new(chat_id),
            viewed_orchestrator_chat_id: Mutex::new(None),
            workspace_error: opened.workspace_error,
        })
        .invoke_handler(tauri::generate_handler![
            get_state,
            action,
            choose_workspace,
            load_pipeline_recipe,
            validate_pipeline_recipe,
            save_pipeline_recipe
        ])
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
    fn workspace_selection_accepts_only_existing_directories() {
        let root = std::env::temp_dir().join(format!("musubi-workspace-picker-{}", epoch_secs()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("file.txt");
        std::fs::write(&file, "x").unwrap();

        assert_eq!(
            canonical_workspace(root.to_str().unwrap()).unwrap(),
            root.canonicalize().unwrap()
        );
        assert!(canonical_workspace(file.to_str().unwrap()).is_err());
        assert!(canonical_workspace(root.join("missing").to_str().unwrap()).is_err());
        assert!(canonical_workspace("  ").is_err());

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn runtime_ledger_assigns_monotonic_sequence_per_request() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();

        append_runtime_log_event(
            &conn,
            "request-1",
            "chat-1",
            "host",
            "host",
            None,
            "host",
            "host",
            "launch",
        )
        .unwrap();
        append_runtime_log_event(
            &conn,
            "request-1",
            "chat-1",
            "worker",
            "stderr",
            Some("worker-7"),
            "coder",
            "tools",
            "write ok",
        )
        .unwrap();

        let seqs: Vec<i64> = conn
            .prepare("SELECT seq FROM runtime_log_events ORDER BY id")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        assert_eq!(seqs, vec![1, 2]);
    }

    #[test]
    fn snapshot_session_ids_do_not_wait_for_database() {
        let state = AppState {
            db: Mutex::new(Connection::open_in_memory().unwrap()),
            state_db: None,
            paused: AtomicBool::new(false),
            project_root: PathBuf::from("."),
            audit_db: None,
            chat_agent: Arc::new(Mutex::new(ChatAgentRuntime::default())),
            chat_id: Mutex::new("gui-orchestrator-project-active".into()),
            viewed_orchestrator_chat_id: Mutex::new(Some("gui-orchestrator-project-viewed".into())),
            workspace_error: None,
        };
        let _db_guard = state.db.lock().unwrap();

        let ids = snapshot_session_ids(&state).unwrap();

        assert_eq!(ids.0, "gui-orchestrator-project-active");
        assert_eq!(ids.1.as_deref(), Some("gui-orchestrator-project-viewed"));
    }

    #[test]
    fn legacy_pipeline_chat_id_is_read_without_minting_a_new_session() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','pipeline','gui-pipeline-project-old'),
             ('new','driver','old result','pipeline','gui-pipeline-project-latest')",
            [],
        )
        .unwrap();

        assert_eq!(
            load_legacy_pipeline_chat_id(&conn).unwrap(),
            "gui-pipeline-project-latest"
        );
        let nonce_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM meta WHERE key='session_nonce.pipeline'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(nonce_count, 0);
    }

    #[test]
    fn runtime_owner_uses_the_exact_session_id() {
        let mut runtime = ChatAgentRuntime::default();

        set_runtime_owner(
            &mut runtime,
            "gui-pipeline-project-session",
            "pipeline",
            "feature-dev",
            "ship it",
            100,
        );

        assert_eq!(runtime.chat_id, "gui-pipeline-project-session");
        assert_eq!(runtime.surface, "pipeline");
    }

    #[test]
    fn project_writer_lease_preserves_the_existing_owner_when_busy() {
        let mut runtime = ChatAgentRuntime::default();
        claim_runtime_owner(
            &mut runtime,
            "gui-pipeline-first",
            "pipeline",
            "feature-dev",
            "first task",
            100,
        )
        .unwrap();

        let error = claim_runtime_owner(
            &mut runtime,
            "gui-orchestrator-second",
            "orchestrator",
            "",
            "second task",
            101,
        )
        .unwrap_err();

        assert!(error.contains("project"));
        assert_eq!(runtime.chat_id, "gui-pipeline-first");
        assert_eq!(runtime.surface, "pipeline");
        assert_eq!(runtime.pipeline_name, "feature-dev");
        assert_eq!(runtime.task, "first task");
        assert_eq!(runtime.started_at, Some(100));
    }

    #[test]
    fn clear_driver_chat_deletes_chat_and_idle_runtime_tails() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello", "orchestrator", "chat-1").unwrap();
        insert_chat(&conn, "driver", None, "hi", "orchestrator", "chat-1").unwrap();
        let mut rt = ChatAgentRuntime {
            chat_id: "chat-1".into(),
            stdout_tail: "stdout text".into(),
            stderr_tail: "stderr text".into(),
            task: "old task".into(),
            started_at: Some(123),
            ..ChatAgentRuntime::default()
        };

        clear_driver_chat_log(&conn, &mut rt, "orchestrator", "chat-1").unwrap();

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
    fn clearing_one_session_does_not_clear_another_sessions_retained_log() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(
            &conn,
            "you",
            None,
            "hello",
            "orchestrator",
            "gui-orchestrator-current",
        )
        .unwrap();
        let mut rt = ChatAgentRuntime {
            chat_id: "gui-pipeline-retained".into(),
            surface: "pipeline".into(),
            stdout_tail: "pipeline output".into(),
            stderr_tail: "pipeline error".into(),
            task: "pipeline task".into(),
            terminal_status: "failed".into(),
            ..ChatAgentRuntime::default()
        };

        clear_driver_chat_log(&conn, &mut rt, "orchestrator", "gui-orchestrator-current").unwrap();

        assert_eq!(rt.chat_id, "gui-pipeline-retained");
        assert_eq!(rt.stdout_tail, "pipeline output");
        assert_eq!(rt.stderr_tail, "pipeline error");
        assert_eq!(rt.task, "pipeline task");
        assert_eq!(rt.terminal_status, "failed");
    }

    #[test]
    fn new_session_rerolls_chat_id_without_deleting_prior_history() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let root = Path::new("/tmp/musubi-new-session-test");
        let old_nonce = load_or_mint_session_nonce(&conn, "orchestrator").unwrap();
        let old_id = scoped_chat_id(root, "orchestrator", &old_nonce);
        insert_chat(&conn, "you", None, "hello", "orchestrator", &old_id).unwrap();
        let slot = Mutex::new(old_id.clone());
        let viewed = Mutex::new(Some("gui-orchestrator-old-view".to_string()));
        let mut rt = ChatAgentRuntime {
            chat_id: old_id.clone(),
            stdout_tail: "x".into(),
            ..ChatAgentRuntime::default()
        };

        new_driver_session(&conn, &mut rt, &slot, &viewed, root, "orchestrator").unwrap();

        // The live chat_id changed, so the agent replays no prior history.
        let new_id = slot.lock().unwrap().clone();
        assert_ne!(new_id, old_id);
        assert!(new_id.starts_with("gui-orchestrator-"));
        // Prior display history remains durable under the old session.
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM chat_log", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);
        assert_eq!(rt.stdout_tail, "");
        assert!(viewed.lock().unwrap().is_none());
        // The new nonce is persisted, so a restart continues this new session.
        let persisted = load_or_mint_session_nonce(&conn, "orchestrator").unwrap();
        assert_eq!(scoped_chat_id(root, "orchestrator", &persisted), new_id);
    }

    #[test]
    fn select_driver_session_switches_to_existing_project_session() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        let slot = Mutex::new("gui-orchestrator-project-current".to_string());
        let viewed = Mutex::new(Some("gui-orchestrator-project-stale".to_string()));
        let mut rt = ChatAgentRuntime::default();

        select_driver_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "orchestrator",
            "gui-orchestrator-project-old",
        )
        .unwrap();

        assert_eq!(
            slot.lock().unwrap().as_str(),
            "gui-orchestrator-project-old"
        );
        assert!(viewed.lock().unwrap().is_none());
        assert_eq!(
            load_or_mint_session_nonce(&conn, "orchestrator").unwrap(),
            "old"
        );
    }

    #[test]
    fn select_driver_session_rejects_unknown_and_cross_scope_sessions() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('other','you','other request','orchestrator','gui-orchestrator-other-old')",
            [],
        )
        .unwrap();
        let slot = Mutex::new("gui-orchestrator-project-current".to_string());
        let viewed = Mutex::new(None);
        let mut rt = ChatAgentRuntime::default();

        let cross_scope = select_driver_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "orchestrator",
            "gui-orchestrator-other-old",
        )
        .unwrap_err();
        assert!(cross_scope.contains("project"));

        let unknown = select_driver_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "orchestrator",
            "gui-orchestrator-project-missing",
        )
        .unwrap_err();
        assert!(unknown.contains("not found"));

        assert!(viewed.lock().unwrap().is_none());
    }

    #[test]
    fn select_driver_session_browses_history_without_reassigning_busy_runtime() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        store_session_nonce(&conn, "orchestrator", "current").unwrap();
        let slot = Mutex::new("gui-orchestrator-project-current".to_string());
        let viewed = Mutex::new(None);
        let mut rt = ChatAgentRuntime {
            running: true,
            chat_id: "gui-orchestrator-project-current".to_string(),
            ..ChatAgentRuntime::default()
        };

        select_driver_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "orchestrator",
            "gui-orchestrator-project-old",
        )
        .unwrap();

        assert_eq!(
            slot.lock().unwrap().as_str(),
            "gui-orchestrator-project-current"
        );
        assert_eq!(rt.chat_id, "gui-orchestrator-project-current");
        assert_eq!(
            viewed.lock().unwrap().as_deref(),
            Some("gui-orchestrator-project-old")
        );
        assert_eq!(
            load_or_mint_session_nonce(&conn, "orchestrator").unwrap(),
            "current"
        );
    }

    #[test]
    fn resolve_send_session_promotes_idle_viewed_history() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        let slot = Mutex::new("gui-orchestrator-project-current".to_string());
        let viewed = Mutex::new(Some("gui-orchestrator-project-old".to_string()));
        let mut rt = ChatAgentRuntime::default();

        let resolved = resolve_orchestrator_send_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "gui-orchestrator-project-old",
        )
        .unwrap();

        assert_eq!(resolved, "gui-orchestrator-project-old");
        assert_eq!(slot.lock().unwrap().as_str(), resolved.as_str());
        assert!(viewed.lock().unwrap().is_none());
    }

    #[test]
    fn resolve_send_session_refuses_busy_historical_promotion() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let slot = Mutex::new("gui-orchestrator-project-current".to_string());
        let viewed = Mutex::new(Some("gui-orchestrator-project-old".to_string()));
        let mut rt = ChatAgentRuntime {
            running: true,
            chat_id: "gui-orchestrator-project-current".to_string(),
            ..ChatAgentRuntime::default()
        };

        let error = resolve_orchestrator_send_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            "gui-orchestrator-project-old",
        )
        .unwrap_err();

        assert!(error.contains("running"));
        assert_eq!(
            slot.lock().unwrap().as_str(),
            "gui-orchestrator-project-current"
        );
        assert_eq!(
            viewed.lock().unwrap().as_deref(),
            Some("gui-orchestrator-project-old")
        );
    }

    #[test]
    fn atomic_send_boundary_claims_runtime_and_persists_to_exact_history() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = Some("gui-orchestrator-project-old".to_string());
        let mut rt = ChatAgentRuntime::default();

        let resolved = prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-old",
            "continue",
            None,
            42,
        )
        .unwrap();

        assert_eq!(resolved, "gui-orchestrator-project-old");
        assert_eq!(active, resolved);
        assert_eq!(viewed, None);
        assert!(rt.running);
        assert_eq!(rt.chat_id, resolved);
        assert_eq!(rt.task, "continue");
        let rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE role='you' AND text='continue' AND chat_id=?1",
                [&resolved],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 1);
    }

    #[test]
    fn competing_runtime_claim_refuses_send_without_writes_or_ownership_changes() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        let conn = Arc::new(Mutex::new(conn));
        let ownership = Arc::new(Mutex::new((
            ChatAgentRuntime::default(),
            "gui-orchestrator-project-current".to_string(),
            Some("gui-orchestrator-project-old".to_string()),
        )));
        let competing_ownership = ownership.clone();
        let (claimed_tx, claimed_rx) = std::sync::mpsc::channel();
        let competitor = std::thread::spawn(move || {
            let mut owned = competing_ownership.lock().unwrap();
            claim_runtime_owner(
                &mut owned.0,
                "gui-pipeline-project-live",
                "pipeline",
                "feature-dev",
                "competing task",
                41,
            )
            .unwrap();
            claimed_tx.send(()).unwrap();
        });
        claimed_rx.recv().unwrap();
        competitor.join().unwrap();

        let mut owned = ownership.lock().unwrap();
        let mut conn = conn.lock().unwrap();
        let (rt, active, viewed) = &mut *owned;
        let error = prepare_orchestrator_send(
            &mut conn,
            rt,
            active,
            viewed,
            "gui-orchestrator-project-old",
            "must not persist",
            None,
            42,
        )
        .unwrap_err();

        assert!(error.contains("active pipeline run"));
        assert_eq!(*active, "gui-orchestrator-project-current");
        assert_eq!(viewed.as_deref(), Some("gui-orchestrator-project-old"));
        assert_eq!(rt.chat_id, "gui-pipeline-project-live");
        let rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE text='must not persist'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 0);
    }

    #[test]
    fn nonce_write_failure_leaves_send_ownership_unchanged() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        conn.execute("DROP TABLE meta", []).unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = Some("gui-orchestrator-project-old".to_string());
        let mut rt = ChatAgentRuntime::default();

        let error = prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-old",
            "must not launch",
            None,
            42,
        )
        .unwrap_err();

        assert!(error.contains("meta"));
        assert_eq!(active, "gui-orchestrator-project-current");
        assert_eq!(viewed.as_deref(), Some("gui-orchestrator-project-old"));
        assert!(!rt.running);
        let rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE text='must not launch'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 0);
    }

    #[test]
    fn chat_display_loads_only_the_active_session() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES\
             ('old','you','old request','orchestrator','old-chat'),\
             ('new','you','new request','orchestrator','new-chat')",
            [],
        )
        .unwrap();

        let messages =
            musubi_data::load_chat_for_session(&conn, "orchestrator", "new-chat").unwrap();

        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0].text, "new request");
    }

    #[test]
    fn session_nonce_is_stable_across_reloads() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let first = load_or_mint_session_nonce(&conn, "orchestrator").unwrap();
        let second = load_or_mint_session_nonce(&conn, "orchestrator").unwrap();
        assert_eq!(first, second, "restart must continue the same session");
        // Surfaces are independent sessions.
        let pipe = load_or_mint_session_nonce(&conn, "pipeline").unwrap();
        assert_ne!(first, pipe);
    }

    #[test]
    fn new_session_refuses_while_agent_runs() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let slot = Mutex::new("gui-orchestrator-abc-1".to_string());
        insert_chat(
            &conn,
            "you",
            None,
            "hello",
            "orchestrator",
            "gui-orchestrator-abc-1",
        )
        .unwrap();
        let mut rt = ChatAgentRuntime {
            running: true,
            ..ChatAgentRuntime::default()
        };
        let viewed = Mutex::new(None);

        let err = new_driver_session(
            &conn,
            &mut rt,
            &slot,
            &viewed,
            Path::new("/tmp/x"),
            "orchestrator",
        )
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
        insert_chat(&conn, "you", None, "hello", "orchestrator", "chat-1").unwrap();
        let mut rt = ChatAgentRuntime {
            running: true,
            ..ChatAgentRuntime::default()
        };

        let err = clear_driver_chat_log(&conn, &mut rt, "orchestrator", "chat-1").unwrap_err();

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
    fn artifact_links_dedup_mentioned_and_manifest_same_file() {
        // Regression: the same file arrives from the answer-mention scan
        // (canonicalized) and from the manifest (raw). Comparing raw PathBufs
        // missed the match and listed it twice; canonicalizing both dedups it.
        let root = std::env::temp_dir().join(format!("musubi-artifact-dup-{}", epoch_secs()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("japan-dashboard.html");
        std::fs::write(&file, "<html></html>").unwrap();

        // Manifest passes the RAW (non-canonicalized) path; the answer mentions
        // the same file by name (scan canonicalizes it).
        let text = append_artifact_links("Created `japan-dashboard.html`.", &root, &[file.clone()]);

        let occurrences = text.matches("musubi-artifact:").count();
        assert_eq!(
            occurrences, 1,
            "artifact should be listed once, got:\n{text}"
        );
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
    fn artifact_open_failures_are_visible_but_successes_are_silent() {
        let source = include_str!("lib.rs");
        let removed_success_helper = ["fn ", "artifact_opened_message"].concat();
        assert!(!source.contains(&removed_success_helper));
        let failed = artifact_open_failed_message("missing.html", "cannot open artifact");
        assert!(failed.contains("Could not open artifact"));
        assert!(failed.contains("missing.html"));
        assert!(failed.contains("cannot open artifact"));
    }

    fn runnable_pipeline(name: &str) -> musubi_data::PipelineCatalogEntry {
        musubi_data::PipelineCatalogEntry {
            name: name.into(),
            description: "Runnable recipe".into(),
            stages: vec!["plan".into(), "build".into()],
            runnable: true,
            blocked_reason: String::new(),
        }
    }

    #[test]
    fn orchestrator_pipeline_direct_launch_resolves_trimmed_task_without_pipeline() {
        assert_eq!(
            resolve_orchestrator_launch("  ship it  ", "direct", "", &[]).unwrap(),
            ("ship it".to_string(), None)
        );
    }

    #[test]
    fn orchestrator_pipeline_launch_resolves_registered_runnable_recipe() {
        let catalog = vec![runnable_pipeline("feature-dev")];

        assert_eq!(
            resolve_orchestrator_launch("  ship it  ", "pipeline", "feature-dev", &catalog)
                .unwrap(),
            ("ship it".to_string(), Some("feature-dev".to_string()))
        );
    }

    #[test]
    fn orchestrator_pipeline_invalid_launches_fail_closed_before_send_mutation() {
        let mut blocked = runnable_pipeline("blocked");
        blocked.runnable = false;
        blocked.blocked_reason = "recipe is blocked".into();
        let catalog = vec![runnable_pipeline("feature-dev"), blocked];

        for (task, mode, pipeline) in [
            ("", "direct", ""),
            ("ship", "", ""),
            ("ship", "unknown", ""),
            ("ship", "pipeline", "missing"),
            ("ship", "pipeline", "../unsafe"),
            ("ship", "pipeline", "blocked"),
        ] {
            assert!(resolve_orchestrator_launch(task, mode, pipeline, &catalog).is_err());
        }
    }

    #[test]
    fn orchestrator_pipeline_send_claims_exact_orchestrator_session() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = None;
        let mut rt = ChatAgentRuntime::default();

        let resolved = prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-current",
            "ship it",
            Some("feature-dev"),
            42,
        )
        .unwrap();

        assert_eq!(resolved, "gui-orchestrator-project-current");
        assert_eq!(rt.chat_id, resolved);
        assert_eq!(rt.surface, "orchestrator");
        assert_eq!(rt.pipeline_name, "feature-dev");
    }

    #[test]
    fn orchestrator_pipeline_historical_idle_send_atomically_promotes_exact_viewed_id() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,text,surface,chat_id) VALUES
             ('old','you','old request','orchestrator','gui-orchestrator-project-old')",
            [],
        )
        .unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = Some("gui-orchestrator-project-old".to_string());
        let mut rt = ChatAgentRuntime::default();

        let resolved = prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-old",
            "continue",
            Some("feature-dev"),
            42,
        )
        .unwrap();

        assert_eq!(resolved, "gui-orchestrator-project-old");
        assert_eq!(active, resolved);
        assert_eq!(viewed, None);
        assert_eq!(rt.chat_id, resolved);
        assert_eq!(rt.pipeline_name, "feature-dev");
    }

    #[test]
    fn orchestrator_pipeline_busy_owner_rejects_without_changing_owner_or_rows() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = None;
        let mut rt = ChatAgentRuntime::default();
        claim_runtime_owner(
            &mut rt,
            "gui-orchestrator-project-owner",
            "orchestrator",
            "",
            "owner task",
            41,
        )
        .unwrap();

        assert!(prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-current",
            "must not send",
            Some("feature-dev"),
            42,
        )
        .is_err());

        assert_eq!(rt.chat_id, "gui-orchestrator-project-owner");
        assert_eq!(rt.pipeline_name, "");
        let rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE text='must not send'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(rows, 0);
    }

    #[test]
    fn orchestrator_pipeline_non_owner_session_cannot_cancel_runtime() {
        let rt = ChatAgentRuntime {
            running: true,
            chat_id: "gui-orchestrator-project-owner".into(),
            ..ChatAgentRuntime::default()
        };

        assert!(ensure_runtime_owner(&rt, "gui-orchestrator-project-viewed").is_err());
        assert!(ensure_runtime_owner(&rt, "gui-orchestrator-project-owner").is_ok());
    }

    #[test]
    fn orchestrator_pipeline_send_writes_only_orchestrator_surface() {
        let mut conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let mut active = "gui-orchestrator-project-current".to_string();
        let mut viewed = None;
        let mut rt = ChatAgentRuntime::default();

        prepare_orchestrator_send(
            &mut conn,
            &mut rt,
            &mut active,
            &mut viewed,
            "gui-orchestrator-project-current",
            "pipeline task",
            Some("feature-dev"),
            42,
        )
        .unwrap();
        insert_chat(
            &conn,
            "driver",
            None,
            "pipeline reply",
            &rt.surface,
            &rt.chat_id,
        )
        .unwrap();

        let orchestrator_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE surface='orchestrator' AND chat_id=?1",
                [&active],
                |row| row.get(0),
            )
            .unwrap();
        let pipeline_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE surface='pipeline'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(orchestrator_rows, 2);
        assert_eq!(pipeline_rows, 0);
    }

    #[test]
    fn orchestrator_pipeline_ipc_and_active_routes_are_registered_without_legacy_mutations() {
        let source = include_str!("lib.rs");
        assert!(source.contains("fn load_pipeline_recipe("));
        assert!(source.contains("fn validate_pipeline_recipe("));
        assert!(source.contains("fn save_pipeline_recipe("));
        let handler_block = source
            .split_once("invoke_handler(tauri::generate_handler![")
            .unwrap()
            .1
            .split_once("])")
            .unwrap()
            .0;
        for handler in [
            "load_pipeline_recipe",
            "validate_pipeline_recipe",
            "save_pipeline_recipe",
        ] {
            assert!(handler_block.contains(handler));
        }
        assert!(!source.contains("\"send_pipeline_task\" =>"));
        assert!(!source.contains("\"pipeline_hint\" =>"));
        assert!(!source.contains(&["pipeline_chat_id", ": Mutex"].concat()));
        assert!(!source
            .contains(&["load_or_mint_session_nonce(&opened.conn, ", "\"pipeline\")"].concat()));
        assert!(source.contains(&["fn load_legacy_", "pipeline_chat_id("].concat()));
    }

    fn copy_tree(source: &Path, target: &Path) {
        std::fs::create_dir_all(target).unwrap();
        for entry in std::fs::read_dir(source).unwrap() {
            let entry = entry.unwrap();
            let destination = target.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                copy_tree(&entry.path(), &destination);
            } else {
                std::fs::copy(entry.path(), destination).unwrap();
            }
        }
    }

    #[test]
    fn orchestrator_pipeline_recipe_wrappers_delegate_and_refresh_catalog() {
        let source_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let project_root = std::env::temp_dir().join(format!(
            "musubi-tauri-recipe-ipc-{}-{}",
            std::process::id(),
            epoch_secs()
        ));
        copy_tree(&source_root.join(".github"), &project_root.join(".github"));

        let mut recipe = load_pipeline_recipe_at(&project_root, "dev-lite").unwrap();
        recipe.name = "ipc-test".into();
        let findings = validate_pipeline_recipe_at(&project_root, &recipe);
        assert!(!findings.iter().any(|finding| finding.severity == "error"));
        let saved = save_pipeline_recipe_at(&project_root, &recipe);
        assert!(saved.saved, "{}", saved.error);
        assert!(saved.catalog_refreshed);
        assert!(musubi_data::read_studio_pipeline_catalog(&project_root)
            .iter()
            .any(|entry| entry.name == "ipc-test"));

        std::fs::remove_dir_all(project_root).unwrap();
    }

    #[test]
    fn orchestrator_pipeline_review_completion_append_stays_with_captured_owner() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        let launch_surface = "orchestrator".to_string();
        let launch_chat_id = "gui-orchestrator-project-old".to_string();
        let mut runtime = ChatAgentRuntime {
            chat_id: launch_chat_id.clone(),
            surface: launch_surface.clone(),
            ..ChatAgentRuntime::default()
        };
        set_runtime_owner(
            &mut runtime,
            "gui-orchestrator-project-new",
            "orchestrator",
            "feature-dev",
            "new task",
            43,
        );

        append_driver_chat_to_conn(
            &conn,
            &launch_surface,
            &launch_chat_id,
            None,
            "old completion",
        )
        .unwrap();

        let old_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE chat_id=?1 AND text='old completion'",
                [&launch_chat_id],
                |row| row.get(0),
            )
            .unwrap();
        let new_rows: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_log WHERE chat_id=?1 AND text='old completion'",
                [&runtime.chat_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(old_rows, 1);
        assert_eq!(new_rows, 0);
    }

    #[test]
    fn orchestrator_pipeline_review_cancel_rejects_supplied_owner_while_viewing_history() {
        let runtime = ChatAgentRuntime {
            running: true,
            chat_id: "gui-orchestrator-project-owner".into(),
            ..ChatAgentRuntime::default()
        };

        let error = authorize_cancel_request(
            "gui-orchestrator-project-owner",
            "gui-orchestrator-project-owner",
            Some("gui-orchestrator-project-history"),
            &runtime,
        )
        .unwrap_err();

        assert!(error.contains("displayed"));
        assert!(authorize_cancel_request(
            "gui-orchestrator-project-owner",
            "gui-orchestrator-project-owner",
            None,
            &runtime,
        )
        .is_ok());
    }

    #[test]
    fn orchestrator_pipeline_review_dispatch_direct_preserves_prepare_launch_identity() {
        let events = std::cell::RefCell::new(Vec::new());

        dispatch_orchestrator_send(
            " ship ",
            "gui-orchestrator-project-exact",
            "direct",
            "ignored",
            &[],
            |task, requested, pipeline| {
                events.borrow_mut().push(format!(
                    "prepare:{task}:{requested}:{}",
                    pipeline.unwrap_or("none")
                ));
                Ok(requested.to_string())
            },
            |task, chat_id, pipeline| {
                events.borrow_mut().push(format!(
                    "launch:{task}:{chat_id}:{}",
                    pipeline.unwrap_or("none")
                ));
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            events.into_inner(),
            vec![
                "prepare:ship:gui-orchestrator-project-exact:none",
                "launch:ship:gui-orchestrator-project-exact:none",
            ]
        );
    }

    #[test]
    fn orchestrator_pipeline_review_dispatch_pipeline_validates_before_exact_launch() {
        let events = std::cell::RefCell::new(Vec::new());
        let catalog = vec![runnable_pipeline("feature-dev")];

        dispatch_orchestrator_send(
            " ship ",
            "gui-orchestrator-project-exact",
            "pipeline",
            "feature-dev",
            &catalog,
            |task, requested, pipeline| {
                events.borrow_mut().push(format!(
                    "prepare:{task}:{requested}:{}",
                    pipeline.unwrap_or("none")
                ));
                Ok(requested.to_string())
            },
            |task, chat_id, pipeline| {
                events.borrow_mut().push(format!(
                    "launch:{task}:{chat_id}:{}",
                    pipeline.unwrap_or("none")
                ));
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            events.into_inner(),
            vec![
                "prepare:ship:gui-orchestrator-project-exact:feature-dev",
                "launch:ship:gui-orchestrator-project-exact:feature-dev",
            ]
        );
    }

    #[test]
    fn orchestrator_pipeline_cancel_snapshots_displayed_session_under_runtime_lock() {
        let runtime = Arc::new(Mutex::new(ChatAgentRuntime {
            running: true,
            chat_id: "gui-orchestrator-project-owner".into(),
            ..ChatAgentRuntime::default()
        }));
        let active = Arc::new(Mutex::new("gui-orchestrator-project-owner".to_string()));
        let viewed = Arc::new(Mutex::new(None));
        let runtime_guard = runtime.lock().unwrap();
        let worker_runtime = runtime.clone();
        let worker_active = active.clone();
        let worker_viewed = viewed.clone();
        let (before_lock_tx, before_lock_rx) = std::sync::mpsc::channel();

        let worker = std::thread::spawn(move || {
            prepare_cancel_claim_with_hook(
                &worker_runtime,
                &worker_active,
                &worker_viewed,
                "gui-orchestrator-project-owner",
                || before_lock_tx.send(()).unwrap(),
            )
        });
        before_lock_rx.recv().unwrap();
        *viewed.lock().unwrap() = Some("gui-orchestrator-project-history".into());
        drop(runtime_guard);

        let error = match worker.join().unwrap() {
            Err(error) => error,
            Ok(_) => panic!("stale owner snapshot authorized cancellation"),
        };
        assert!(error.contains("displayed"));
        assert!(!runtime.lock().unwrap().cancel_requested);
    }
}
