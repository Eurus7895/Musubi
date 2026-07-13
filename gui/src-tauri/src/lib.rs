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
    // Optional read-only history focus. Never owns a running process or future
    // messages; it only chooses which orchestrator chat snapshot is displayed.
    viewed_orchestrator_chat_id: Mutex<Option<String>>,
    // Pipeline studio session id (gui-pipeline-*-<nonce>). Same single process
    // slot, but its own conversation history + run scope.
    pipeline_chat_id: Mutex<String>,
}

#[derive(Default)]
struct ChatAgentRuntime {
    running: bool,
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
    store_session_nonce(conn, surface, &nonce);
    let new_id = scoped_chat_id(project_root, surface, &nonce);
    *chat_id_slot.lock().map_err(|e| e.to_string())? = new_id;
    *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? = None;
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

    *chat_id_slot.lock().map_err(|e| e.to_string())? = requested_chat_id.to_string();
    *viewed_chat_id_slot.lock().map_err(|e| e.to_string())? = None;
    store_session_nonce(conn, surface, requested_nonce);
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

fn artifact_open_failed_message(raw_path: &str, error: &str) -> String {
    format!("Could not open artifact.\n\nPath: `{raw_path}`\n\n{error}")
}

fn append_driver_chat(app: &tauri::AppHandle, tone: Option<&str>, text: &str) {
    let state = app.state::<AppState>();
    let Ok((surface, chat_id)) = state
        .chat_agent
        .lock()
        .map(|rt| (surface_arg(&rt.surface).to_string(), rt.chat_id.clone()))
    else {
        return;
    };
    if chat_id.is_empty() {
        return;
    }
    let Ok(conn) = state.db.lock() else {
        return;
    };
    let _ = insert_chat(&conn, "driver", tone, text, &surface, &chat_id);
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
        claim_runtime_owner(
            &mut rt,
            chat_id,
            surface,
            pipeline_name.unwrap_or_default(),
            &task_text,
            started_at,
        )?;
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
        musubi_data::AgentLaunchScope {
            chat_id: Some(chat_id),
            pipeline_name,
        },
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
    let orchestrator_chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
    let viewed_orchestrator_chat_id = state
        .viewed_orchestrator_chat_id
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    let pipeline_chat_id = state
        .pipeline_chat_id
        .lock()
        .map_err(|e| e.to_string())?
        .clone();
    let displayed_orchestrator_chat_id = viewed_orchestrator_chat_id
        .as_deref()
        .unwrap_or(&orchestrator_chat_id);
    st.chat =
        musubi_data::load_chat_for_session(&conn, "orchestrator", displayed_orchestrator_chat_id)
            .map_err(|e| e.to_string())?;
    st.pipe_chat = musubi_data::load_chat_for_session(&conn, "pipeline", &pipeline_chat_id)
        .map_err(|e| e.to_string())?;
    st.orchestrator_chat_id = orchestrator_chat_id;
    st.viewed_orchestrator_chat_id = viewed_orchestrator_chat_id.unwrap_or_default();
    st.pipeline_chat_id = pipeline_chat_id;
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
            let chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
            {
                let conn = state.db.lock().map_err(|e| e.to_string())?;
                insert_chat(&conn, "you", None, &text, "orchestrator", &chat_id)?;
            }
            if let Err(e) =
                start_chat_agent(app, state.inner(), text, &chat_id, "orchestrator", None)
            {
                let conn = state.db.lock().map_err(|err| err.to_string())?;
                insert_chat(&conn, "driver", Some("deny"), &e, "orchestrator", &chat_id)?;
            }
        }
        // Pipeline studio session: same single process slot, its own chat_id +
        // conversation history + run scope.
        "send_pipeline_task" => {
            let catalog = musubi_data::read_studio_pipeline_catalog(&state.project_root);
            let requested_text = str_arg(0);
            let requested_pipeline = str_arg(1);
            let chat_id = state
                .pipeline_chat_id
                .lock()
                .map_err(|e| e.to_string())?
                .clone();
            let (text, pipeline_name) =
                match prepare_pipeline_launch(&requested_text, &requested_pipeline, &catalog) {
                    Ok(prepared) => prepared,
                    Err(error) => {
                        let conn = state.db.lock().map_err(|e| e.to_string())?;
                        insert_chat(&conn, "driver", Some("deny"), &error, "pipeline", &chat_id)?;
                        return Ok(());
                    }
                };
            {
                let conn = state.db.lock().map_err(|e| e.to_string())?;
                insert_chat(&conn, "you", None, &text, "pipeline", &chat_id)?;
            }
            if let Err(e) = start_chat_agent(
                app,
                state.inner(),
                text,
                &chat_id,
                "pipeline",
                Some(&pipeline_name),
            ) {
                let conn = state.db.lock().map_err(|err| err.to_string())?;
                insert_chat(&conn, "driver", Some("deny"), &e, "pipeline", &chat_id)?;
            }
        }
        "pipeline_hint" => {
            let text = str_arg(0);
            let chat_id = state.chat_id.lock().map_err(|e| e.to_string())?.clone();
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            if !text.trim().is_empty() {
                insert_chat(&conn, "you", None, &text, "orchestrator", &chat_id)?;
            }
            insert_chat(
                &conn,
                "driver",
                None,
                "Choose a pipeline preset in Pipeline studio before running. A bare `pipeline` command does not start an agent or consume model tokens.",
                "orchestrator",
                &chat_id,
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
            let surface = surface_arg(&str_arg(0));
            let chat_id = if surface == "pipeline" {
                state.pipeline_chat_id.lock()
            } else {
                state.chat_id.lock()
            }
            .map_err(|e| e.to_string())?
            .clone();
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            clear_driver_chat_log(&conn, &mut rt, surface, &chat_id)?;
        }
        // Fresh session: re-mint the surface's chat_id so the agent's replay
        // history starts empty. Old turns stay under the previous chat_id.
        "new_session" => {
            let surface = surface_arg(&str_arg(0));
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            let slot = if surface == "pipeline" {
                &state.pipeline_chat_id
            } else {
                &state.chat_id
            };
            new_driver_session(
                &conn,
                &mut rt,
                slot,
                &state.viewed_orchestrator_chat_id,
                &state.project_root,
                surface,
            )?;
        }
        "open_artifact" => {
            let raw_path = str_arg(0);
            let surface = surface_arg(&str_arg(1));
            match open_workspace_path(&state.project_root, &raw_path) {
                Ok(_) => {}
                Err(e) => {
                    let chat_id = if surface == "pipeline" {
                        state.pipeline_chat_id.lock()
                    } else {
                        state.chat_id.lock()
                    }
                    .map_err(|err| err.to_string())?
                    .clone();
                    let conn = state.db.lock().map_err(|err| err.to_string())?;
                    insert_chat(
                        &conn,
                        "driver",
                        Some("deny"),
                        &artifact_open_failed_message(&raw_path, &e),
                        surface,
                        &chat_id,
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
    // Continue the persisted session for each surface (option a: restart resumes
    // the current session); mint on first use.
    let chat_nonce = load_or_mint_session_nonce(&opened.conn, "orchestrator");
    let pipe_nonce = load_or_mint_session_nonce(&opened.conn, "pipeline");
    let chat_id = scoped_chat_id(&opened.project_root, "orchestrator", &chat_nonce);
    let pipeline_chat_id = scoped_chat_id(&opened.project_root, "pipeline", &pipe_nonce);
    // Pre-session rows can only belong to the session that was active when the
    // migration ran. Backfill once; future rows are written with their owner.
    let _ = opened.conn.execute(
        "UPDATE chat_log SET chat_id=?1 WHERE surface='orchestrator' AND chat_id=''",
        [&chat_id],
    );
    let _ = opened.conn.execute(
        "UPDATE chat_log SET chat_id=?1 WHERE surface='pipeline' AND chat_id=''",
        [&pipeline_chat_id],
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

#[cfg(test)]
mod tests {
    use super::*;

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
        let old_nonce = load_or_mint_session_nonce(&conn, "orchestrator");
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
        let persisted = load_or_mint_session_nonce(&conn, "orchestrator");
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
        assert_eq!(load_or_mint_session_nonce(&conn, "orchestrator"), "old");
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
        store_session_nonce(&conn, "orchestrator", "current");
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
        assert_eq!(load_or_mint_session_nonce(&conn, "orchestrator"), "current");
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
