//! musubi-tier: substrate
//!
//! Musubi data core — reads the governance substrate's `audit.db` (append-only
//! SQLite) into the `State` object the console UI renders. Pure data: no LLM, no
//! GUI deps, so it builds and tests in a headless environment.
//!
//! Schema contract (see SCHEMA.md). The reader maps the **real** Musubi tables
//! written by the substrate:
//!   - `subagent_audit` (`musubi/storage/subagent_audit.py`) — real columns
//!     `handle_id`, `parent_session_id`, `parent_agent_name`, `final_status`,
//!     `wall_clock_timeout_s`, `tools_used` (JSON array), `ts` (epoch REAL).
//!   - `tool_audit` (`scripts/post_tool_use.py`) — every governed tool call.
//!     The Policy view folds from here when no console-side `policy_audit`
//!     verdict ledger is present (the substrate's `pre_tool_use` hook returns
//!     allow/deny but does not persist it, so executed = allowed).
//!   - `chat_log`, `meta` — console-side (the GUI writes these).
//!   - `policy_audit` — optional console/forward-compat verdict ledger; when it
//!     has rows it wins over `tool_audit` (keeps the demo's HI #3 deny example).
//!
//! Active profile is the LMRouter source of truth: an explicit console choice
//! (`meta.active_profile`) wins, else the `default` in `.musubi/llm.json`.
//!
//! The reader is tolerant of a fresh DB (empty tables → empty surfaces) and of
//! either a REAL or a TEXT `ts`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension};
use serde::Serialize;

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct State {
    pub subagents: Vec<Agent>,
    pub events: Vec<serde_json::Value>,
    pub policy: Vec<Decision>,
    pub audit: Vec<AuditRow>,
    pub chat: Vec<ChatMsg>,
    pub total_spawned: i64,
    pub total_done: i64,
    pub allow_count: i64,
    pub deny_count: i64,
    pub active_profile: String,
    pub pipe_steps: Vec<PipeStep>,
    pub pipe_name: String,
    pub pipe_running: bool,
    pub pipe_cur: i64,
    pub pipe_prog: i64,
    pub pipe_done_flag: bool,
    pub paused: bool,
    pub runtime_source: String,
    pub setup_status: SetupStatus,
    pub t: i64,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct SetupStatus {
    pub project_root: String,
    pub audit_db_path: String,
    pub audit_db_source: String,
    pub python_cli: CliStatus,
    pub musubi_cli: CliStatus,
    pub agent_cli: CliStatus,
    pub llm_config_path: String,
    pub llm_configured: bool,
    pub path_hint: String,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct CliStatus {
    pub found: bool,
    pub path: String,
    pub hint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedAuditDb {
    pub path: PathBuf,
    pub source: String,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Agent {
    pub id: i64,
    pub handle: String,
    pub role: String,
    pub brief: String,
    pub status: String,
    pub turns: i64,
    pub max: i64,
    pub tools: Vec<String>,
    pub wall: i64,
    pub model: String,
    pub profile: String,
    pub parent: String,
    #[serde(skip)]
    pub spawn_epoch: Option<i64>,
}

#[derive(Serialize, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Decision {
    pub id: i64,
    pub ts: String,
    pub verdict: String,
    pub tool: String,
    pub role: String,
    pub handle: String,
    pub reason: String,
}

#[derive(Serialize, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AuditRow {
    pub id: i64,
    pub ts: String,
    pub event: String,
    pub role: String,
    pub handle: String,
    pub detail: String,
    pub status: Option<String>,
}

#[derive(Serialize, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct ChatMsg {
    pub role: String,
    pub ts: Option<String>,
    pub text: String,
    pub tone: Option<String>,
}

#[derive(Serialize, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PipeStep {
    pub uid: i64,
    pub role: String,
    pub status: String,
    pub handle: Option<String>,
}

/// Parse an `allowed_tools` / `tools_used` column stored as a JSON array or a
/// comma list.
fn parse_tools(raw: &str) -> Vec<String> {
    let s = raw.trim();
    if s.is_empty() {
        return vec![];
    }
    if let Ok(v) = serde_json::from_str::<Vec<String>>(s) {
        return v;
    }
    s.split(',')
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
        .collect()
}

fn current_epoch_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn value_epoch_secs(v: &Value) -> Option<i64> {
    match v {
        Value::Real(f) => Some(*f as i64),
        Value::Integer(i) => Some(*i),
        _ => None,
    }
}

/// Render a `ts` column (REAL epoch seconds, INTEGER, or a pre-formatted TEXT
/// like the demo's `14:46:01`) as a `HH:MM:SS` UTC string.
fn fmt_ts(v: &Value) -> String {
    if let Value::Text(s) = v {
        return s.clone();
    }
    let Some(secs) = value_epoch_secs(v) else {
        return String::new();
    };
    let sod = ((secs % 86_400) + 86_400) % 86_400;
    format!("{:02}:{:02}:{:02}", sod / 3600, (sod % 3600) / 60, sod % 60)
}

