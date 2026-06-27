//! Musubi data core — reads the governance substrate's `audit.db` (append-only
//! SQLite) into the `State` object the console UI renders. Pure data: no LLM, no
//! GUI deps, so it builds and tests in a headless environment.
//!
//! Schema contract (see SCHEMA.md): tables `subagent_audit`, `policy_audit`,
//! `chat_log`, and a `meta` key/value table. The reader is tolerant of a fresh
//! DB (empty tables → empty surfaces).

use rusqlite::{Connection, OptionalExtension};
use serde::Serialize;

#[derive(Serialize, Default, Debug)]
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
    pub t: i64,
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
}

#[derive(Serialize, Debug)]
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

#[derive(Serialize, Debug)]
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

#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct ChatMsg {
    pub role: String,
    pub ts: Option<String>,
    pub text: String,
    pub tone: Option<String>,
}

#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct PipeStep {
    pub uid: i64,
    pub role: String,
    pub status: String,
    pub handle: Option<String>,
}

/// Parse an `allowed_tools` column stored as a JSON array or a comma list.
fn parse_tools(raw: &str) -> Vec<String> {
    let s = raw.trim();
    if s.is_empty() {
        return vec![];
    }
    if let Ok(v) = serde_json::from_str::<Vec<String>>(s) {
        return v;
    }
    s.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect()
}

