//! musubi-tier: substrate
//!
//! Tauri desktop shell for the Musubi console.
//!
//! Bridges the webkit-free `musubi-data` core to the React UI:
//!   - `get_state`        → snapshot of the console state
//!   - `action`           → mutating actions (chat, profile, pipeline)
//!   - `state://update`   → emitted ~1×/s by a background poller as audit.db grows
//!
//! Data source: the configured Musubi `audit.db`. When no database can be
//! resolved, the console opens an empty in-memory schema for first-run setup.

use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::Connection;
use tauri::{Emitter, Manager};

struct AppState {
    db: Mutex<Connection>,
    paused: AtomicBool,
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
    task: Arc<Mutex<TaskRuntime>>,
}

/// The single active `agent "<task>"` child process plus its serializable
/// status. Output tails are bounded; the audit DB — not this overlay — stays
/// the orchestration source of truth.
#[derive(Default)]
struct TaskRuntime {
    status: musubi_data::TaskLauncherStatus,
    child: Option<Arc<Mutex<Child>>>,
}

/// Keep the last 64 KiB per stream.
const TAIL_CAP: usize = 64 * 1024;

fn epoch_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

enum TailStream {
    Stdout,
    Stderr,
}

/// Drain a child stream into the bounded tail from a background thread.
fn pump_stream(
    stream: impl Read + Send + 'static,
    shared: Arc<Mutex<TaskRuntime>>,
    which: TailStream,
) {
    std::thread::spawn(move || {
        let mut reader = stream;
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let chunk = String::from_utf8_lossy(&buf[..n]).into_owned();
                    if let Ok(mut rt) = shared.lock() {
                        let tail = match which {
                            TailStream::Stdout => &mut rt.status.stdout_tail,
                            TailStream::Stderr => &mut rt.status.stderr_tail,
                        };
                        musubi_data::push_bounded_tail(tail, &chunk, TAIL_CAP);
                    }
                }
            }
        }
    });
}

/// Spawn one governed `agent` process for `task_text`. Rejects an empty task
/// and refuses to start while another task is running. Never blocks the Tauri
/// event loop: readers and the exit waiter run on background threads.
fn start_task(state: &AppState, task_text: String, profile: String) -> Result<(), String> {
    // Resolve everything that needs the db lock *before* taking the task lock
    // so no code path ever holds both at once.
    let default_profile = {
        let conn = state.db.lock().map_err(|e| e.to_string())?;
        musubi_data::read_active_profile(&conn)
    };
    let env = musubi_data::current_env_map();
    let setup =
        musubi_data::detect_setup_status(&env, &state.project_root, state.audit_db.as_ref());
    let agent_path = setup
        .agent_cli
        .found
        .then(|| PathBuf::from(&setup.agent_cli.path));
    let spec = musubi_data::build_agent_launch_spec(
        &task_text,
        &profile,
        &default_profile,
        agent_path.as_deref(),
        &state.project_root,
        &env,
    )?;

    let mut rt = state.task.lock().map_err(|e| e.to_string())?;
    if rt.status.running {
        return Err("a task is already running — stop it first".into());
    }

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
            let msg = format!("failed to launch {}: {e}", spec.program.display());
            rt.status.error = msg.clone();
            return Err(msg);
        }
    };

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let child = Arc::new(Mutex::new(child));

    rt.status = musubi_data::TaskLauncherStatus {
        running: true,
        task: task_text,
        profile,
        started_at: Some(epoch_secs()),
        ..Default::default()
    };
    rt.child = Some(child.clone());
    drop(rt);

    let shared = state.task.clone();
    if let Some(out) = stdout {
        pump_stream(out, shared.clone(), TailStream::Stdout);
    }
    if let Some(err) = stderr {
        pump_stream(err, shared.clone(), TailStream::Stderr);
    }

    // Exit waiter: poll try_wait so cancel_task can take the child lock to kill.
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(200));
        let polled = match child.lock() {
            Ok(mut c) => c.try_wait(),
            Err(_) => break,
        };
        match polled {
            Ok(None) => continue,
            Ok(Some(status)) => {
                if let Ok(mut rt) = shared.lock() {
                    rt.status.running = false;
                    rt.status.finished_at = Some(epoch_secs());
                    // On unix a kill() exit has no code; surface -1 so the UI
                    // still distinguishes it from success.
                    rt.status.exit_code = Some(status.code().unwrap_or(-1));
                    rt.child = None;
                }
                break;
            }
            Err(e) => {
                if let Ok(mut rt) = shared.lock() {
                    rt.status.running = false;
                    rt.status.finished_at = Some(epoch_secs());
                    rt.status.error = format!("wait failed: {e}");
                    rt.child = None;
                }
                break;
            }
        }
    });

    Ok(())
}