/// Compose the parent label shown on an Orchestrator card. Real rows carry a
/// `parent_agent_name` + a `parent_session_id` (a UUID); long ids are shortened.
fn fmt_parent(agent: &str, session: &str) -> String {
    let agent = if agent.is_empty() { "driver" } else { agent };
    if session.is_empty() {
        return agent.to_string();
    }
    let sid = if session.len() > 12 {
        &session[..8]
    } else {
        session
    };
    format!("{agent} · {sid}")
}

/// Read the full console state from an open connection to a Musubi `audit.db`.
pub fn load_state(conn: &Connection) -> rusqlite::Result<State> {
    load_state_at(conn, current_epoch_secs())
}

fn load_state_at(conn: &Connection, now_epoch: i64) -> rusqlite::Result<State> {
    let mut st = State {
        active_profile: read_active_profile(conn),
        pipe_name: "feature-dev".into(),
        pipe_cur: -1,
        runtime_source: "demo".into(),
        ..Default::default()
    };

    // ── sub-agent cohort: fold the append-only lifecycle log per handle ──
    // One row per (spawned|completed) event; a handle is 'running' until its
    // 'completed' row lands. Columns are the real subagent_audit schema.
    let mut stmt = conn.prepare(
        "SELECT id, ts, event, handle_id, role, parent_session_id, parent_agent_name, \
                brief, allowed_tools, max_turns, wall_clock_timeout_s, final_status, \
                turns, tools_used \
         FROM subagent_audit ORDER BY id ASC",
    )?;
    let mut order: Vec<String> = Vec::new();
    let mut agents: std::collections::HashMap<String, Agent> = std::collections::HashMap::new();
    let mut audit: Vec<AuditRow> = Vec::new();

    let rows = stmt.query_map([], |r| {
        let ts_value = r.get::<_, Value>(1)?;
        Ok(RawAudit {
            id: r.get(0)?,
            ts: fmt_ts(&ts_value),
            ts_epoch: value_epoch_secs(&ts_value),
            event: r.get(2)?,
            handle: r.get(3)?,
            role: r.get(4)?,
            parent_session: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
            parent_agent: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
            brief: r.get::<_, Option<String>>(7)?.unwrap_or_default(),
            allowed_tools: r.get::<_, Option<String>>(8)?.unwrap_or_default(),
            max_turns: r.get::<_, Option<i64>>(9)?.unwrap_or(0),
            wall: r.get::<_, Option<i64>>(10)?.unwrap_or(0),
            final_status: r.get::<_, Option<String>>(11)?,
            turns: r.get::<_, Option<i64>>(12)?.unwrap_or(0),
            tools_used: r.get::<_, Option<String>>(13)?.unwrap_or_default(),
        })
    })?;

    for row in rows {
        let row = row?;
        if row.event == "spawned" {
            st.total_spawned += 1;
            let tools = parse_tools(&row.allowed_tools);
            if !agents.contains_key(&row.handle) {
                order.push(row.handle.clone());
            }
            agents.insert(
                row.handle.clone(),
                Agent {
                    id: row.id,
                    handle: row.handle.clone(),
                    role: row.role.clone(),
                    brief: row.brief.clone(),
                    status: "running".into(),
                    turns: row.turns,
                    max: row.max_turns,
                    tools,
                    wall: row.wall,
                    // The real subagent_audit schema does not record the
                    // resolved model/profile per handle; left blank.
                    model: String::new(),
                    profile: String::new(),
                    parent: fmt_parent(&row.parent_agent, &row.parent_session),
                    spawn_epoch: row.ts_epoch,
                },
            );
        } else if row.event == "completed" {
            let status = row.final_status.clone().unwrap_or_else(|| "done".into());
            if status == "done" {
                st.total_done += 1;
            }
            if let Some(a) = agents.get_mut(&row.handle) {
                a.status = status.clone();
                a.turns = row.turns.max(a.turns);
            }
        }

        // every lifecycle row is an append-only audit ledger entry
        let detail = if row.event == "spawned" {
            format!(
                "allowed_tools=[{}] max_turns={}",
                parse_tools(&row.allowed_tools).len(),
                row.max_turns
            )
        } else {
            let err = if row.final_status.as_deref() == Some("done") {
                ""
            } else {
                " err"
            };
            format!(
                "turns={} tools_used={}{}",
                row.turns,
                parse_tools(&row.tools_used).len(),
                err
            )
        };
        audit.push(AuditRow {
            id: row.id,
            ts: row.ts.clone(),
            event: row.event.clone(),
            role: row.role.clone(),
            handle: row.handle.clone(),
            detail,
            status: if row.event == "spawned" {
                None
            } else {
                Some(row.final_status.clone().unwrap_or_else(|| "done".into()))
            },
        });
    }

    for agent in agents.values_mut() {
        if agent.status == "running"
            && agent.wall > 0
            && agent
                .spawn_epoch
                .is_some_and(|spawned_at| now_epoch.saturating_sub(spawned_at) > agent.wall)
        {
            agent.status = "abandoned".into();
        }
    }

    st.subagents = order
        .into_iter()
        .filter_map(|h| agents.remove(&h))
        .collect();
    audit.reverse(); // newest first
    audit.truncate(120);
    st.audit = audit;

    // ── policy decisions ──
    // Prefer a console/forward-compat verdict ledger (policy_audit) when it has
    // rows; otherwise fold from the real tool_audit (executed = allowed — the
    // substrate's pre_tool_use deny is not persisted).
    let has_policy = table_exists(conn, "policy_audit")?
        && count(conn, "SELECT COUNT(*) FROM policy_audit")? > 0;
    if has_policy {
        let mut pstmt = conn.prepare(
            "SELECT id, ts, verdict, tool, role, handle, reason \
             FROM policy_audit ORDER BY id DESC LIMIT 50",
        )?;
        st.policy = pstmt
            .query_map([], |r| {
                Ok(Decision {
                    id: r.get(0)?,
                    ts: fmt_ts(&r.get::<_, Value>(1)?),
                    verdict: r.get(2)?,
                    tool: r.get(3)?,
                    role: r.get(4)?,
                    handle: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
                    reason: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        st.allow_count = count(
            conn,
            "SELECT COUNT(*) FROM policy_audit WHERE verdict='ALLOW'",
        )?;
        st.deny_count = count(
            conn,
            "SELECT COUNT(*) FROM policy_audit WHERE verdict='DENY'",
        )?;
    } else if table_exists(conn, "tool_audit")? {
        let mut tstmt = conn.prepare(
            "SELECT id, ts, agent, tool, status FROM tool_audit ORDER BY id DESC LIMIT 50",
        )?;
        st.policy = tstmt
            .query_map([], |r| {
                let status: Option<String> = r.get(4)?;
                Ok(Decision {
                    id: r.get(0)?,
                    ts: fmt_ts(&r.get::<_, Value>(1)?),
                    verdict: "ALLOW".into(),
                    tool: r.get(3)?,
                    role: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    handle: String::new(),
                    reason: status.unwrap_or_else(|| "executed".into()),
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        // pre_tool_use denies are not persisted, so every recorded call is an allow.
        st.allow_count = count(conn, "SELECT COUNT(*) FROM tool_audit")?;
        st.deny_count = 0;
    }

    // ── driver chat ──
    if table_exists(conn, "chat_log")? {
        let mut cstmt =
            conn.prepare("SELECT role, ts, text, tone FROM chat_log ORDER BY id ASC LIMIT 60")?;
        st.chat = cstmt
            .query_map([], |r| {
                Ok(ChatMsg {
                    role: r.get(0)?,
                    ts: r.get::<_, Option<String>>(1)?,
                    text: r.get(2)?,
                    tone: r.get::<_, Option<String>>(3)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
    }

    // ── pipeline studio default (authoring surface; not from the audit) ──
    st.pipe_steps = ["explorer", "planner", "coder", "reviewer"]
        .iter()
        .enumerate()
        .map(|(i, r)| PipeStep {
            uid: (i + 1) as i64,
            role: r.to_string(),
            status: "idle".into(),
            handle: None,
        })
        .collect();

    Ok(st)
}

struct RawAudit {
    id: i64,
    ts: String,
    ts_epoch: Option<i64>,
    event: String,
    handle: String,
    role: String,
    parent_session: String,
    parent_agent: String,
    brief: String,
    allowed_tools: String,
    max_turns: i64,
    wall: i64,
    final_status: Option<String>,
    turns: i64,
    tools_used: String,
}

/// Active LMRouter profile: an explicit console choice wins, else the
/// `default` recorded in `.musubi/llm.json` (the runner's source of truth),
/// else a conservative fallback.
fn read_active_profile(conn: &Connection) -> String {
    if let Some(p) = read_meta(conn, "active_profile") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(p) = read_llm_default() {
        return p;
    }
    "anthropic.default".into()
}

/// Read the `default` profile name from `.musubi/llm.json`. Located via the
/// `MUSUBI_LLM_CONFIG` env var, else by walking up from `$MUSUBI_DB`. Any
/// failure (unset env, missing file, malformed JSON) yields `None`.
fn read_llm_default() -> Option<String> {
    let path = std::env::var("MUSUBI_LLM_CONFIG")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .or_else(find_llm_json_near_db)?;
    let txt = std::fs::read_to_string(path).ok()?;
    let v: serde_json::Value = serde_json::from_str(&txt).ok()?;
    v.get("default")?.as_str().map(str::to_string)
}

fn find_llm_json_near_db() -> Option<PathBuf> {
    let db = std::env::var("MUSUBI_DB").ok().filter(|s| !s.is_empty())?;
    let mut dir = Path::new(&db).parent();
    while let Some(d) = dir {
        let cand = d.join(".musubi").join("llm.json");
        if cand.is_file() {
            return Some(cand);
        }
        dir = d.parent();
    }
    None
}

fn read_meta(conn: &Connection, key: &str) -> Option<String> {
    if !table_exists(conn, "meta").unwrap_or(false) {
        return None;
    }
    conn.query_row("SELECT value FROM meta WHERE key=?1", [key], |r| r.get(0))
        .optional()
        .ok()
        .flatten()
}

fn table_exists(conn: &Connection, name: &str) -> rusqlite::Result<bool> {
    let n: i64 = conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
        [name],
        |r| r.get(0),
    )?;
    Ok(n > 0)
}

fn count(conn: &Connection, sql: &str) -> rusqlite::Result<i64> {
    conn.query_row(sql, [], |r| r.get(0))
}

/// Create the Musubi audit schema on a fresh database. Mirrors the real
/// substrate tables (`subagent_audit`, `tool_audit`) plus the console-side
/// `chat_log` / `meta`, and an optional `policy_audit` verdict ledger.
pub fn init_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_SQL)
}

pub const SCHEMA_SQL: &str = r#"
-- Real substrate table — musubi/storage/subagent_audit.py (HI #8).
CREATE TABLE IF NOT EXISTS subagent_audit (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                   REAL NOT NULL,
  handle_id            TEXT NOT NULL,
  parent_session_id    TEXT NOT NULL,
  parent_agent_name    TEXT NOT NULL,
  role                 TEXT NOT NULL,
  brief                TEXT NOT NULL,
  event                TEXT NOT NULL,            -- 'spawned' | 'completed'
  allowed_tools        TEXT,                     -- JSON array
  max_turns            INTEGER,
  wall_clock_timeout_s INTEGER,
  final_status         TEXT,                     -- done|failed|escalated|abandoned
  escalated            INTEGER,
  turns                INTEGER,
  tools_used           TEXT,                     -- JSON array
  summary_truncated    INTEGER,
  verification_errors  TEXT
);
-- Real substrate table — scripts/post_tool_use.py. Every governed tool call.
CREATE TABLE IF NOT EXISTS tool_audit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           REAL NOT NULL,
  session_id   TEXT,
  pipeline     TEXT,
  agent        TEXT,
  tool         TEXT NOT NULL,
  args_json    TEXT,
  result_hash  TEXT,
  status       TEXT
);
-- Optional console/forward-compat verdict ledger (allow/deny). The real
-- pre_tool_use hook does not persist verdicts; when this is empty the Policy
-- view folds from tool_audit instead.
CREATE TABLE IF NOT EXISTS policy_audit (
  id      INTEGER PRIMARY KEY,
  ts      TEXT NOT NULL,
  verdict TEXT NOT NULL,                      -- 'ALLOW' | 'DENY'
  tool    TEXT NOT NULL,
  role    TEXT NOT NULL,
  handle  TEXT,
  reason  TEXT
);
-- Console-side tables (the GUI writes these).
CREATE TABLE IF NOT EXISTS chat_log (
  id   INTEGER PRIMARY KEY,
  ts   TEXT,
  role TEXT,                                   -- 'you' | 'driver' | 'system'
  tone TEXT,
  text TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"#;

/// Seed a representative governed session — used by `cargo test`, and by the
/// app as a fallback demo DB when no real `audit.db` is configured. Rows use
/// the real `subagent_audit` / `tool_audit` shapes, plus a `policy_audit` deny
/// to illustrate the evaluator firewall (HI #3).
pub fn seed_demo(conn: &Connection) -> rusqlite::Result<()> {
    init_schema(conn)?;
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('active_profile','anthropic.default')",
        [],
    )?;

    // A fixed base epoch so the demo ledger shows stable times.
    let base = 1_736_500_000_i64;

    #[allow(clippy::too_many_arguments)]
    let spawn = |conn: &Connection,
                 id: i64,
                 off: i64,
                 handle: &str,
                 role: &str,
                 brief: &str,
                 tools: &str,
                 max: i64,
                 wall: i64|
     -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(?1,?2,'spawned',?3,'agent-loop','driver',?4,?5,?6,?7,?8)",
            rusqlite::params![id, (base + off) as f64, handle, role, brief, tools, max, wall],
        )?;
        Ok(())
    };
    let complete = |conn: &Connection,
                    id: i64,
                    off: i64,
                    handle: &str,
                    role: &str,
                    turns: i64,
                    tools_used: &str,
                    status: &str|
     -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,final_status,turns,tools_used)\
             VALUES(?1,?2,'completed',?3,'agent-loop','driver',?4,'',?5,?6,?7)",
            rusqlite::params![id, (base + off) as f64, handle, role, status, turns, tools_used],
        )?;
        Ok(())
    };

    spawn(
        conn,
        1,
        0,
        "a1b2c3d4",
        "explorer",
        "Map callers of LMRouter across agent/vendors",
        r#"["musubi_read_file","musubi_run_command","musubi_retrieve"]"#,
        6,
        300,
    )?;
    spawn(
        conn,
        2,
        8,
        "b2c3d4e5",
        "investigator",
        "Reproduce the failing pytest in storage/db.py",
        r#"["musubi_read_file","musubi_run_command","musubi_query_subagent_events"]"#,
        8,
        300,
    )?;
    spawn(
        conn,
        3,
        17,
        "c3d4e5f6",
        "reviewer-aux",
        "Verify the patch touches code only",
        r#"["musubi_read_file"]"#,
        4,
        300,
    )?;
    complete(
        conn,
        4,
        30,
        "a1b2c3d4",
        "explorer",
        6,
        r#"["musubi_read_file","musubi_run_command","musubi_retrieve"]"#,
        "done",
    )?;

    // tool_audit — the real allowed-call ledger the Policy view folds from on a
    // real DB (here policy_audit below wins because it has rows).
    let call = |conn: &Connection,
                id: i64,
                off: i64,
                agent: &str,
                tool: &str,
                status: &str|
     -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO tool_audit(id,ts,session_id,pipeline,agent,tool,status) VALUES(?1,?2,'agent-loop','feature-dev',?3,?4,?5)",
            rusqlite::params![id, (base + off) as f64, agent, tool, status],
        )?;
        Ok(())
    };
    call(conn, 1, 1, "explorer", "musubi_read_file", "ok")?;
    call(conn, 2, 9, "investigator", "musubi_run_command", "ok")?;
    call(conn, 3, 19, "reviewer-aux", "musubi_read_file", "ok")?;

    // policy_audit — a deny example for the evaluator firewall (HI #3).
    let decide = |conn: &Connection,
                  id: i64,
                  ts: &str,
                  verdict: &str,
                  tool: &str,
                  role: &str,
                  handle: &str,
                  reason: &str|
     -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO policy_audit(id,ts,verdict,tool,role,handle,reason) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            rusqlite::params![id, ts, verdict, tool, role, handle, reason],
        )?;
        Ok(())
    };
    decide(
        conn,
        1,
        "14:46:02",
        "ALLOW",
        "musubi_read_file",
        "explorer",
        "a1b2c3d4",
        "in surface",
    )?;
    decide(
        conn,
        2,
        "14:46:10",
        "ALLOW",
        "musubi_run_command",
        "investigator",
        "b2c3d4e5",
        "in surface",
    )?;
    decide(
        conn,
        3,
        "14:46:19",
        "DENY",
        "musubi_write_file",
        "reviewer-aux",
        "c3d4e5f6",
        "outside firewall surface — code-only (HI #3)",
    )?;
    decide(
        conn,
        4,
        "14:46:20",
        "ALLOW",
        "musubi_read_file",
        "reviewer-aux",
        "c3d4e5f6",
        "in surface",
    )?;

    let say = |conn: &Connection,
               id: i64,
               ts: Option<&str>,
               role: &str,
               tone: Option<&str>,
               text: &str|
     -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO chat_log(id,ts,role,tone,text) VALUES(?1,?2,?3,?4,?5)",
            rusqlite::params![id, ts, role, tone, text],
        )?;
        Ok(())
    };
    say(
        conn,
        1,
        Some("14:46:00"),
        "you",
        None,
        "Audit why run_command is denied for the reviewer. Tie everything to policy.",
    )?;
    say(conn, 2, Some("14:46:00"), "driver", None, "On it. I reach the model through one inject point and spawn governed threads — each turn-capped, firewalled, and bound into the audit.")?;
    say(
        conn,
        3,
        None,
        "system",
        Some("spawn"),
        "tied explorer · investigator · reviewer-aux into the audit",
    )?;

    Ok(())
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::*;

    fn demo() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        seed_demo(&conn).unwrap();
        conn
    }

    fn demo_state() -> State {
        let conn = demo();
        load_state_at(&conn, 1_736_500_020).unwrap()
    }

    #[test]
    fn builds_cohort_with_running_and_completed() {
        let st = demo_state();
        assert_eq!(st.subagents.len(), 3, "three handles spawned");
        let explorer = st.subagents.iter().find(|a| a.role == "explorer").unwrap();
        assert_eq!(explorer.status, "done", "explorer completed");
        assert_eq!(explorer.turns, 6);
        assert_eq!(explorer.tools.len(), 3);
        assert_eq!(explorer.parent, "driver · agent-loop");
        let reviewer = st
            .subagents
            .iter()
            .find(|a| a.role == "reviewer-aux")
            .unwrap();
        assert_eq!(reviewer.status, "running");
        assert_eq!(reviewer.max, 4);
        assert_eq!(reviewer.wall, 300);
    }

    #[test]
    fn counts_match_the_log() {
        let st = demo_state();
        assert_eq!(st.total_spawned, 3);
        assert_eq!(st.total_done, 1);
        // policy_audit has rows, so it wins over tool_audit.
        assert_eq!(st.allow_count, 3);
        assert_eq!(st.deny_count, 1);
        assert_eq!(st.active_profile, "anthropic.default");
    }

    #[test]
    fn audit_is_newest_first_with_derived_detail() {
        let st = demo_state();
        assert_eq!(st.audit.len(), 4);
        assert!(st.audit[0].id > st.audit[1].id, "newest first");
        let spawned = st
            .audit
            .iter()
            .find(|r| r.event == "spawned" && r.handle == "c3d4e5f6")
            .unwrap();
        assert_eq!(spawned.detail, "allowed_tools=[1] max_turns=4");
        assert!(spawned.status.is_none());
        let completed = st.audit.iter().find(|r| r.event == "completed").unwrap();
        assert_eq!(completed.status.as_deref(), Some("done"));
        assert_eq!(completed.detail, "turns=6 tools_used=3");
    }

    #[test]
    fn policy_folds_from_tool_audit_when_no_verdict_ledger() {
        // A real DB has tool_audit but no policy_audit rows.
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO tool_audit(id,ts,agent,tool,status) VALUES(1,1736500001.0,'explorer','musubi_read_file','ok')",
            [],
        )
        .unwrap();
        let st = load_state(&conn).unwrap();
        assert_eq!(st.policy.len(), 1);
        assert_eq!(st.policy[0].verdict, "ALLOW");
        assert_eq!(st.policy[0].tool, "musubi_read_file");
        assert_eq!(st.allow_count, 1);
        assert_eq!(st.deny_count, 0);
    }

    #[test]
    fn serializes_to_camelcase_json() {
        let st = demo_state();
        let v: serde_json::Value = serde_json::to_value(&st).unwrap();
        assert!(v.get("totalSpawned").is_some());
        assert!(v.get("activeProfile").is_some());
        assert!(v.get("runtimeSource").is_some());
        assert!(v["subagents"][0].get("spawnEpoch").is_none());
        assert!(v["subagents"][0].get("max").is_some());
        assert!(v["pipeSteps"].as_array().unwrap().len() == 4);
    }

    #[test]
    fn default_runtime_source_is_demo_until_backend_overrides_it() {
        let st = demo_state();
        assert_eq!(st.runtime_source, "demo");
    }

    #[test]
    fn stale_spawn_without_completion_is_abandoned() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(1,1000.0,'spawned','stale-1','session-1','driver','planner','old task','[]',5,60)",
            [],
        )
        .unwrap();

        let st = load_state_at(&conn, 2000).unwrap();

        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].status, "abandoned");
    }

    #[test]
    fn fresh_db_yields_empty_surfaces() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        let st = load_state(&conn).unwrap();
        assert_eq!(st.subagents.len(), 0);
        assert_eq!(st.total_spawned, 0);
        assert!(!st.active_profile.is_empty());
    }

    fn temp_dir(name: &str) -> PathBuf {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("musubi-{name}-{stamp}"));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn resolve_audit_db_prefers_explicit_env_path() {
        let root = temp_dir("explicit-db");
        let explicit = root.join("custom").join("audit.db");
        let mut env = std::collections::HashMap::new();
        env.insert(
            "MUSUBI_DB".to_string(),
            explicit.to_string_lossy().to_string(),
        );

        let resolved = resolve_audit_db_path(&env, &root).unwrap();

        assert_eq!(resolved.path, explicit);
        assert_eq!(resolved.source, "musubi-db");
    }

    #[test]
    fn resolve_audit_db_uses_musubi_root_when_env_db_is_absent() {
        let root = temp_dir("root-db");
        let musubi_root = root.join("musubi-core");
        let mut env = std::collections::HashMap::new();
        env.insert(
            "MUSUBI_ROOT".to_string(),
            musubi_root.to_string_lossy().to_string(),
        );

        let resolved = resolve_audit_db_path(&env, &root).unwrap();

        assert_eq!(resolved.path, musubi_root.join("data").join("audit.db"));
        assert_eq!(resolved.source, "musubi-root");
    }

    #[test]
    fn resolve_audit_db_finds_workspace_package_storage() {
        let root = temp_dir("workspace-db");
        let storage = root.join("musubi").join("storage");
        std::fs::create_dir_all(&storage).unwrap();
        std::fs::write(root.join("musubi").join("server.py"), "").unwrap();

        let resolved = resolve_audit_db_path(&std::collections::HashMap::new(), &root).unwrap();

        assert_eq!(resolved.path, storage.join("audit.db"));
        assert_eq!(resolved.source, "workspace");
    }

    #[test]
    fn find_command_checks_extra_python_script_dirs() {
        let root = temp_dir("script-dir");
        let scripts = root.join("Scripts");
        std::fs::create_dir_all(&scripts).unwrap();
        let exe = scripts.join(if cfg!(windows) {
            "musubi.exe"
        } else {
            "musubi"
        });
        std::fs::write(&exe, "").unwrap();

        let found = find_command("musubi", "", &[scripts]).unwrap();

        assert_eq!(found, exe);
    }

    #[test]
    fn detect_setup_status_reports_project_llm_config() {
        let root = temp_dir("setup-status");
        std::fs::create_dir_all(root.join(".musubi")).unwrap();
        std::fs::write(
            root.join(".musubi").join("llm.json"),
            r#"{"default":"ollama.local"}"#,
        )
        .unwrap();
        let resolved = ResolvedAuditDb {
            path: root.join("musubi").join("storage").join("audit.db"),
            source: "workspace".into(),
        };

        let status = detect_setup_status(&std::collections::HashMap::new(), &root, Some(&resolved));

        assert_eq!(status.project_root, root.to_string_lossy());
        assert!(status.llm_configured);
        assert_eq!(
            status.llm_config_path,
            root.join(".musubi").join("llm.json").to_string_lossy()
        );
        assert_eq!(status.audit_db_source, "workspace");
    }

    #[test]
    fn detect_setup_status_reports_python_on_path() {
        let root = temp_dir("python-status");
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let exe = bin.join(if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        });
        std::fs::write(&exe, "").unwrap();
        let mut env = std::collections::HashMap::new();
        env.insert("PATH".to_string(), bin.to_string_lossy().to_string());

        let status = detect_setup_status(&env, &root, None);

        assert!(status.python_cli.found);
        assert_eq!(status.python_cli.path, exe.to_string_lossy());
    }
}

