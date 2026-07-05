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

use std::collections::hash_map::DefaultHasher;
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

use rusqlite::Connection;
use tauri::{Emitter, Manager};

struct AppState {
    db: Mutex<Connection>,
    paused: AtomicBool,
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
    chat_agent: Arc<Mutex<ChatAgentRuntime>>,
    chat_id: String,
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

fn clock_label(epoch: i64) -> String {
    let sod = ((epoch % 86_400) + 86_400) % 86_400;
    format!("{:02}:{:02}:{:02}", sod / 3600, (sod % 3600) / 60, sod % 60)
}

fn insert_chat(
    conn: &Connection,
    role: &str,
    tone: Option<&str>,
    text: &str,
) -> Result<(), String> {
    conn.execute(
        "INSERT INTO chat_log(ts,role,tone,text) VALUES(?1,?2,?3,?4)",
        rusqlite::params![clock_label(epoch_secs()), role, tone, text],
    )
    .map(|_| ())
    .map_err(|e| e.to_string())
}

fn clear_driver_chat_log(conn: &Connection, rt: &mut ChatAgentRuntime) -> Result<(), String> {
    if rt.running {
        return Err(
            "Cannot clear chat while the agent is running. Cancel or wait for it to finish.".into(),
        );
    }
    conn.execute("DELETE FROM chat_log", [])
        .map_err(|e| e.to_string())?;
    rt.stdout_tail.clear();
    rt.stderr_tail.clear();
    rt.task.clear();
    rt.started_at = None;
    rt.cancel_requested = false;
    Ok(())
}

fn workspace_root_from_musubi_config(path: &std::path::Path) -> Option<PathBuf> {
    let dir = path.parent()?;
    if dir.file_name().and_then(|s| s.to_str()) != Some(".musubi") {
        return None;
    }
    dir.parent().map(PathBuf::from)
}

fn scoped_chat_id(project_root: &Path) -> String {
    let root = project_root
        .canonicalize()
        .unwrap_or_else(|_| project_root.to_path_buf())
        .to_string_lossy()
        .to_lowercase();
    let mut hasher = DefaultHasher::new();
    root.hash(&mut hasher);
    format!("gui-orchestrator-{:016x}", hasher.finish())
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
                    '`' | '"' | '\'' | '(' | ')' | '[' | ']' | '{' | '}' | '<' | '>' | ','
                        | ';' | ':' | '!' | '?'
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
        format!("{summary}\n\n[Open full process log](musubi-log:last)")
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

fn open_workspace_path(project_root: &Path, raw_path: &str) -> Result<(), String> {
    let path = PathBuf::from(raw_path);
    let canonical = path
        .canonicalize()
        .map_err(|e| format!("cannot open artifact: {e}"))?;
    let root = project_root
        .canonicalize()
        .map_err(|e| format!("cannot resolve project root: {e}"))?;
    if !canonical.starts_with(&root) {
        return Err("refusing to open a path outside the project root".into());
    }
    let mut cmd = if cfg!(windows) {
        let mut c = std::process::Command::new("explorer.exe");
        c.arg(&canonical);
        c
    } else if cfg!(target_os = "macos") {
        let mut c = std::process::Command::new("open");
        c.arg(&canonical);
        c
    } else {
        let mut c = std::process::Command::new("xdg-open");
        c.arg(&canonical);
        c
    };
    cmd.spawn()
        .map(|_| ())
        .map_err(|e| format!("failed to open artifact: {e}"))
}

fn append_driver_chat(app: &tauri::AppHandle, tone: Option<&str>, text: &str) {
    let state = app.state::<AppState>();
    let Ok(conn) = state.db.lock() else {
        return;
    };
    let _ = insert_chat(&conn, "driver", tone, text);
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
        Some(&state.chat_id),
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
                let log = process_log(&stdout_tail, &stderr_tail);
                if cancelled {
                    append_driver_chat(
                        &app,
                        Some("deny"),
                        &append_process_log_link("Agent cancelled by user.", &log),
                    );
                    break;
                }
                let code = status.code().unwrap_or(-1);
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
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
}

fn open_configured_db() -> OpenedDb {
    let env = musubi_data::current_env_map();
    let project_root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if let Some(resolved) = musubi_data::resolve_audit_db_path(&env, &project_root) {
        let conn = Connection::open(&resolved.path).expect("open Musubi audit db");
        let _ = musubi_data::init_schema(&conn);
        eprintln!(
            "[musubi] reading audit.db at {} ({})",
            resolved.path.display(),
            resolved.source
        );
        return OpenedDb {
            conn,
            project_root,
            audit_db: Some(resolved),
        };
    }

    let conn = open_db();
    eprintln!("[musubi] no audit.db source found; using empty in-memory state");
    OpenedDb {
        conn,
        project_root,
        audit_db: None,
    }
}

fn snapshot(state: &AppState) -> Result<musubi_data::State, String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    let mut st = musubi_data::load_state(&conn).map_err(|e| e.to_string())?;
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
                insert_chat(&conn, "you", None, &text)?;
            }
            if let Err(e) = start_chat_agent(app, state.inner(), text) {
                let conn = state.db.lock().map_err(|err| err.to_string())?;
                insert_chat(&conn, "driver", Some("deny"), &e)?;
            }
        }
        "pipeline_hint" => {
            let text = str_arg(0);
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            if !text.trim().is_empty() {
                insert_chat(&conn, "you", None, &text)?;
            }
            insert_chat(
                &conn,
                "driver",
                None,
                "Choose a pipeline preset in Pipeline studio before running. A bare `pipeline` command does not start an agent or consume model tokens.",
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
            let mut rt = state.chat_agent.lock().map_err(|e| e.to_string())?;
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            clear_driver_chat_log(&conn, &mut rt)?;
        }
        "open_artifact" => {
            open_workspace_path(&state.project_root, &str_arg(0))?;
        }
        // Pipelines are launched by asking the driver in chat (the root agent
        // spawns them via musubi_spawn_pipeline), reusing the single agent slot
        // and the Orchestrator session input — there is no separate run action.
        // Studio authoring actions remain client-side scaffolding for now.
        "add_pipe" | "remove_pipe" | "move_pipe" | "clear_pipe" | "load_preset" => {
            eprintln!("[musubi] pipeline studio action '{kind}' - client-side only");
        }
        other => eprintln!("[musubi] unknown action: {other}"),
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let opened = open_configured_db();
    let chat_id = scoped_chat_id(&opened.project_root);
    tauri::Builder::default()
        .manage(AppState {
            db: Mutex::new(opened.conn),
            paused: AtomicBool::new(false),
            project_root: opened.project_root,
            audit_db: opened.audit_db,
            chat_agent: Arc::new(Mutex::new(ChatAgentRuntime::default())),
            chat_id,
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
        insert_chat(&conn, "you", None, "hello").unwrap();
        insert_chat(&conn, "driver", None, "hi").unwrap();
        let mut rt = ChatAgentRuntime {
            stdout_tail: "stdout text".into(),
            stderr_tail: "stderr text".into(),
            task: "old task".into(),
            started_at: Some(123),
            ..ChatAgentRuntime::default()
        };

        clear_driver_chat_log(&conn, &mut rt).unwrap();

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
    fn clear_driver_chat_refuses_while_agent_runs() {
        let conn = Connection::open_in_memory().unwrap();
        musubi_data::init_schema(&conn).unwrap();
        insert_chat(&conn, "you", None, "hello").unwrap();
        let mut rt = ChatAgentRuntime {
            running: true,
            ..ChatAgentRuntime::default()
        };

        let err = clear_driver_chat_log(&conn, &mut rt).unwrap_err();

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

        let text = append_artifact_links(
            "Open `weather-dashboard.html` in your browser.",
            &root,
            &[],
        );

        assert!(text.contains("[weather-dashboard.html](musubi-artifact:"));
        let _ = std::fs::remove_file(file);
        let _ = std::fs::remove_dir(root);
    }
}
