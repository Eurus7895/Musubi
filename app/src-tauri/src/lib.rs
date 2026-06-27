//! Tauri desktop shell for the Musubi console.
//!
//! Bridges the webkit-free `musubi-data` core to the React UI:
//!   - `get_state`        → snapshot of the console state
//!   - `action`           → mutating actions (chat, profile, pipeline)
//!   - `state://update`   → emitted ~1×/s by a background poller as audit.db grows
//!
//! Data source: the file at `$MUSUBI_DB` (Musubi's `storage/audit.db`). When
//! unset, an in-memory demo DB is seeded so the app runs standalone.

use std::sync::{Mutex, atomic::{AtomicBool, Ordering}};
use std::time::Duration;

use rusqlite::Connection;
use tauri::{Emitter, Manager};

struct AppState {
    db: Mutex<Connection>,
    paused: AtomicBool,
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
            musubi_data::seed_demo(&conn).expect("seed demo");
            eprintln!("[musubi] MUSUBI_DB not set — using in-memory demo data");
            conn
        }
    }
}

fn snapshot(state: &AppState) -> Result<musubi_data::State, String> {
    let conn = state.db.lock().map_err(|e| e.to_string())?;
    let mut st = musubi_data::load_state(&conn).map_err(|e| e.to_string())?;
    st.paused = state.paused.load(Ordering::Relaxed);
    Ok(st)
}

#[tauri::command]
fn get_state(state: tauri::State<AppState>) -> Result<musubi_data::State, String> {
    snapshot(&state)
}

#[tauri::command]
fn action(kind: String, args: Vec<serde_json::Value>, state: tauri::State<AppState>) -> Result<(), String> {
    let str_arg = |i: usize| args.get(i).and_then(|v| v.as_str()).unwrap_or("").to_string();
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
            conn.execute("INSERT INTO chat_log(role,tone,text) VALUES('you',NULL,?1)", [&text])
                .map_err(|e| e.to_string())?;
            conn.execute("INSERT INTO chat_log(role,tone,text) VALUES('driver',NULL,?1)", [&ack])
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
        // substrate — it must go through the MCP server, not a direct DB write.
        // Wire these to musubi_spawn_subagent / the pipeline runner here.
        "add_pipe" | "remove_pipe" | "move_pipe" | "clear_pipe" | "load_preset"
        | "run_pipe" | "stop_pipe" | "reset_pipe" => {
            eprintln!("[musubi] pipeline action '{kind}' — route to MCP server (todo)");
        }
        other => eprintln!("[musubi] unknown action: {other}"),
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState {
            db: Mutex::new(open_db()),
            paused: AtomicBool::new(false),
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