pub fn current_env_map() -> HashMap<String, String> {
    std::env::vars().collect()
}

pub fn resolve_audit_db_path(env: &HashMap<String, String>, cwd: &Path) -> Option<ResolvedAuditDb> {
    if let Some(raw) = nonempty(env, "MUSUBI_DB") {
        return Some(ResolvedAuditDb {
            path: PathBuf::from(raw),
            source: "musubi-db".into(),
        });
    }
    if let Some(raw) = nonempty(env, "MUSUBI_ROOT") {
        return Some(ResolvedAuditDb {
            path: PathBuf::from(raw).join("data").join("audit.db"),
            source: "musubi-root".into(),
        });
    }

    let mut dir = Some(cwd);
    while let Some(d) = dir {
        let package_storage = d.join("musubi").join("storage");
        if d.join("musubi").join("server.py").is_file() || package_storage.is_dir() {
            return Some(ResolvedAuditDb {
                path: package_storage.join("audit.db"),
                source: "workspace".into(),
            });
        }
        let local_storage = d.join("storage");
        if d.join("server.py").is_file() || local_storage.is_dir() {
            return Some(ResolvedAuditDb {
                path: local_storage.join("audit.db"),
                source: "package".into(),
            });
        }
        dir = d.parent();
    }
    None
}

