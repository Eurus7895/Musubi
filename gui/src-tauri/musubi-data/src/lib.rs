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
    pub agent_turns: Vec<AgentTurn>,
    pub pipeline_runs: Vec<PipelineRun>,
    pub pipeline_catalog: Vec<PipelineCatalogEntry>,
    pub orchestrator_chat_id: String,
    pub pipeline_chat_id: String,
    pub events: Vec<serde_json::Value>,
    pub policy: Vec<Decision>,
    pub audit: Vec<AuditRow>,
    pub chat: Vec<ChatMsg>,
    // The Pipeline studio drives its own session; its conversation is scoped
    // by `chat_log.surface = 'pipeline'` and surfaced separately from `chat`.
    pub pipe_chat: Vec<ChatMsg>,
    pub total_spawned: i64,
    pub total_done: i64,
    pub allow_count: i64,
    pub deny_count: i64,
    pub active_profile: String,
    pub profiles: Vec<LmProfile>,
    pub pipe_steps: Vec<PipeStep>,
    pub pipe_name: String,
    pub pipe_running: bool,
    pub pipe_cur: i64,
    pub pipe_prog: i64,
    pub pipe_done_flag: bool,
    pub paused: bool,
    pub runtime_source: String,
    pub setup_status: SetupStatus,
    pub driver_status: DriverStatus,
    pub t: i64,
}