/// Kill the active child process, if any. Audit rows already written by the
/// backend are left intact; the exit waiter records the final state.
fn cancel_task(state: &AppState) -> Result<(), String> {
    let child = state.task.lock().map_err(|e| e.to_string())?.child.clone();
    if let Some(c) = child {
        if let Ok(mut c) = c.lock() {
            let _ = c.kill();
        }
    }
    Ok(())
}

/// Clear the local stdout/stderr/error tails only.
fn clear_task_output(state: &AppState) -> Result<(), String> {
    let mut rt = state.task.lock().map_err(|e| e.to_string())?;
    rt.status.stdout_tail.clear();
    rt.status.stderr_tail.clear();
    rt.status.error.clear();
    Ok(())
}

fn open_db() -> Connection {
    match std::env::var("MUSUBI_DB") {
        Ok(path) if !path.is_empty() => {
            let conn = Connection::open(&path).expect("open MUSUBI_DB");
            // append-only tables already exist in a real audit.db; harmless on a new file
            let _ = musubi_data::init_schema(&conn);
            eprintln!("[musubi] reading audit.db at {path}");
            conn
        }
        _ => {
            let conn = Connection::open_in_memory().expect("open in-memory db");
            musubi_data::init_schema(&conn).expect("init empty schema");
            eprintln!("[musubi] MUSUBI_DB not set — using empty in-memory state");
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
    // Copy the launcher overlay before touching the db lock so no code path
    // ever holds both mutexes at once.
    let task_launcher = state
        .task
        .lock()
        .map_err(|e| e.to_string())?
        .status
        .clone();
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
    st.task_launcher = task_launcher;
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
            let conn = state.db.lock().map_err(|e| e.to_string())?;
            let ack = format!(
                "Acknowledged — re-checking the policy surface and re-tying threads to the audit for: “{}”",
                &text.chars().take(72).collect::<String>()
            );
            conn.execute(
                "INSERT INTO chat_log(role,tone,text) VALUES('you',NULL,?1)",
                [&text],
            )
            .map_err(|e| e.to_string())?;
            conn.execute(
                "INSERT INTO chat_log(role,tone,text) VALUES('driver',NULL,?1)",
                [&ack],
            )
            .map_err(|e| e.to_string())?;
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
        // On-demand task launcher: Run spawns exactly one governed
        // `agent "<task>"` process; opening the GUI never starts one.
        "run_task" => {
            start_task(&state, str_arg(0), str_arg(1))?;
        }
        "cancel_task" => {
            cancel_task(&state)?;
        }
        "clear_task_output" => {
            clear_task_output(&state)?;
        }
        // Spawning agents / running pipelines is a write to the governed
        // substrate — it must go through the MCP server, not a direct DB write.
        // Wire these to musubi_spawn_subagent / the pipeline runner here.
        "add_pipe" | "remove_pipe" | "move_pipe" | "clear_pipe" | "load_preset" | "run_pipe"
        | "stop_pipe" | "reset_pipe" => {
            eprintln!("[musubi] pipeline action '{kind}' — route to MCP server (todo)");
        }
        other => eprintln!("[musubi] unknown action: {other}"),
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let opened = open_configured_db();
    tauri::Builder::default()
        .manage(AppState {
            db: Mutex::new(opened.conn),
            paused: AtomicBool::new(false),
            project_root: opened.project_root,
            audit_db: opened.audit_db,
            task: Arc::new(Mutex::new(TaskRuntime::default())),
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