pub fn detect_setup_status(
    env: &HashMap<String, String>,
    project_root: &Path,
    audit_db: Option<&ResolvedAuditDb>,
) -> SetupStatus {
    let path_env = env.get("PATH").map(String::as_str).unwrap_or("");
    let extra_dirs = python_script_dirs_from_env(env);
    let python_cli = python_status(path_env);
    let musubi_cli = cli_status("musubi", path_env, &extra_dirs);
    let agent_cli = cli_status("agent", path_env, &extra_dirs);
    let llm_config = resolve_llm_config_path(env, project_root, audit_db);
    let missing = ["musubi", "agent"]
        .into_iter()
        .filter(|name| {
            if *name == "musubi" {
                !musubi_cli.found
            } else {
                !agent_cli.found
            }
        })
        .collect::<Vec<_>>();
    let path_hint = if missing.is_empty() {
        String::new()
    } else {
        let mut hint = format!(
            "Missing {}. Run `python -m pip install --user musubi`.",
            missing.join(", ")
        );
        if let Some(dir) = extra_dirs.first() {
            hint.push_str(&format!(
                " Add `{}` to PATH if scripts are installed there.",
                dir.display()
            ));
        }
        hint
    };

    SetupStatus {
        project_root: project_root.to_string_lossy().to_string(),
        audit_db_path: audit_db
            .map(|r| r.path.to_string_lossy().to_string())
            .unwrap_or_default(),
        audit_db_source: audit_db
            .map(|r| r.source.clone())
            .unwrap_or_else(|| "none".into()),
        python_cli,
        musubi_cli,
        agent_cli,
        llm_configured: llm_config.as_ref().is_some_and(|p| p.is_file()),
        llm_config_path: llm_config
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default(),
        path_hint,
    }
}