/// Runtime overlay for the on-demand task launcher. The GUI spawns one governed
/// `agent "<task>"` process only when the user presses Run; this snapshot is a
/// console-side view of that child process, not orchestration state — the audit
/// DB stays the source of truth.
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

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct DriverStatus {
    pub running: bool,
    pub surface: String,
    pub pipeline_name: String,
    pub terminal_status: String,
    pub task: String,
    pub started_at: Option<i64>,
    pub stdout_tail: String,
    pub stderr_tail: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LmProfile {
    pub name: String,
    pub family: String,
    pub model: String,
    pub transport: String,
    pub endpoint: String,
    pub key_env: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineCatalogEntry {
    pub name: String,
    pub description: String,
    pub stages: Vec<String>,
    pub runnable: bool,
    pub blocked_reason: String,
}

const STUDIO_PIPELINES: [&str; 2] = ["feature-dev", "dev-lite"];

/// Load the deterministic pipelines the standalone linear runner supports.
/// This intentionally avoids a second YAML dependency: Studio needs only the
/// stable name/description/stage fields from the two supported recipe shapes.
pub fn read_studio_pipeline_catalog(project_root: &Path) -> Vec<PipelineCatalogEntry> {
    STUDIO_PIPELINES
        .iter()
        .filter_map(|name| {
            let path = project_root
                .join(".github")
                .join("pipelines")
                .join(name)
                .join("pipeline.yaml");
            let raw = std::fs::read_to_string(path).ok()?;
            parse_studio_pipeline(&raw).filter(|entry| entry.name == *name && entry.runnable)
        })
        .collect()
}

fn parse_studio_pipeline(raw: &str) -> Option<PipelineCatalogEntry> {
    let mut name = String::new();
    let mut description = String::new();
    let mut stages = Vec::new();
    let mut section = "";

    for line in raw.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let indent = line.len().saturating_sub(line.trim_start().len());
        if indent == 0 {
            if let Some(value) = trimmed.strip_prefix("name:") {
                name = value.trim().to_string();
                continue;
            }
            if let Some(value) = trimmed.strip_prefix("description:") {
                description = value.trim().to_string();
                continue;
            }
            section = match trimmed {
                "stages:" => "stages",
                "generator:" => "generator",
                "evaluator:" => "evaluator",
                _ => "",
            };
            continue;
        }
        match section {
            "stages" => {
                if let Some(value) = trimmed.strip_prefix("- preset:") {
                    stages.push(value.trim().to_string());
                }
            }
            "generator" => {
                if let Some(value) = trimmed.strip_prefix("- name:") {
                    stages.push(value.trim().to_string());
                }
            }
            "evaluator" => {
                if let Some(value) = trimmed.strip_prefix("stage:") {
                    stages.push(value.trim().to_string());
                    section = "";
                } else if let Some(value) = trimmed.strip_prefix("name:") {
                    stages.push(value.trim().to_string());
                    section = "";
                } else if let Some(value) = trimmed.strip_prefix("agent:") {
                    let file = Path::new(value.trim()).file_name()?.to_str()?;
                    let role = file.strip_suffix(".agent.md").unwrap_or(file);
                    stages.push(role.to_string());
                    section = "";
                }
            }
            _ => {}
        }
    }

    let runnable = valid_pipeline_name(&name) && stages.len() >= 2;
    Some(PipelineCatalogEntry {
        name,
        description,
        stages,
        runnable,
        blocked_reason: if runnable {
            String::new()
        } else {
            "Pipeline must resolve to at least two safe stages.".into()
        },
    })
}

pub fn valid_pipeline_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedAuditDb {
    pub path: PathBuf,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedStateDb {
    pub path: PathBuf,
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
    pub parent_session: String,
    pub parent_agent: String,
    // Owning GUI session (serialized `chatId`), resolved by joining the run's
    // parent_session to agent_turns.chat_id. Lets the UI scope runs to the
    // Orchestrator vs the Pipeline studio surface (chat_id prefix). Empty when
    // no agent_turns row maps the session — treated as Orchestrator.
    pub chat_id: String,
    // Spawn time (epoch seconds), serialized as `spawnEpoch`. The Orchestrator
    // uses it to sort runs by real chronology across worker sessions and
    // driver-only turns, which live in separate audit tables.
    pub spawn_epoch: Option<i64>,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AgentTurn {
    pub id: i64,
    pub chat_id: String,
    pub parent_session: String,
    // Turn start time (epoch seconds), serialized as `startedAt`. Lets the
    // Orchestrator order driver-only turns against worker sessions by real time.
    pub started_at: f64,
    pub model_family: String,
    pub cycles: i64,
    pub tokens_in_estimate: i64,
    pub tokens_out_estimate: i64,
    // How much prior conversation this turn replayed as its seed. 0 for a
    // stateless turn; large replay is the dominant cost of long GUI sessions.
    pub replay_messages: i64,
    pub replay_tokens: i64,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRun {
    pub session_id: String,
    pub chat_id: String,
    pub pipeline_name: String,
    pub brief: String,
    pub started_at: f64,
    pub ended_at: Option<f64>,
    pub status: String,
    pub stages: Vec<Agent>,
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
    // Keep the standalone reader backwards-compatible for fixtures that keep
    // the observability tables together. The desktop shell explicitly calls
    // `load_state_with_pipeline_runs` with the sibling state DB instead.
    load_state_at_with_pipeline_runs(conn, Some(conn), current_epoch_secs())
}

/// Read console state from the append-only audit ledger and, when available,
/// join pipeline lifecycle rows from Musubi's sibling state database.
///
/// `pipeline_runs` belongs to `musubi.db`, not the audit ledger. A missing
/// state connection is valid (for first run or an older workspace) and yields
/// no pipeline run cards rather than guessing from audit rows.
pub fn load_state_with_pipeline_runs(
    audit_conn: &Connection,
    state_conn: Option<&Connection>,
) -> rusqlite::Result<State> {
    load_state_at_with_pipeline_runs(audit_conn, state_conn, current_epoch_secs())
}

/// Return only real pipeline runs joined to their audit-envelope ancestry.
pub fn load_pipeline_runs(
    audit_conn: &Connection,
    state_conn: Option<&Connection>,
) -> rusqlite::Result<Vec<PipelineRun>> {
    Ok(load_state_with_pipeline_runs(audit_conn, state_conn)?.pipeline_runs)
}

#[cfg(test)]
fn load_state_at(conn: &Connection, now_epoch: i64) -> rusqlite::Result<State> {
    load_state_at_with_pipeline_runs(conn, Some(conn), now_epoch)
}

fn load_state_at_with_pipeline_runs(
    conn: &Connection,
    pipeline_state_conn: Option<&Connection>,
    now_epoch: i64,
) -> rusqlite::Result<State> {
    let mut st = State {
        active_profile: read_active_profile(conn),
        profiles: read_llm_profiles(),
        pipe_name: String::new(),
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
    let mut pipeline_envelopes: std::collections::HashMap<String, PipelineEnvelope> =
        std::collections::HashMap::new();
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
        let is_pipeline_marker = row.role.starts_with("pipeline:");
        if row.event == "spawned" {
            if is_pipeline_marker {
                pipeline_envelopes.insert(
                    row.handle.clone(),
                    PipelineEnvelope {
                        parent_session: row.parent_session.clone(),
                        brief: row.brief.clone(),
                    },
                );
            }
            if !is_pipeline_marker {
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
                        parent_session: row.parent_session.clone(),
                        parent_agent: row.parent_agent.clone(),
                        chat_id: String::new(), // backfilled from agent_turns below
                        spawn_epoch: row.ts_epoch,
                    },
                );
            }
        } else if row.event == "completed" {
            let status = row.final_status.clone().unwrap_or_else(|| "done".into());
            if !is_pipeline_marker && status == "done" {
                st.total_done += 1;
            }
            if !is_pipeline_marker {
                if let Some(a) = agents.get_mut(&row.handle) {
                    a.status = status.clone();
                    a.turns = row.turns.max(a.turns);
                }
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

    // ── driver chat, split by surface (Orchestrator vs Pipeline studio) ──
    // Backward-compatible: on a pre-migration DB with no `surface` column,
    // every row is treated as the Orchestrator surface.
    if table_exists(conn, "chat_log")? {
        let has_surface = column_exists(conn, "chat_log", "surface")?;
        let surface_expr = if has_surface {
            "COALESCE(surface, 'orchestrator')"
        } else {
            "'orchestrator'"
        };
        let mut cstmt = conn.prepare(&format!(
            "SELECT role, ts, text, tone, {surface_expr} FROM chat_log ORDER BY id ASC LIMIT 120"
        ))?;
        let rows = cstmt
            .query_map([], |r| {
                Ok((
                    ChatMsg {
                        role: r.get(0)?,
                        ts: r.get::<_, Option<String>>(1)?,
                        text: r.get(2)?,
                        tone: r.get::<_, Option<String>>(3)?,
                    },
                    r.get::<_, String>(4)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (msg, surface) in rows {
            if surface == "pipeline" {
                st.pipe_chat.push(msg);
            } else {
                st.chat.push(msg);
            }
        }
        // Each surface still shows its most recent ~60 messages.
        trim_front(&mut st.chat, 60);
        trim_front(&mut st.pipe_chat, 60);
    }

    // ── pipeline studio default (authoring surface; not from the audit) ──
    // Driver turn metadata is operational state, just like pipeline_runs. In
    // production it lives in the sibling musubi.db rather than audit.db.
    let agent_turn_conn = pipeline_state_conn.unwrap_or(conn);
    if table_exists(agent_turn_conn, "agent_turns")? {
        // Replay columns are recent; older audit DBs lack them. Select constant
        // zeros in that case so the reader tolerates a pre-migration DB.
        let has_replay = column_exists(agent_turn_conn, "agent_turns", "replay_tokens")?;
        let sql = if has_replay {
            "SELECT id, chat_id, parent_session_id, started_at, model_family, cycles, \
                    tokens_in_estimate, tokens_out_estimate, replay_messages, replay_tokens \
             FROM agent_turns ORDER BY id ASC LIMIT 120"
        } else {
            "SELECT id, chat_id, parent_session_id, started_at, model_family, cycles, \
                    tokens_in_estimate, tokens_out_estimate, 0 AS replay_messages, \
                    0 AS replay_tokens \
             FROM agent_turns ORDER BY id ASC LIMIT 120"
        };
        let mut tstmt = agent_turn_conn.prepare(sql)?;
        st.agent_turns = tstmt
            .query_map([], |r| {
                Ok(AgentTurn {
                    id: r.get(0)?,
                    chat_id: r.get(1)?,
                    parent_session: r.get(2)?,
                    started_at: r.get(3)?,
                    model_family: r.get(4)?,
                    cycles: r.get(5)?,
                    tokens_in_estimate: r.get(6)?,
                    tokens_out_estimate: r.get(7)?,
                    replay_messages: r.get(8)?,
                    replay_tokens: r.get(9)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
    }

    // ── tag each run with its owning session ──
    // subagent_audit has no chat_id; agent_turns maps parent_session → chat_id.
    // The UI scopes runs to a surface by the chat_id prefix (gui-pipeline-*).
    let session_to_chat: std::collections::HashMap<String, String> = st
        .agent_turns
        .iter()
        .filter(|t| !t.chat_id.is_empty() && !t.parent_session.is_empty())
        .map(|t| (t.parent_session.clone(), t.chat_id.clone()))
        .collect();
    for agent in &mut st.subagents {
        if let Some(chat_id) = session_to_chat.get(&agent.parent_session) {
            agent.chat_id = chat_id.clone();
        }
    }

    if let Some(state_conn) = pipeline_state_conn {
        if table_exists(state_conn, "pipeline_runs")? {
            let mut pipeline_session_to_chat = std::collections::HashMap::new();
            if column_exists(state_conn, "pipeline_runs", "chat_id")? {
                let mut chat_stmt = state_conn.prepare(
                    "SELECT session_id, COALESCE(chat_id, '') FROM pipeline_runs",
                )?;
                pipeline_session_to_chat = chat_stmt
                    .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                    .collect::<rusqlite::Result<std::collections::HashMap<_, _>>>()?;
            }
            let mut pstmt = state_conn.prepare(
                "SELECT session_id, pipeline_name, started_at, ended_at, final_status \
             FROM pipeline_runs ORDER BY started_at ASC",
            )?;
            st.pipeline_runs = pstmt
                .query_map([], |r| {
                    let session_id: String = r.get(0)?;
                    // `state.create_session()` also records the outer driver
                    // session. Only the child whose ID is an audited
                    // `pipeline:<name>` envelope represents a runnable pipeline.
                    let Some(envelope) = pipeline_envelopes.get(&session_id) else {
                        return Ok(None);
                    };
                    let chat_id = session_to_chat
                        .get(&envelope.parent_session)
                        .or_else(|| pipeline_session_to_chat.get(&envelope.parent_session))
                        .cloned()
                        .unwrap_or_default();
                    let mut stages = st
                        .subagents
                        .iter()
                        .filter(|agent| agent.parent_session == session_id)
                        .cloned()
                        .collect::<Vec<_>>();
                    for stage in &mut stages {
                        stage.chat_id = chat_id.clone();
                    }
                    let recorded_status = r.get::<_, Option<String>>(4)?;
                    let status = recorded_status.unwrap_or_else(|| derive_pipeline_status(&stages));
                    Ok(Some(PipelineRun {
                        session_id,
                        chat_id,
                        pipeline_name: r.get(1)?,
                        brief: envelope.brief.clone(),
                        started_at: r.get(2)?,
                        ended_at: r.get(3)?,
                        status,
                        stages,
                    }))
                })?
                .collect::<rusqlite::Result<Vec<Option<_>>>>()?
                .into_iter()
                .flatten()
                .collect();
        }
    }

    let pipeline_sessions = st
        .pipeline_runs
        .iter()
        .map(|run| run.session_id.as_str())
        .collect::<std::collections::HashSet<_>>();
    st.subagents
        .retain(|agent| !pipeline_sessions.contains(agent.parent_session.as_str()));

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

struct PipelineEnvelope {
    parent_session: String,
    brief: String,
}

fn derive_pipeline_status(stages: &[Agent]) -> String {
    if stages.is_empty() || stages.iter().any(|stage| stage.status == "running") {
        return "running".into();
    }
    if stages.iter().all(|stage| stage.status == "done") {
        return "success".into();
    }
    if stages.iter().any(|stage| stage.status == "escalated") {
        return "escalated".into();
    }
    "aborted".into()
}

/// Active LMRouter profile: an explicit console choice wins, else the
/// `default` recorded in `.musubi/llm.json` (the runner's source of truth),
/// else a conservative fallback.
pub fn read_active_profile(conn: &Connection) -> String {
    read_active_profile_for_config(conn, None)
}

pub fn read_active_profile_for_config(conn: &Connection, llm_config_path: Option<&Path>) -> String {
    if let Some(p) = read_meta(conn, "active_profile") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(path) = llm_config_path {
        if let Some(p) = read_llm_default_from_path(path) {
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
    read_llm_default_from_path(path)
}

pub fn read_llm_default_from_path(path: impl AsRef<Path>) -> Option<String> {
    let txt = std::fs::read_to_string(path).ok()?;
    let v: serde_json::Value = serde_json::from_str(&txt).ok()?;
    v.get("default")?.as_str().map(str::to_string)
}

fn read_llm_profiles() -> Vec<LmProfile> {
    let Some(path) = std::env::var("MUSUBI_LLM_CONFIG")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .or_else(find_llm_json_near_db)
    else {
        return vec![];
    };
    read_llm_profiles_from_path(path)
}

pub fn read_llm_profiles_from_path(path: impl AsRef<Path>) -> Vec<LmProfile> {
    let Ok(txt) = std::fs::read_to_string(path) else {
        return vec![];
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt) else {
        return vec![];
    };
    parse_llm_profiles(&v)
}

fn parse_llm_profiles(v: &serde_json::Value) -> Vec<LmProfile> {
    let Some(root) = v.as_object() else {
        return vec![];
    };
    let mut profiles = Vec::new();
    for (family, family_value) in root {
        if family == "default" || family.starts_with("//") {
            continue;
        }
        let Some(family_profiles) = family_value.as_object() else {
            continue;
        };
        for (profile, config) in family_profiles {
            if profile.starts_with("//") {
                continue;
            }
            let Some(config) = config.as_object() else {
                continue;
            };
            let field = |name: &str| {
                config
                    .get(name)
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            let model = first_nonempty(&[field("model"), field("deployment")]);
            let transport =
                first_nonempty(&[field("transport"), default_transport(family, config)]);
            let endpoint = first_nonempty(&[
                field("base_url"),
                field("azure_endpoint"),
                field("endpoint"),
                default_endpoint(family),
            ]);
            let key_env = first_nonempty(&[
                field("api_key_env"),
                field("key_env"),
                if config.get("api_key").and_then(|v| v.as_str()).is_some() {
                    "inline key".to_string()
                } else {
                    String::new()
                },
            ]);
            profiles.push(LmProfile {
                name: format!("{family}.{profile}"),
                family: family.to_string(),
                model,
                transport,
                endpoint,
                key_env,
            });
        }
    }
    profiles
}

fn first_nonempty(values: &[String]) -> String {
    values
        .iter()
        .map(|s| s.trim())
        .find(|s| !s.is_empty())
        .unwrap_or("")
        .to_string()
}

fn default_transport(family: &str, config: &serde_json::Map<String, serde_json::Value>) -> String {
    if config.get("transport").and_then(|v| v.as_str()).is_some() {
        return String::new();
    }
    match family {
        "ollama" => "local",
        "azure" => "curl",
        _ => "SDK",
    }
    .to_string()
}

fn default_endpoint(family: &str) -> String {
    match family {
        "anthropic" => "api.anthropic.com",
        "deepseek" => "api.deepseek.com",
        "openai" => "api.openai.com",
        "ollama" => "127.0.0.1:11434",
        _ => "",
    }
    .to_string()
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

/// True when `table` has a column named `col`. Used to stay backward-compatible
/// with audit DBs created before a column was added (e.g. `chat_log.surface`).
fn column_exists(conn: &Connection, table: &str, col: &str) -> rusqlite::Result<bool> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        if row.get::<_, String>(1)? == col {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Keep only the newest `cap` items, dropping from the front.
fn trim_front<T>(v: &mut Vec<T>, cap: usize) {
    if v.len() > cap {
        v.drain(0..v.len() - cap);
    }
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
  id      INTEGER PRIMARY KEY,
  ts      TEXT,
  role    TEXT,                                -- 'you' | 'driver' | 'system'
  tone    TEXT,
  text    TEXT,
  surface TEXT NOT NULL DEFAULT 'orchestrator' -- 'orchestrator' | 'pipeline'
);
CREATE TABLE IF NOT EXISTS agent_turns (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id              TEXT NOT NULL,
  parent_session_id    TEXT NOT NULL,
  started_at           REAL NOT NULL,
  ended_at             REAL,
  model_family         TEXT NOT NULL,
  cycles               INTEGER NOT NULL DEFAULT 0,
  tokens_in_estimate   INTEGER NOT NULL DEFAULT 0,
  tokens_out_estimate  INTEGER NOT NULL DEFAULT 0,
  lm_ms                INTEGER NOT NULL DEFAULT 0,
  total_ms             INTEGER NOT NULL DEFAULT 0,
  replay_messages      INTEGER NOT NULL DEFAULT 0,
  replay_tokens        INTEGER NOT NULL DEFAULT 0,
  schema_version       TEXT NOT NULL DEFAULT 'v1'
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  session_id              TEXT PRIMARY KEY,
  pipeline_name           TEXT NOT NULL,
  chat_id                 TEXT,
  started_at              REAL NOT NULL,
  ended_at                REAL,
  final_status            TEXT,
  total_tokens_estimate   INTEGER NOT NULL DEFAULT 0,
  correction_attempts     INTEGER NOT NULL DEFAULT 0,
  escalated               INTEGER NOT NULL DEFAULT 0,
  chunked                 INTEGER NOT NULL DEFAULT 0,
  chunk_count             INTEGER NOT NULL DEFAULT 0,
  schema_version          TEXT NOT NULL DEFAULT 'v1'
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
        // spawnEpoch is now serialized so the UI can sort runs chronologically.
        assert!(v["subagents"][0].get("spawnEpoch").is_some());
        assert!(v["subagents"][0].get("max").is_some());
        assert!(v["pipeSteps"].as_array().unwrap().is_empty());
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
    fn pipeline_markers_do_not_count_as_subagents() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(1,1000.0,'spawned','pipe-1','parent-1','driver','pipeline:dev-lite','build a thing','[]',3,0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(2,1001.0,'spawned','worker-1','pipe-1','pipeline:dev-lite','planner','build a thing','[]',5,300)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,final_status,turns,tools_used)\
             VALUES(3,1002.0,'completed','worker-1','pipe-1','pipeline:dev-lite','planner','build a thing','done',1,'[]')",
            [],
        )
        .unwrap();

        let st = load_state_at(&conn, 2000).unwrap();

        assert_eq!(st.total_spawned, 1);
        assert_eq!(st.total_done, 1);
        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].handle, "worker-1");
        assert!(!st.subagents.iter().any(|a| a.role.starts_with("pipeline:")));
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

    #[test]
    fn loads_agent_turns_for_driver_only_runs() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO agent_turns\
             (id,chat_id,parent_session_id,started_at,model_family,cycles,tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)\
             VALUES(42,'chat-a','direct-session',1000.0,'deepseek',1,100,20,300,500)",
            [],
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_turns.len(), 1);
        assert_eq!(st.agent_turns[0].parent_session, "direct-session");
        assert_eq!(st.agent_turns[0].cycles, 1);
        // A row inserted without the replay columns reads back as 0.
        assert_eq!(st.agent_turns[0].replay_messages, 0);
        assert_eq!(st.agent_turns[0].replay_tokens, 0);
    }

    #[test]
    fn agent_turns_surface_replay_seed_cost() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO agent_turns\
             (id,chat_id,parent_session_id,started_at,model_family,cycles,\
              tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms,\
              replay_messages,replay_tokens)\
             VALUES(7,'gui-orchestrator-abc-1','s',1.0,'deepseek',3,900,80,10,20,49,48120)",
            [],
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_turns[0].replay_messages, 49);
        assert_eq!(st.agent_turns[0].replay_tokens, 48120);
    }

    #[test]
    fn chat_log_splits_by_surface() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,tone,text,surface) \
             VALUES('t','you',NULL,'orch hi','orchestrator')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO chat_log(ts,role,tone,text,surface) \
             VALUES('t','you',NULL,'pipe hi','pipeline')",
            [],
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(
            st.chat.iter().map(|m| m.text.as_str()).collect::<Vec<_>>(),
            ["orch hi"]
        );
        assert_eq!(
            st.pipe_chat
                .iter()
                .map(|m| m.text.as_str())
                .collect::<Vec<_>>(),
            ["pipe hi"]
        );
    }

    #[test]
    fn subagent_chat_id_resolved_from_agent_turns() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        // The driver turn maps parent_session 'sess-1' to a pipeline chat_id.
        conn.execute(
            "INSERT INTO agent_turns\
             (id,chat_id,parent_session_id,started_at,model_family,cycles,tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)\
             VALUES(1,'gui-pipeline-abc','sess-1',1000.0,'deepseek',1,0,0,0,0)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(1,1000.0,'spawned','h1','sess-1','agent','coder','do it','[]',10,300)",
            [],
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].chat_id, "gui-pipeline-abc");
    }

    #[test]
    fn pipeline_run_ancestry_resolves_exact_chat_and_child_stages() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO agent_turns\
             (chat_id,parent_session_id,started_at,ended_at,model_family,cycles,\
              tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)\
             VALUES ('gui-pipeline-current','outer-session',100,110,'test',1,1,1,1,10)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO pipeline_runs\
             (session_id,pipeline_name,started_at,ended_at,final_status)\
             VALUES ('pipeline-session','feature-dev',101,109,NULL)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
              allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES (101,'spawned','pipeline-session','outer-session','agent',\
                     'pipeline:feature-dev','ship it','[]',2,0)",
            [],
        )
        .unwrap();
        for (id, role, ts) in [("stage-plan", "planner", 102), ("stage-code", "coder", 104)] {
            conn.execute(
                "INSERT INTO subagent_audit\
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
                  allowed_tools,max_turns,wall_clock_timeout_s)\
                 VALUES (?1,'spawned',?2,'pipeline-session','pipeline:feature-dev',?3,\
                         'stage brief','[]',8,60)",
                rusqlite::params![ts, id, role],
            )
            .unwrap();
            conn.execute(
                "INSERT INTO subagent_audit\
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
                  final_status,turns,tools_used)\
                 VALUES (?1,'completed',?2,'pipeline-session','pipeline:feature-dev',?3,\
                         'stage brief','done',2,'[]')",
                rusqlite::params![ts + 1, id, role],
            )
            .unwrap();
        }

        let st = load_state_at(&conn, 120).unwrap();

        assert_eq!(st.pipeline_runs.len(), 1);
        let run = &st.pipeline_runs[0];
        assert_eq!(run.session_id, "pipeline-session");
        assert_eq!(run.chat_id, "gui-pipeline-current");
        assert_eq!(run.pipeline_name, "feature-dev");
        assert_eq!(run.brief, "ship it");
        assert_eq!(run.status, "success");
        assert_eq!(
            run.stages
                .iter()
                .map(|stage| stage.role.as_str())
                .collect::<Vec<_>>(),
            vec!["planner", "coder"]
        );
        assert!(
            st.subagents.is_empty(),
            "pipeline descendants stay out of Orchestrator"
        );
    }

    #[test]
    fn pipeline_runs_join_state_db_to_audit_ancestry() {
        let root = temp_dir("pipeline-runs-two-dbs");
        let audit_path = root.join("audit.db");
        let state_path = root.join("musubi.db");
        let audit = Connection::open(&audit_path).unwrap();
        let state = Connection::open(&state_path).unwrap();
        init_schema(&audit).unwrap();
        init_schema(&state).unwrap();

        state
            .execute(
                "INSERT INTO agent_turns\
             (chat_id,parent_session_id,started_at,ended_at,model_family,cycles,\
              tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)\
             VALUES ('gui-pipeline-current','outer-session',100,110,'test',1,1,1,1,10)",
                [],
            )
            .unwrap();
        audit
            .execute(
                "INSERT INTO subagent_audit\
             (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
              allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES (101,'spawned','pipeline-session','outer-session','agent',\
                     'pipeline:feature-dev','ship it','[]',2,0)",
                [],
            )
            .unwrap();
        for (handle, role, ts) in [("stage-plan", "planner", 102), ("stage-code", "coder", 104)] {
            audit
                .execute(
                    "INSERT INTO subagent_audit\
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
                  allowed_tools,max_turns,wall_clock_timeout_s)\
                 VALUES (?1,'spawned',?2,'pipeline-session','pipeline:feature-dev',?3,\
                         'stage brief','[\\\"musubi_read_file\\\"]',8,60)",
                    rusqlite::params![ts, handle, role],
                )
                .unwrap();
            audit
                .execute(
                    "INSERT INTO subagent_audit\
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
                  final_status,turns,tools_used)\
                 VALUES (?1,'completed',?2,'pipeline-session','pipeline:feature-dev',?3,\
                         'stage brief','done',2,'[\\\"musubi_read_file\\\"]')",
                    rusqlite::params![ts + 1, handle, role],
                )
                .unwrap();
        }
        state
            .execute(
                "INSERT INTO pipeline_runs\
             (session_id,pipeline_name,started_at,ended_at,final_status)\
             VALUES ('pipeline-session','feature-dev',101,109,'success')",
                [],
            )
            .unwrap();
        // The root driver's own state session must not become a second card.
        state
            .execute(
                "INSERT INTO pipeline_runs\
             (session_id,pipeline_name,started_at,ended_at,final_status)\
             VALUES ('outer-session','feature-dev',100,110,'success')",
                [],
            )
            .unwrap();

        let joined = load_state_with_pipeline_runs(&audit, Some(&state)).unwrap();
        assert_eq!(joined.pipeline_runs.len(), 1);
        let run = &joined.pipeline_runs[0];
        assert_eq!(run.session_id, "pipeline-session");
        assert_eq!(run.chat_id, "gui-pipeline-current");
        assert_eq!(run.status, "success");
        assert_eq!(run.brief, "ship it");
        assert_eq!(run.stages.len(), 2);
        assert!(joined.subagents.is_empty());

        let without_state = load_state_with_pipeline_runs(&audit, None).unwrap();
        assert!(without_state.pipeline_runs.is_empty());
        assert_eq!(without_state.subagents.len(), 2);
    }

    #[test]
    fn pipeline_run_keeps_chat_scope_when_driver_never_finishes() {
        let root = temp_dir("pipeline-run-live-chat-scope");
        let audit = Connection::open(root.join("audit.db")).unwrap();
        let state = Connection::open(root.join("musubi.db")).unwrap();
        init_schema(&audit).unwrap();
        init_schema(&state).unwrap();
        audit
            .execute(
                "INSERT INTO subagent_audit\
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,\
                  allowed_tools,max_turns,wall_clock_timeout_s)\
                 VALUES (101,'spawned','pipeline-session','outer-session','agent',\
                         'pipeline:feature-dev','ship it','[]',2,0)",
                [],
            )
            .unwrap();
        state
            .execute(
                "INSERT INTO pipeline_runs (session_id,pipeline_name,started_at,chat_id)\
                 VALUES ('outer-session','feature-dev',100,'gui-pipeline-current')",
                [],
            )
            .unwrap();
        state
            .execute(
                "INSERT INTO pipeline_runs (session_id,pipeline_name,started_at)\
                 VALUES ('pipeline-session','feature-dev',101)",
                [],
            )
            .unwrap();

        let joined = load_state_with_pipeline_runs(&audit, Some(&state)).unwrap();
        assert_eq!(joined.pipeline_runs.len(), 1);
        assert_eq!(joined.pipeline_runs[0].chat_id, "gui-pipeline-current");
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
    fn resolve_state_db_uses_existing_sibling_of_audit_ledger() {
        let root = temp_dir("state-db");
        let storage = root.join("storage");
        std::fs::create_dir_all(&storage).unwrap();
        let audit = ResolvedAuditDb {
            path: storage.join("audit.db"),
            source: "workspace".into(),
        };

        assert!(resolve_state_db_path(&audit).is_none());
        std::fs::write(storage.join("musubi.db"), "").unwrap();

        let state = resolve_state_db_path(&audit).expect("sibling state DB");
        assert_eq!(state.path, storage.join("musubi.db"));
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

    #[test]
    fn read_llm_profiles_from_path_parses_project_profiles() {
        let root = temp_dir("llm-profiles");
        let cfg = root.join("llm.json");
        std::fs::write(
            &cfg,
            r#"{
              "default": "deepseek.cloud",
              "deepseek": {
                "cloud": {
                  "model": "deepseek-v4-flash",
                  "api_key_env": "DEEPSEEK_API_KEY"
                }
              },
              "azure": {
                "work": {
                  "transport": "curl",
                  "azure_endpoint": "https://example.openai.azure.com",
                  "deployment": "gpt-4o",
                  "api_key_env": "AZURE_OPENAI_API_KEY"
                }
              }
            }"#,
        )
        .unwrap();

        let profiles = read_llm_profiles_from_path(&cfg);

        assert!(profiles.iter().any(|p| {
            p.name == "deepseek.cloud"
                && p.family == "deepseek"
                && p.model == "deepseek-v4-flash"
                && p.endpoint == "api.deepseek.com"
        }));
        assert!(profiles.iter().any(|p| {
            p.name == "azure.work"
                && p.transport == "curl"
                && p.model == "gpt-4o"
                && p.endpoint == "https://example.openai.azure.com"
        }));
    }

    #[test]
    fn active_profile_uses_detected_config_default_when_meta_is_empty() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        let root = temp_dir("active-profile-default");
        let cfg = root.join("llm.json");
        std::fs::write(&cfg, r#"{"default":"ollama.local"}"#).unwrap();

        assert_eq!(
            read_active_profile_for_config(&conn, Some(&cfg)),
            "ollama.local"
        );
    }

    #[test]
    fn active_profile_meta_wins_over_detected_config_default() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('active_profile','azure.work')",
            [],
        )
        .unwrap();
        let root = temp_dir("active-profile-meta");
        let cfg = root.join("llm.json");
        std::fs::write(&cfg, r#"{"default":"ollama.local"}"#).unwrap();

        assert_eq!(
            read_active_profile_for_config(&conn, Some(&cfg)),
            "azure.work"
        );
    }

    #[test]
    fn default_state_omits_task_launcher_overlay() {
        let st = demo_state();
        let v: serde_json::Value = serde_json::to_value(&st).unwrap();
        assert!(v.get("taskLauncher").is_none());
    }

    #[test]
    fn launch_spec_places_task_first_with_stable_tool_surface() {
        let root = PathBuf::from("/proj");
        let spec = build_agent_launch_spec(
            "add a health endpoint",
            "",
            "anthropic.default",
            None,
            &root,
            &std::collections::HashMap::new(),
            None,
            None,
        )
        .unwrap();

        assert_eq!(spec.program, PathBuf::from("agent"));
        assert_eq!(
            spec.args,
            vec!["add a health endpoint", "--tool-surface", "agent"]
        );
        assert_eq!(spec.cwd, root);
        assert!(spec.env.is_empty());
    }

    #[test]
    fn launch_spec_adds_profile_only_when_it_differs_from_default() {
        let root = PathBuf::from("/proj");
        let with = build_agent_launch_spec(
            "task",
            "azure.work",
            "anthropic.default",
            None,
            &root,
            &std::collections::HashMap::new(),
            None,
            None,
        )
        .unwrap();
        assert_eq!(
            with.args,
            vec!["task", "--profile", "azure.work", "--tool-surface", "agent"]
        );

        let same = build_agent_launch_spec(
            "task",
            "anthropic.default",
            "anthropic.default",
            None,
            &root,
            &std::collections::HashMap::new(),
            None,
            None,
        )
        .unwrap();
        assert_eq!(same.args, vec!["task", "--tool-surface", "agent"]);
    }

    #[test]
    fn launch_spec_adds_chat_id_for_replay() {
        let root = PathBuf::from("/proj");
        let spec = build_agent_launch_spec(
            "task",
            "",
            "",
            None,
            &root,
            &std::collections::HashMap::new(),
            Some("gui-orchestrator"),
            None,
        )
        .unwrap();

        assert_eq!(
            spec.args,
            vec![
                "task",
                "--chat-id",
                "gui-orchestrator",
                "--tool-surface",
                "agent"
            ]
        );
    }

    #[test]
    fn launch_spec_adds_pipeline_for_deterministic_studio_run() {
        let root = PathBuf::from("/proj");
        let spec = build_agent_launch_spec(
            "ship it",
            "",
            "",
            None,
            &root,
            &std::collections::HashMap::new(),
            Some("gui-pipeline-abc"),
            Some("feature-dev"),
        )
        .unwrap();

        assert_eq!(
            spec.args,
            vec![
                "ship it",
                "--chat-id",
                "gui-pipeline-abc",
                "--pipeline",
                "feature-dev",
                "--tool-surface",
                "agent"
            ]
        );
    }

    #[test]
    fn studio_pipeline_catalog_reads_only_supported_registered_recipes() {
        let root = temp_dir("studio-pipeline-catalog");
        let pipeline_root = root.join(".github").join("pipelines");
        let feature = pipeline_root.join("feature-dev");
        let lite = pipeline_root.join("dev-lite");
        let review = pipeline_root.join("code-review");
        std::fs::create_dir_all(&feature).unwrap();
        std::fs::create_dir_all(&lite).unwrap();
        std::fs::create_dir_all(&review).unwrap();
        std::fs::write(
            feature.join("pipeline.yaml"),
            "name: feature-dev\ndescription: Ship a feature\ngenerator:\n  agents:\n    - name: planner\n    - name: coder\nevaluator:\n  name: reviewer\n",
        )
        .unwrap();
        std::fs::write(
            lite.join("pipeline.yaml"),
            "name: dev-lite\ndescription: Lightweight delivery\nstages:\n  - preset: plan\n  - preset: build\n  - preset: check\n",
        )
        .unwrap();
        std::fs::write(
            review.join("pipeline.yaml"),
            "name: code-review\ndescription: Unsupported fan-out\nstages:\n  - preset: scope\n  - preset: findings\n  - preset: synthesis\n",
        )
        .unwrap();

        let catalog = read_studio_pipeline_catalog(&root);

        assert_eq!(
            catalog.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(),
            vec!["feature-dev", "dev-lite"]
        );
        assert_eq!(catalog[0].description, "Ship a feature");
        assert_eq!(catalog[0].stages, vec!["planner", "coder", "reviewer"]);
        assert_eq!(catalog[1].stages, vec!["plan", "build", "check"]);
        assert!(catalog.iter().all(|p| p.runnable));
    }

    #[test]
    fn launch_spec_uses_detected_agent_cli_and_forwards_musubi_env() {
        let root = PathBuf::from("/proj");
        let cli = PathBuf::from("/scripts/agent.exe");
        let mut env = std::collections::HashMap::new();
        env.insert("MUSUBI_ROOT".to_string(), "/musubi-core".to_string());
        env.insert("MUSUBI_DB".to_string(), "/data/audit.db".to_string());
        env.insert(
            "MUSUBI_LLM_CONFIG".to_string(),
            "/proj/.musubi/llm.json".to_string(),
        );
        env.insert(
            "MUSUBI_MCP_CONFIG".to_string(),
            "/proj/.musubi/mcp.json".to_string(),
        );
        env.insert("ANTHROPIC_API_KEY".to_string(), "sk-…".to_string());

        let spec =
            build_agent_launch_spec("task", "", "", Some(&cli), &root, &env, None, None).unwrap();

        assert_eq!(spec.program, cli);
        let mut forwarded = spec.env.clone();
        forwarded.sort();
        assert_eq!(
            forwarded,
            vec![
                ("MUSUBI_DB".to_string(), "/data/audit.db".to_string()),
                (
                    "MUSUBI_LLM_CONFIG".to_string(),
                    "/proj/.musubi/llm.json".to_string()
                ),
                (
                    "MUSUBI_MCP_CONFIG".to_string(),
                    "/proj/.musubi/mcp.json".to_string()
                ),
                ("MUSUBI_ROOT".to_string(), "/musubi-core".to_string()),
            ],
            "only MUSUBI_* is forwarded explicitly; the rest is inherited"
        );
    }

    #[test]
    fn launch_spec_rejects_empty_task() {
        let err = build_agent_launch_spec(
            "  \n ",
            "",
            "",
            None,
            Path::new("/proj"),
            &std::collections::HashMap::new(),
            None,
            None,
        )
        .unwrap_err();
        assert!(err.contains("empty"));
    }

    #[test]
    fn bounded_tail_keeps_newest_content_on_utf8_boundaries() {
        let mut buf = String::new();
        push_bounded_tail(&mut buf, "hello ", 64);
        push_bounded_tail(&mut buf, "world", 64);
        assert_eq!(buf, "hello world");

        let mut buf = String::from("0123456789");
        push_bounded_tail(&mut buf, "abcde", 8);
        assert_eq!(buf, "789abcde", "newest bytes win");

        // A multi-byte char straddling the cut is dropped whole, never split.
        let mut buf = String::new();
        push_bounded_tail(&mut buf, "aé", 2); // 'é' is 2 bytes
        assert_eq!(buf, "é");
        let mut buf = String::new();
        push_bounded_tail(&mut buf, "aaé", 2);
        assert_eq!(buf, "é");
        let mut buf = String::new();
        push_bounded_tail(&mut buf, "é日本", 4); // cut lands mid-'日'
        assert_eq!(buf, "本");
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

/// Resolve the state store paired with an audit ledger. The state database is
/// optional because fresh and pre-observability workspaces have no
/// `pipeline_runs` table to join yet.
pub fn resolve_state_db_path(audit_db: &ResolvedAuditDb) -> Option<ResolvedStateDb> {
    let path = audit_db.path.parent()?.join("musubi.db");
    path.is_file().then_some(ResolvedStateDb { path })
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

/// Deterministic launch recipe for one governed `agent "<task>"` child process.
/// Pure data so the spawn path is unit-testable without running an LLM-backed
/// process (the driver stays the only layer that reaches a model — HI #1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentLaunchSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: Vec<(String, String)>,
}

/// Build the launch spec for the on-demand task launcher.
///
/// - `program`: the detected `agent` CLI when setup found one, else `"agent"`
///   resolved via `PATH`.
/// - `args`: the task as the positional argument, `--profile` only when a
///   non-default profile is selected, and `--tool-surface agent` (the stable
///   launcher surface).
/// - `cwd`: the detected project root so the backend anchors its own discovery.
/// - `env`: explicit `MUSUBI_ROOT` / `MUSUBI_DB` forwards; the child inherits
///   the rest of the parent environment (provider credentials included).
pub fn build_agent_launch_spec(
    task: &str,
    profile: &str,
    default_profile: &str,
    agent_cli_path: Option<&Path>,
    project_root: &Path,
    env: &HashMap<String, String>,
    chat_id: Option<&str>,
    pipeline_name: Option<&str>,
) -> Result<AgentLaunchSpec, String> {
    let task = task.trim();
    if task.is_empty() {
        return Err("task is empty — type what the agent should do".into());
    }

    let program = agent_cli_path
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("agent"));

    let mut args = vec![task.to_string()];
    let profile = profile.trim();
    if !profile.is_empty() && profile != default_profile.trim() {
        args.push("--profile".into());
        args.push(profile.to_string());
    }
    if let Some(chat_id) = chat_id.map(str::trim).filter(|s| !s.is_empty()) {
        args.push("--chat-id".into());
        args.push(chat_id.to_string());
    }
    if let Some(pipeline_name) = pipeline_name.map(str::trim).filter(|s| !s.is_empty()) {
        if !valid_pipeline_name(pipeline_name) {
            return Err(format!("invalid pipeline name: {pipeline_name:?}"));
        }
        args.push("--pipeline".into());
        args.push(pipeline_name.to_string());
    }
    args.push("--tool-surface".into());
    args.push("agent".into());

    Ok(AgentLaunchSpec {
        program,
        args,
        cwd: project_root.to_path_buf(),
        env: forwarded_spec_env(env),
    })
}

/// The MUSUBI_* vars a spawned `agent` inherits explicitly (the rest of the
/// parent environment — provider credentials included — is inherited by the
/// process spawn itself).
fn forwarded_spec_env(env: &HashMap<String, String>) -> Vec<(String, String)> {
    let mut spec_env = Vec::new();
    for key in [
        "MUSUBI_ROOT",
        "MUSUBI_DB",
        "MUSUBI_LLM_CONFIG",
        "MUSUBI_MCP_CONFIG",
    ] {
        if let Some(val) = nonempty(env, key) {
            spec_env.push((key.to_string(), val));
        }
    }
    spec_env
}

/// Append `chunk` to `buf`, keeping only the newest `cap` bytes and never
/// splitting a UTF-8 character. Bounds the stdout/stderr tails the launcher
/// holds in memory.
pub fn push_bounded_tail(buf: &mut String, chunk: &str, cap: usize) {
    buf.push_str(chunk);
    if buf.len() <= cap {
        return;
    }
    let mut cut = buf.len() - cap;
    while cut < buf.len() && !buf.is_char_boundary(cut) {
        cut += 1;
    }
    buf.drain(..cut);
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
