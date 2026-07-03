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

use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};
use std::time::Duration;

use rusqlite::Connection;
use tauri::{Emitter, Manager};

struct AppState {
    db: Mutex<Connection>,
    paused: AtomicBool,
    project_root: PathBuf,
    audit_db: Option<musubi_data::ResolvedAuditDb>,
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
                "Acknowledged - re-checking the policy surface and re-tying threads to the audit for: \"{}\"",
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
        // Spawning agents / running pipelines is a write to the governed
        // substrate - it must go through the MCP server, not a direct DB write.
        // Wire these to musubi_spawn_subagent / the pipeline runner here.
        "add_pipe" | "remove_pipe" | "move_pipe" | "clear_pipe" | "load_preset" | "run_pipe"
        | "stop_pipe" | "reset_pipe" => {
            eprintln!("[musubi] pipeline action '{kind}' - route to MCP server (todo)");
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