pub fn find_command(command: &str, path_env: &str, extra_dirs: &[PathBuf]) -> Option<PathBuf> {
    let path_dirs = std::env::split_paths(path_env);
    path_dirs
        .chain(extra_dirs.iter().cloned())
        .flat_map(|dir| {
            command_candidates(command)
                .into_iter()
                .map(move |name| dir.join(name))
        })
        .find(|path| path.is_file())
}

fn cli_status(command: &str, path_env: &str, extra_dirs: &[PathBuf]) -> CliStatus {
    match find_command(command, path_env, extra_dirs) {
        Some(path) => CliStatus {
            found: true,
            path: path.to_string_lossy().to_string(),
            hint: String::new(),
        },
        None => CliStatus {
            found: false,
            path: String::new(),
            hint: "Install the Python core with `python -m pip install --user musubi`.".into(),
        },
    }
}

fn python_status(path_env: &str) -> CliStatus {
    find_command("python", path_env, &[])
        .or_else(|| find_command("py", path_env, &[]))
        .map(|path| CliStatus {
            found: true,
            path: path.to_string_lossy().to_string(),
            hint: String::new(),
        })
        .unwrap_or_else(|| CliStatus {
            found: false,
            path: String::new(),
            hint: "Install Python 3.11+ and open a new terminal.".into(),
        })
}