/// Read the full console state from an open connection to a Musubi `audit.db`.
pub fn load_state(conn: &Connection) -> rusqlite::Result<State> {
    let mut st = State {
        active_profile: read_meta(conn, "active_profile").unwrap_or_else(|| "anthropic.default".into()),
        pipe_name: "feature-dev".into(),
        pipe_cur: -1,
        ..Default::default()
    };

    // ── sub-agent cohort: fold the append-only lifecycle log per handle ──
    // One row per (spawned|completed) event; a handle is 'running' until its
    // 'completed' row lands.
    let mut stmt = conn.prepare(
        "SELECT id, ts, event, handle, role, parent, model, profile, brief, \
                allowed_tools, max_turns, turns, tools_used, status, wall_remaining \
         FROM subagent_audit ORDER BY id ASC",
    )?;
    let mut order: Vec<String> = Vec::new();
    let mut agents: std::collections::HashMap<String, Agent> = std::collections::HashMap::new();
    let mut audit: Vec<AuditRow> = Vec::new();

    let rows = stmt.query_map([], |r| {
        Ok(RawAudit {
            id: r.get(0)?,
            ts: r.get(1)?,
            event: r.get(2)?,
            handle: r.get(3)?,
            role: r.get(4)?,
            parent: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
            model: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
            profile: r.get::<_, Option<String>>(7)?.unwrap_or_default(),
            brief: r.get::<_, Option<String>>(8)?.unwrap_or_default(),
            allowed_tools: r.get::<_, Option<String>>(9)?.unwrap_or_default(),
            max_turns: r.get::<_, Option<i64>>(10)?.unwrap_or(0),
            turns: r.get::<_, Option<i64>>(11)?.unwrap_or(0),
            tools_used: r.get::<_, Option<i64>>(12)?.unwrap_or(0),
            status: r.get::<_, Option<String>>(13)?,
            wall_remaining: r.get::<_, Option<i64>>(14)?.unwrap_or(0),
        })
    })?;

    for row in rows {
        let row = row?;
        let tools = parse_tools(&row.allowed_tools);
        if row.event == "spawned" {
            st.total_spawned += 1;
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
                    tools: tools.clone(),
                    wall: row.wall_remaining,
                    model: row.model.clone(),
                    profile: row.profile.clone(),
                    parent: if row.parent.is_empty() { "driver · agent-loop".into() } else { row.parent.clone() },
                },
            );
        } else if row.event == "completed" {
            let status = row.status.clone().unwrap_or_else(|| "done".into());
            if status == "done" {
                st.total_done += 1;
            }
            if let Some(a) = agents.get_mut(&row.handle) {
                a.status = status.clone();
                a.turns = row.turns.max(a.turns);
                a.wall = row.wall_remaining;
            }
        }

        // every lifecycle row is an append-only audit ledger entry
        let detail = if row.event == "spawned" {
            format!("allowed_tools=[{}] max_turns={}", tools.len(), row.max_turns)
        } else {
            let err = if row.status.as_deref() == Some("done") { "" } else { " err" };
            format!("turns={} tools_used={}{}", row.turns, row.tools_used, err)
        };
        audit.push(AuditRow {
            id: row.id,
            ts: row.ts.clone(),
            event: row.event.clone(),
            role: row.role.clone(),
            handle: row.handle.clone(),
            detail,
            status: if row.event == "spawned" { None } else { row.status.clone() },
        });
    }

    st.subagents = order.into_iter().filter_map(|h| agents.remove(&h)).collect();
    audit.reverse(); // newest first
    audit.truncate(120);
    st.audit = audit;

    // ── policy decisions ──
    let mut pstmt = conn.prepare(
        "SELECT id, ts, verdict, tool, role, handle, reason FROM policy_audit ORDER BY id DESC LIMIT 50",
    )?;
    st.policy = pstmt
        .query_map([], |r| {
            Ok(Decision {
                id: r.get(0)?,
                ts: r.get(1)?,
                verdict: r.get(2)?,
                tool: r.get(3)?,
                role: r.get(4)?,
                handle: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
                reason: r.get::<_, Option<String>>(6)?.unwrap_or_default(),
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    st.allow_count = count(conn, "SELECT COUNT(*) FROM policy_audit WHERE verdict='ALLOW'")?;
    st.deny_count = count(conn, "SELECT COUNT(*) FROM policy_audit WHERE verdict='DENY'")?;

    // ── driver chat ──
    if table_exists(conn, "chat_log")? {
        let mut cstmt = conn.prepare(
            "SELECT role, ts, text, tone FROM chat_log ORDER BY id ASC LIMIT 60",
        )?;
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
        .map(|(i, r)| PipeStep { uid: (i + 1) as i64, role: r.to_string(), status: "idle".into(), handle: None })
        .collect();

    Ok(st)
}

struct RawAudit {
    id: i64,
    ts: String,
    event: String,
    handle: String,
    role: String,
    parent: String,
    model: String,
    profile: String,
    brief: String,
    allowed_tools: String,
    max_turns: i64,
    turns: i64,
    tools_used: i64,
    status: Option<String>,
    wall_remaining: i64,
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

/// Create the Musubi audit schema on a fresh database.
pub fn init_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_SQL)
}

pub const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS subagent_audit (
  id             INTEGER PRIMARY KEY,
  ts             TEXT NOT NULL,
  event          TEXT NOT NULL,              -- 'spawned' | 'completed'
  handle         TEXT NOT NULL,
  role           TEXT NOT NULL,
  parent         TEXT,
  model          TEXT,
  profile        TEXT,
  brief          TEXT,
  allowed_tools  TEXT,                       -- JSON array of tool names
  max_turns      INTEGER,
  turns          INTEGER,
  tools_used     INTEGER,
  status         TEXT,                        -- running|done|failed|escalated|abandoned
  wall_remaining INTEGER,
  verification_errors INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS policy_audit (
  id      INTEGER PRIMARY KEY,
  ts      TEXT NOT NULL,
  verdict TEXT NOT NULL,                      -- 'ALLOW' | 'DENY'
  tool    TEXT NOT NULL,
  role    TEXT NOT NULL,
  handle  TEXT,
  reason  TEXT
);
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
/// app as a fallback demo DB when no real `audit.db` is configured.
pub fn seed_demo(conn: &Connection) -> rusqlite::Result<()> {
    init_schema(conn)?;
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('active_profile','anthropic.default')", [])?;

    let spawn = |conn: &Connection, id: i64, ts: &str, handle: &str, role: &str, model: &str, profile: &str, brief: &str, tools: &str, max: i64| -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO subagent_audit(id,ts,event,handle,role,parent,model,profile,brief,allowed_tools,max_turns,turns,tools_used,status,wall_remaining)\
             VALUES(?1,?2,'spawned',?3,?4,'driver · agent-loop',?5,?6,?7,?8,?9,0,0,'running',300)",
            rusqlite::params![id, ts, handle, role, model, profile, brief, tools, max],
        )?;
        Ok(())
    };
    let complete = |conn: &Connection, id: i64, ts: &str, handle: &str, role: &str, turns: i64, status: &str| -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO subagent_audit(id,ts,event,handle,role,turns,tools_used,status,wall_remaining)\
             VALUES(?1,?2,'completed',?3,?4,?5,?5,?6,0)",
            rusqlite::params![id, ts, handle, role, turns, status],
        )?;
        Ok(())
    };

    spawn(conn, 1, "14:46:01", "a1b2c3d4", "explorer", "llama3.1", "ollama.local", "Map callers of LMRouter across agent/vendors", r#"["musubi_read_file","musubi_run_command","musubi_retrieve"]"#, 6)?;
    spawn(conn, 2, "14:46:09", "b2c3d4e5", "investigator", "claude-sonnet-4", "anthropic.default", "Reproduce the failing pytest in storage/db.py", r#"["musubi_read_file","musubi_run_command","musubi_query_subagent_events"]"#, 8)?;
    spawn(conn, 3, "14:46:18", "c3d4e5f6", "reviewer-aux", "gpt-5-mini", "openai.default", "Verify the patch touches code only", r#"["musubi_read_file"]"#, 4)?;
    complete(conn, 4, "14:46:31", "a1b2c3d4", "explorer", 6, "done")?;

    let decide = |conn: &Connection, id: i64, ts: &str, verdict: &str, tool: &str, role: &str, handle: &str, reason: &str| -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO policy_audit(id,ts,verdict,tool,role,handle,reason) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            rusqlite::params![id, ts, verdict, tool, role, handle, reason],
        )?;
        Ok(())
    };
    decide(conn, 1, "14:46:02", "ALLOW", "musubi_read_file", "explorer", "a1b2c3d4", "in surface")?;
    decide(conn, 2, "14:46:10", "ALLOW", "musubi_run_command", "investigator", "b2c3d4e5", "in surface")?;
    decide(conn, 3, "14:46:19", "DENY", "musubi_write_file", "reviewer-aux", "c3d4e5f6", "outside firewall surface — code-only (HI #3)")?;
    decide(conn, 4, "14:46:20", "ALLOW", "musubi_read_file", "reviewer-aux", "c3d4e5f6", "in surface")?;

    let say = |conn: &Connection, id: i64, ts: Option<&str>, role: &str, tone: Option<&str>, text: &str| -> rusqlite::Result<()> {
        conn.execute(
            "INSERT INTO chat_log(id,ts,role,tone,text) VALUES(?1,?2,?3,?4,?5)",
            rusqlite::params![id, ts, role, tone, text],
        )?;
        Ok(())
    };
    say(conn, 1, Some("14:46:00"), "you", None, "Audit why run_command is denied for the reviewer. Tie everything to policy.")?;
    say(conn, 2, Some("14:46:00"), "driver", None, "On it. I reach the model through one inject point and spawn governed threads — each turn-capped, firewalled, and bound into the audit.")?;
    say(conn, 3, None, "system", Some("spawn"), "tied explorer · investigator · reviewer-aux into the audit")?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn demo() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        seed_demo(&conn).unwrap();
        conn
    }

    #[test]
    fn builds_cohort_with_running_and_completed() {
        let st = load_state(&demo()).unwrap();
        assert_eq!(st.subagents.len(), 3, "three handles spawned");
        let explorer = st.subagents.iter().find(|a| a.role == "explorer").unwrap();
        assert_eq!(explorer.status, "done", "explorer completed");
        assert_eq!(explorer.turns, 6);
        assert_eq!(explorer.model, "llama3.1");
        assert_eq!(explorer.tools.len(), 3);
        let reviewer = st.subagents.iter().find(|a| a.role == "reviewer-aux").unwrap();
        assert_eq!(reviewer.status, "running");
        assert_eq!(reviewer.max, 4);
    }

    #[test]
    fn counts_match_the_log() {
        let st = load_state(&demo()).unwrap();
        assert_eq!(st.total_spawned, 3);
        assert_eq!(st.total_done, 1);
        assert_eq!(st.allow_count, 3);
        assert_eq!(st.deny_count, 1);
        assert_eq!(st.active_profile, "anthropic.default");
    }

    #[test]
    fn audit_is_newest_first_with_derived_detail() {
        let st = load_state(&demo()).unwrap();
        assert_eq!(st.audit.len(), 4);
        assert!(st.audit[0].id > st.audit[1].id, "newest first");
        let spawned = st.audit.iter().find(|r| r.event == "spawned" && r.handle == "c3d4e5f6").unwrap();
        assert_eq!(spawned.detail, "allowed_tools=[1] max_turns=4");
        assert!(spawned.status.is_none());
        let completed = st.audit.iter().find(|r| r.event == "completed").unwrap();
        assert_eq!(completed.status.as_deref(), Some("done"));
    }

    #[test]
    fn serializes_to_camelcase_json() {
        let st = load_state(&demo()).unwrap();
        let v: serde_json::Value = serde_json::to_value(&st).unwrap();
        assert!(v.get("totalSpawned").is_some());
        assert!(v.get("activeProfile").is_some());
        assert!(v["subagents"][0].get("max").is_some());
        assert!(v["pipeSteps"].as_array().unwrap().len() == 4);
    }

    #[test]
    fn fresh_db_yields_empty_surfaces() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        let st = load_state(&conn).unwrap();
        assert_eq!(st.subagents.len(), 0);
        assert_eq!(st.total_spawned, 0);
        assert_eq!(st.active_profile, "anthropic.default");
    }
}