fn nonempty(env: &HashMap<String, String>, key: &str) -> Option<String> {
    env.get(key)
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn command_candidates(command: &str) -> Vec<String> {
    let path = Path::new(command);
    if path.extension().is_some() {
        return vec![command.to_string()];
    }
    if cfg!(windows) {
        vec![
            format!("{command}.exe"),
            format!("{command}.cmd"),
            format!("{command}.bat"),
            command.to_string(),
        ]
    } else {
        vec![command.to_string()]
    }
}

fn python_script_dirs_from_env(env: &HashMap<String, String>) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Some(appdata) = nonempty(env, "APPDATA") {
        collect_child_script_dirs(&mut dirs, PathBuf::from(appdata).join("Python"), "Python");
    }
    if let Some(local) = nonempty(env, "LOCALAPPDATA") {
        collect_child_script_dirs(
            &mut dirs,
            PathBuf::from(&local).join("Programs").join("Python"),
            "Python",
        );
        collect_child_script_dirs(
            &mut dirs,
            PathBuf::from(local).join("Packages"),
            "PythonSoftwareFoundation.Python.",
        );
    }
    if let Some(home) = nonempty(env, "USERPROFILE").or_else(|| nonempty(env, "HOME")) {
        dirs.push(PathBuf::from(home).join(".local").join("bin"));
    }
    dirs
}

fn collect_child_script_dirs(dirs: &mut Vec<PathBuf>, base: PathBuf, prefix: &str) {
    let Ok(entries) = std::fs::read_dir(base) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|s| s.to_str()) else {
            continue;
        };
        if !name.starts_with(prefix) {
            continue;
        }
        let scripts = if name.starts_with("PythonSoftwareFoundation.Python.") {
            path.join("LocalCache").join("local-packages")
        } else {
            path
        };
        if let Ok(children) = std::fs::read_dir(&scripts) {
            for child in children.flatten() {
                let cand = child.path().join("Scripts");
                if cand.is_dir() {
                    dirs.push(cand);
                }
            }
        }
        let direct = scripts.join("Scripts");
        if direct.is_dir() {
            dirs.push(direct);
        }
    }
}

fn resolve_llm_config_path(
    env: &HashMap<String, String>,
    project_root: &Path,
    audit_db: Option<&ResolvedAuditDb>,
) -> Option<PathBuf> {
    if let Some(raw) = nonempty(env, "MUSUBI_LLM_CONFIG") {
        return Some(PathBuf::from(raw));
    }
    let project_config = project_root.join(".musubi").join("llm.json");
    if project_config.is_file() {
        return Some(project_config);
    }
    let mut dir = audit_db.and_then(|r| r.path.parent());
    while let Some(d) = dir {
        let cand = d.join(".musubi").join("llm.json");
        if cand.is_file() {
            return Some(cand);
        }
        dir = d.parent();
    }
    Some(project_config)
}
