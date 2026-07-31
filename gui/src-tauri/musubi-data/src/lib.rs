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

use std::collections::{HashMap, HashSet};
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct State {
    pub subagents: Vec<Agent>,
    pub agent_turns: Vec<AgentTurn>,
    pub agent_cycles: Vec<AgentCycle>,
    pub runtime_log_events: Vec<RuntimeLogEvent>,
    pub tool_evidence: Vec<ToolEvidence>,
    pub orchestrator_sessions: Vec<OrchestratorSession>,
    pub session_folder_grants: Vec<FolderGrant>,
    pub pipeline_runs: Vec<PipelineRun>,
    pub pipeline_catalog: Vec<PipelineCatalogEntry>,
    pub pipeline_builder_catalog: PipelineBuilderCatalog,
    pub orchestrator_chat_id: String,
    pub viewed_orchestrator_chat_id: String,
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
    /// Legacy compatibility field; session grants no longer block startup.
    pub workspace_blocked_reason: String,
    pub t: i64,
}

pub const MAX_EXTERNAL_FOLDER_GRANTS: i64 = 16;

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FolderGrant {
    pub chat_id: String,
    pub grant_id: String,
    pub alias: String,
    pub canonical_path: String,
    pub ordinal: i64,
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
    pub request_id: String,
    pub chat_id: String,
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
    /// Carries a `musubi-tier` tag, so it is one of the repository's own
    /// recipes rather than something the Studio minted. Deleting it is refused.
    pub protected: bool,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineBuilderCatalog {
    pub presets: Vec<PipelinePresetCatalogEntry>,
    pub agents: Vec<PipelineAgentCatalogEntry>,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelinePresetCatalogEntry {
    pub id: String,
    pub description: String,
    pub agent: String,
    pub stage: String,
    pub source_path: String,
    pub runnable: bool,
    pub blocked_reason: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineAgentCatalogEntry {
    pub name: String,
    pub display_label: String,
    pub step: String,
    pub agent: String,
    pub prompt_path: String,
    pub role_skill: String,
    pub allowed_tools: Vec<String>,
    pub max_turns: i64,
    pub max_output_tokens: Option<i64>,
    pub spawn_allowlist: Vec<String>,
    pub source_paths: Vec<String>,
    pub runnable: bool,
    pub blocked_reason: String,
}

/// Load every safe, valid pipeline registered below `.github/pipelines`.
/// Invalid or unresolved recipes fail closed and stay out of the runnable
/// catalog; legacy generator/evaluator recipes are projected into the same
/// flat sequential stage view.
pub fn read_studio_pipeline_catalog(project_root: &Path) -> Vec<PipelineCatalogEntry> {
    let root = project_root.join(".github").join("pipelines");
    let Ok(entries) = std::fs::read_dir(root) else {
        return vec![];
    };
    let mut names = entries
        .flatten()
        .filter(|entry| entry.path().is_dir())
        .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
        .filter(|name| name != "presets" && valid_pipeline_name(name))
        .collect::<Vec<_>>();
    names.sort();
    names
        .into_iter()
        .filter_map(|name| {
            let recipe = read_pipeline_recipe(project_root, &name).ok()?;
            let findings = validate_pipeline_recipe(project_root, &recipe);
            if findings.iter().any(|finding| finding.severity == "error") {
                return None;
            }
            let stages = recipe
                .stages
                .iter()
                .map(|stage| {
                    if !stage.preset.is_empty() {
                        stage.preset.clone()
                    } else if !stage.stage.is_empty() {
                        stage.stage.clone()
                    } else {
                        stage.agent.clone()
                    }
                })
                .collect();
            let protected = pipeline_is_protected(project_root, &name);
            Some(PipelineCatalogEntry {
                name: recipe.name,
                description: recipe.description,
                stages,
                runnable: true,
                blocked_reason: String::new(),
                protected,
            })
        })
        .collect()
}

pub fn valid_pipeline_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRecipe {
    pub name: String,
    pub description: String,
    pub version: String,
    pub baseline_checks: Vec<serde_yaml::Value>,
    pub correction: serde_yaml::Value,
    pub stages: Vec<PipelineStageRecipe>,
    pub resolved_contracts: Vec<ResolvedStageContract>,
    pub findings: Vec<PipelineFinding>,
}

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PipelineStageRecipe {
    pub preset: String,
    pub agent: String,
    pub stage: String,
    pub spawns: Vec<String>,
}

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ResolvedStageContract {
    pub step: String,
    pub agent: String,
    pub prompt_path: String,
    pub role_skill: String,
    pub allowed_tools: Vec<String>,
    pub max_turns: i64,
    pub max_output_tokens: Option<i64>,
    pub source_paths: Vec<String>,
}

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineFinding {
    pub severity: String,
    pub step: String,
    pub field: String,
    pub message: String,
}

#[derive(Serialize, Deserialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineSaveResult {
    pub saved: bool,
    pub catalog_refreshed: bool,
    pub path: String,
    pub findings: Vec<PipelineFinding>,
    pub error: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PipelineDocument {
    name: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    version: String,
    #[serde(default)]
    baseline_checks: Vec<serde_yaml::Value>,
    #[serde(default)]
    correction: serde_yaml::Value,
    #[serde(default)]
    stages: Vec<RawPipelineStage>,
    #[serde(default)]
    generator: Option<LegacyGenerator>,
    #[serde(default)]
    evaluator: Option<LegacyStage>,
    #[serde(default)]
    level: Option<serde_yaml::Value>,
    #[serde(default)]
    max_credits: Option<serde_yaml::Value>,
    #[serde(default)]
    warn_at: Option<serde_yaml::Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawPipelineStage {
    #[serde(default)]
    preset: String,
    #[serde(default)]
    agent: String,
    #[serde(default)]
    stage: String,
    #[serde(default)]
    spawns: serde_yaml::Value,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct LegacyGenerator {
    #[serde(default)]
    agents: Vec<LegacyStage>,
}

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct LegacyStage {
    #[serde(default)]
    name: String,
    #[serde(default)]
    agent: String,
    #[serde(default)]
    stage: String,
    #[serde(default)]
    skill: Option<String>,
    #[serde(default)]
    spawns: serde_yaml::Value,
}

#[derive(Serialize)]
struct PipelineOutputDocument<'a> {
    name: &'a str,
    description: &'a str,
    version: &'a str,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    baseline_checks: &'a Vec<serde_yaml::Value>,
    stages: &'a Vec<PipelineStageRecipe>,
    #[serde(skip_serializing_if = "yaml_value_is_null")]
    correction: &'a serde_yaml::Value,
}

#[derive(Clone)]
struct EffectiveStage {
    agent: String,
    stage: String,
    preset_path: Option<PathBuf>,
    agent_path: PathBuf,
}

fn yaml_value_is_null(value: &&serde_yaml::Value) -> bool {
    value.is_null()
}

fn finding(step: impl Into<String>, field: &str, message: impl Into<String>) -> PipelineFinding {
    PipelineFinding {
        severity: "error".into(),
        step: step.into(),
        field: field.into(),
        message: message.into(),
    }
}

fn spawns_from_value(
    value: serde_yaml::Value,
    step: &str,
    findings: &mut Vec<PipelineFinding>,
) -> Vec<String> {
    if value.is_null() {
        return vec![];
    }
    match value {
        serde_yaml::Value::Sequence(items) => {
            let mut roles = Vec::new();
            for item in items {
                if let Some(role) = item.as_str() {
                    roles.push(role.to_string());
                } else {
                    findings.push(finding(step, "spawns", "spawn roles must be strings"));
                }
            }
            roles
        }
        _ => {
            findings.push(finding(step, "spawns", "spawns must be a list of roles"));
            vec![]
        }
    }
}

fn role_from_agent_reference(reference: &str) -> String {
    Path::new(reference)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(reference)
        .strip_suffix(".agent.md")
        .unwrap_or(reference)
        .to_string()
}

pub fn read_pipeline_recipe(project_root: &Path, name: &str) -> Result<PipelineRecipe, String> {
    let path = checked_pipeline_path(project_root, name, false)?;
    let raw = std::fs::read_to_string(&path)
        .map_err(|error| format!("failed to read {}: {error}", path.display()))?;
    let document: PipelineDocument = serde_yaml::from_str(&raw)
        .map_err(|error| format!("invalid pipeline YAML in {}: {error}", path.display()))?;
    if document.name != name {
        return Err(format!(
            "pipeline directory {name:?} contains recipe named {:?}",
            document.name
        ));
    }
    let _legacy_ignored = (document.level, document.max_credits, document.warn_at);
    let mut findings = Vec::new();
    let mut role_skills = Vec::new();
    let stages = if !document.stages.is_empty() {
        document
            .stages
            .into_iter()
            .enumerate()
            .map(|(index, raw)| {
                let step = format!("stages[{index}]");
                role_skills.push(String::new());
                PipelineStageRecipe {
                    preset: raw.preset,
                    agent: raw.agent,
                    stage: raw.stage,
                    spawns: spawns_from_value(raw.spawns, &step, &mut findings),
                }
            })
            .collect()
    } else {
        let mut legacy = document.generator.unwrap_or_default().agents;
        if let Some(evaluator) = document.evaluator {
            legacy.push(evaluator);
        } else {
            findings.push(finding(
                "pipeline",
                "evaluator",
                "legacy recipe has no evaluator",
            ));
        }
        legacy
            .into_iter()
            .enumerate()
            .map(|(index, raw)| {
                let step = format!("stages[{index}]");
                let role = if raw.name.is_empty() {
                    role_from_agent_reference(&raw.agent)
                } else {
                    raw.name
                };
                let stage = if raw.stage.is_empty() {
                    role.clone()
                } else {
                    raw.stage
                };
                role_skills.push(raw.skill.unwrap_or_default());
                PipelineStageRecipe {
                    preset: String::new(),
                    agent: role,
                    stage,
                    spawns: spawns_from_value(raw.spawns, &step, &mut findings),
                }
            })
            .collect()
    };
    let mut recipe = PipelineRecipe {
        name: document.name,
        description: document.description,
        version: document.version,
        baseline_checks: document.baseline_checks,
        correction: document.correction,
        stages,
        resolved_contracts: vec![],
        findings,
    };
    if let Ok(effective) = resolve_recipe_stages(project_root, &recipe) {
        for (index, stage) in effective.iter().enumerate() {
            match resolve_stage_contract(stage, role_skills.get(index).cloned().unwrap_or_default())
            {
                Ok(contract) => recipe.resolved_contracts.push(contract),
                Err(error) => {
                    recipe
                        .findings
                        .push(finding(format!("stages[{index}]"), "contract", error))
                }
            }
        }
    }
    Ok(recipe)
}

fn render_pipeline_recipe(
    recipe: &PipelineRecipe,
    comments: &str,
    extras: &serde_yaml::Mapping,
) -> Result<String, String> {
    let mut document = serde_yaml::to_value(PipelineOutputDocument {
        name: &recipe.name,
        description: &recipe.description,
        version: &recipe.version,
        baseline_checks: &recipe.baseline_checks,
        stages: &recipe.stages,
        correction: &recipe.correction,
    })
    .map_err(|error| format!("failed to render pipeline YAML: {error}"))?;
    if let serde_yaml::Value::Mapping(mapping) = &mut document {
        for (key, value) in extras {
            mapping.insert(key.clone(), value.clone());
        }
    }
    let body = serde_yaml::to_string(&document)
        .map_err(|error| format!("failed to render pipeline YAML: {error}"))?;
    Ok(format!("{comments}{body}"))
}

fn safe_relative_reference(value: &str) -> bool {
    !value.is_empty()
        && !Path::new(value).is_absolute()
        && Path::new(value).components().all(|component| {
            matches!(
                component,
                std::path::Component::Normal(_) | std::path::Component::CurDir
            )
        })
}

fn collect_agent_matches(dir: &Path, filename: &str, matches: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_agent_matches(&path, filename, matches);
        } else if path.file_name().and_then(|name| name.to_str()) == Some(filename) {
            matches.push(path);
        }
    }
}

fn resolve_agent_path(project_root: &Path, reference: &str) -> Result<PathBuf, String> {
    if !safe_relative_reference(reference) {
        return Err(format!("unsafe agent reference {reference:?}"));
    }
    let agents_root = project_root.join(".github").join("agents");
    let mut matches = Vec::new();
    if reference.contains('/') || reference.contains('\\') || reference.ends_with(".agent.md") {
        let normalized = reference.replace('\\', "/");
        let relative = normalized.strip_prefix("agents/").unwrap_or(&normalized);
        let candidate = agents_root.join(relative);
        if candidate.is_file() {
            matches.push(candidate);
        }
    } else {
        collect_agent_matches(&agents_root, &format!("{reference}.agent.md"), &mut matches);
    }
    match matches.len() {
        1 => Ok(matches.remove(0)),
        0 => Err(format!("unresolved agent {reference:?}")),
        _ => Err(format!(
            "ambiguous agent {reference:?}: {} matches",
            matches.len()
        )),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PresetDocument {
    id: String,
    agent: String,
    stage: String,
    #[serde(default)]
    description: String,
}

fn resolve_recipe_stages(
    project_root: &Path,
    recipe: &PipelineRecipe,
) -> Result<Vec<EffectiveStage>, String> {
    let mut effective = Vec::new();
    for (index, stage) in recipe.stages.iter().enumerate() {
        let mut agent = stage.agent.clone();
        let mut stage_name = stage.stage.clone();
        let mut preset_path = None;
        if !stage.preset.is_empty() {
            if !valid_pipeline_name(&stage.preset) {
                return Err(format!(
                    "stages[{index}] has unsafe preset {:?}",
                    stage.preset
                ));
            }
            let path = project_root
                .join(".github/pipelines/presets")
                .join(format!("{}.yaml", stage.preset));
            let raw = std::fs::read_to_string(&path)
                .map_err(|error| format!("unresolved preset {:?}: {error}", stage.preset))?;
            let preset: PresetDocument = serde_yaml::from_str(&raw)
                .map_err(|error| format!("invalid preset {:?}: {error}", stage.preset))?;
            let _description = preset.description;
            if preset.id != stage.preset {
                return Err(format!(
                    "preset {:?} declares mismatched id {:?}",
                    stage.preset, preset.id
                ));
            }
            if agent.is_empty() {
                agent = preset.agent;
            }
            if stage_name.is_empty() {
                stage_name = preset.stage;
            }
            preset_path = Some(path);
        }
        if agent.is_empty() {
            return Err(format!("stages[{index}] has no agent or preset default"));
        }
        if stage_name.is_empty() || !valid_pipeline_name(&stage_name) {
            return Err(format!(
                "stages[{index}] has unsafe or empty stage {stage_name:?}"
            ));
        }
        let agent_path = resolve_agent_path(project_root, &agent)?;
        effective.push(EffectiveStage {
            agent,
            stage: stage_name,
            preset_path,
            agent_path,
        });
    }
    Ok(effective)
}

fn parse_agent_frontmatter(path: &Path) -> Result<serde_yaml::Mapping, String> {
    let raw = std::fs::read_to_string(path)
        .map_err(|error| format!("failed to read agent {}: {error}", path.display()))?;
    let text = raw.trim_start();
    let rest = text
        .strip_prefix("---")
        .ok_or_else(|| format!("agent {} has no YAML frontmatter", path.display()))?;
    let end = rest
        .find("\n---")
        .ok_or_else(|| format!("agent {} has unterminated frontmatter", path.display()))?;
    let frontmatter = serde_yaml::from_str::<serde_yaml::Mapping>(&rest[..end])
        .map_err(|error| format!("invalid agent frontmatter {}: {error}", path.display()))?;
    if rest[end + "\n---".len()..].trim().is_empty() {
        return Err(format!("agent {} has an empty prompt", path.display()));
    }
    Ok(frontmatter)
}

fn yaml_mapping_get<'a>(map: &'a serde_yaml::Mapping, key: &str) -> Option<&'a serde_yaml::Value> {
    map.get(serde_yaml::Value::String(key.into()))
}

fn resolve_stage_contract(
    stage: &EffectiveStage,
    role_skill: String,
) -> Result<ResolvedStageContract, String> {
    let frontmatter = parse_agent_frontmatter(&stage.agent_path)?;
    let tool_items = yaml_mapping_get(&frontmatter, "tools")
        .and_then(serde_yaml::Value::as_sequence)
        .ok_or_else(|| format!("agent {:?} has missing or invalid tools", stage.agent))?;
    let allowed_tools = tool_items
        .iter()
        .map(|item| {
            item.as_str()
                .map(str::to_string)
                .ok_or_else(|| format!("agent {:?} has a non-string tool", stage.agent))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let max_turns = yaml_mapping_get(&frontmatter, "maxTurns")
        .and_then(serde_yaml::Value::as_i64)
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("agent {:?} has invalid maxTurns", stage.agent))?;
    let max_output_tokens = match yaml_mapping_get(&frontmatter, "maxOutputTokens") {
        Some(value) => Some(
            value
                .as_i64()
                .filter(|value| *value > 0)
                .ok_or_else(|| format!("agent {:?} has invalid maxOutputTokens", stage.agent))?,
        ),
        None => None,
    };
    let mut source_paths = Vec::new();
    if let Some(path) = &stage.preset_path {
        source_paths.push(path.to_string_lossy().to_string());
    }
    source_paths.push(stage.agent_path.to_string_lossy().to_string());
    Ok(ResolvedStageContract {
        step: stage.stage.clone(),
        agent: stage.agent.clone(),
        prompt_path: stage.agent_path.to_string_lossy().to_string(),
        role_skill,
        allowed_tools,
        max_turns,
        max_output_tokens,
        source_paths,
    })
}

fn extract_quoted_strings(line: &str) -> Vec<String> {
    let mut values = Vec::new();
    let mut chars = line.char_indices();
    while let Some((start, ch)) = chars.next() {
        if ch != '"' {
            continue;
        }
        if let Some((end, _)) = chars.find(|(_, candidate)| *candidate == '"') {
            values.push(line[start + 1..end].to_string());
        }
    }
    values
}

fn read_spawn_firewall(project_root: &Path) -> HashMap<String, Vec<String>> {
    let Ok(raw) = std::fs::read_to_string(project_root.join("scripts/policy_engine.py")) else {
        return HashMap::new();
    };
    let Some(start) = raw.find("MAIN_SUBAGENT_ALLOWLIST:") else {
        return HashMap::new();
    };
    let mut result = HashMap::new();
    let mut current: Option<String> = None;
    for line in raw[start..].lines().skip(1) {
        let trimmed = line.trim();
        if trimmed == "}" {
            break;
        }
        let quoted = extract_quoted_strings(trimmed);
        if trimmed.contains(':') {
            if let Some(key) = quoted.first() {
                current = Some(key.clone());
                result.entry(key.clone()).or_insert_with(Vec::new);
                for role in quoted.iter().skip(1) {
                    result.entry(key.clone()).or_default().push(role.clone());
                }
            }
        } else if let Some(key) = &current {
            result.entry(key.clone()).or_default().extend(quoted);
        }
        if trimmed.contains(']') {
            current = None;
        }
    }
    result
}

fn effective_spawn_firewall(
    stage: &EffectiveStage,
    fallback: &HashMap<String, Vec<String>>,
) -> Result<Vec<String>, String> {
    let frontmatter = parse_agent_frontmatter(&stage.agent_path)?;
    let Some(value) = yaml_mapping_get(&frontmatter, "spawn_allowlist") else {
        return Ok(fallback.get(&stage.agent).cloned().unwrap_or_default());
    };
    let items = value.as_sequence().ok_or_else(|| {
        format!(
            "agent {:?} has invalid spawn_allowlist; expected a list",
            stage.agent
        )
    })?;
    items
        .iter()
        .map(|item| {
            item.as_str().map(str::to_string).ok_or_else(|| {
                format!(
                    "agent {:?} has a non-string spawn_allowlist role",
                    stage.agent
                )
            })
        })
        .collect()
}

fn collect_agent_catalog_paths(dir: &Path, paths: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_agent_catalog_paths(&path, paths);
        } else if path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".agent.md"))
        {
            paths.push(path);
        }
    }
}

/// Project the authoritative preset and agent catalogs for Pipeline Studio.
/// Resolution delegates to the same parser, contract, and policy functions
/// used by recipe validation; this projection never makes an entry runnable
/// on weaker evidence than the backend save boundary.
pub fn read_pipeline_builder_catalog(project_root: &Path) -> PipelineBuilderCatalog {
    let firewall = read_spawn_firewall(project_root);
    let presets_root = project_root
        .join(".github")
        .join("pipelines")
        .join("presets");
    let mut preset_paths = std::fs::read_dir(&presets_root)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.is_file() && path.extension().and_then(|ext| ext.to_str()) == Some("yaml")
        })
        .collect::<Vec<_>>();
    preset_paths.sort();
    let presets = preset_paths
        .into_iter()
        .filter_map(|path| {
            let id = path.file_stem()?.to_str()?.to_string();
            if !valid_pipeline_name(&id) {
                return None;
            }
            let mut entry = PipelinePresetCatalogEntry {
                id: id.clone(),
                source_path: path.to_string_lossy().to_string(),
                ..PipelinePresetCatalogEntry::default()
            };
            let parsed = std::fs::read_to_string(&path)
                .map_err(|error| format!("failed to read preset {id:?}: {error}"))
                .and_then(|raw| {
                    serde_yaml::from_str::<PresetDocument>(&raw)
                        .map_err(|error| format!("invalid preset {id:?}: {error}"))
                });
            if let Ok(preset) = parsed {
                entry.agent = preset.agent;
                entry.stage = preset.stage;
                entry.description = preset.description;
            }
            let recipe = PipelineRecipe {
                stages: vec![PipelineStageRecipe {
                    preset: id,
                    ..PipelineStageRecipe::default()
                }],
                ..PipelineRecipe::default()
            };
            let resolved = resolve_recipe_stages(project_root, &recipe).and_then(|mut stages| {
                let stage = stages.remove(0);
                resolve_stage_contract(&stage, String::new())?;
                effective_spawn_firewall(&stage, &firewall)?;
                Ok(())
            });
            match resolved {
                Ok(()) => entry.runnable = true,
                Err(error) => entry.blocked_reason = error,
            }
            Some(entry)
        })
        .collect();

    let agents_root = project_root.join(".github").join("agents");
    let mut agent_paths = Vec::new();
    collect_agent_catalog_paths(&agents_root, &mut agent_paths);
    agent_paths.sort();
    let mut emitted = HashSet::new();
    let agents = agent_paths
        .into_iter()
        .filter_map(|path| {
            let filename = path.file_name()?.to_str()?;
            let name = filename.strip_suffix(".agent.md")?.to_string();
            if !valid_pipeline_name(&name) || !emitted.insert(name.clone()) {
                return None;
            }
            let mut entry = PipelineAgentCatalogEntry {
                name: name.clone(),
                step: name.clone(),
                agent: name.clone(),
                prompt_path: path.to_string_lossy().to_string(),
                source_paths: vec![path.to_string_lossy().to_string()],
                ..PipelineAgentCatalogEntry::default()
            };
            let resolved = (|| {
                let resolved_path = resolve_agent_path(project_root, &name)?;
                let frontmatter = parse_agent_frontmatter(&resolved_path)?;
                entry.display_label = yaml_mapping_get(&frontmatter, "name")
                    .and_then(serde_yaml::Value::as_str)
                    .filter(|label| !label.trim().is_empty())
                    .ok_or_else(|| format!("agent {name:?} has missing or invalid name"))?
                    .to_string();
                let stage = EffectiveStage {
                    agent: name.clone(),
                    stage: name.clone(),
                    preset_path: None,
                    agent_path: resolved_path,
                };
                let contract = resolve_stage_contract(&stage, String::new())?;
                entry.step = contract.step;
                entry.agent = contract.agent;
                entry.prompt_path = contract.prompt_path;
                entry.role_skill = contract.role_skill;
                entry.allowed_tools = contract.allowed_tools;
                entry.max_turns = contract.max_turns;
                entry.max_output_tokens = contract.max_output_tokens;
                entry.source_paths = contract.source_paths;
                entry.spawn_allowlist = effective_spawn_firewall(&stage, &firewall)?;
                Ok::<(), String>(())
            })();
            match resolved {
                Ok(()) => entry.runnable = true,
                Err(error) => entry.blocked_reason = error,
            }
            Some(entry)
        })
        .collect();

    PipelineBuilderCatalog { presets, agents }
}

fn evaluator_like(stage: &EffectiveStage, recipe_stage: &PipelineStageRecipe) -> bool {
    matches!(stage.agent.as_str(), "reviewer" | "synthesizer")
        || matches!(stage.stage.as_str(), "review" | "check" | "synthesis")
        || recipe_stage.preset == "check"
}

pub fn validate_pipeline_recipe(
    project_root: &Path,
    recipe: &PipelineRecipe,
) -> Vec<PipelineFinding> {
    let mut findings = recipe.findings.clone();
    if !valid_pipeline_name(&recipe.name) {
        findings.push(finding(
            "pipeline",
            "name",
            "name must be lowercase kebab-case",
        ));
    }
    if recipe.stages.len() < 2 {
        findings.push(finding(
            "pipeline",
            "stages",
            "pipeline requires at least two stages",
        ));
    }
    let firewall = read_spawn_firewall(project_root);
    let mut seen_agents = HashSet::new();
    let mut seen_stages = HashSet::new();
    let mut effective = Vec::new();
    for (index, recipe_stage) in recipe.stages.iter().enumerate() {
        let step = format!("stages[{index}]");
        let one = PipelineRecipe {
            stages: vec![recipe_stage.clone()],
            ..PipelineRecipe::default()
        };
        match resolve_recipe_stages(project_root, &one) {
            Ok(mut stages) => {
                let stage = stages.remove(0);
                if let Err(error) = resolve_stage_contract(&stage, String::new()) {
                    let contract_finding = finding(&step, "contract", error);
                    if !findings.contains(&contract_finding) {
                        findings.push(contract_finding);
                    }
                }
                if !seen_agents.insert(stage.agent.clone()) {
                    findings.push(finding(
                        &step,
                        "agent",
                        "duplicate resolved agent is ambiguous",
                    ));
                }
                if !seen_stages.insert(stage.stage.clone()) {
                    findings.push(finding(
                        &step,
                        "stage",
                        "duplicate resolved stage is ambiguous",
                    ));
                }
                let allowed_spawns = match effective_spawn_firewall(&stage, &firewall) {
                    Ok(allowed) => allowed,
                    Err(error) => {
                        findings.push(finding(&step, "spawns", error));
                        Vec::new()
                    }
                };
                for role in &recipe_stage.spawns {
                    if !valid_pipeline_name(role) || resolve_agent_path(project_root, role).is_err()
                    {
                        findings.push(finding(
                            &step,
                            "spawns",
                            format!("unknown spawn role {role:?}"),
                        ));
                    } else if !allowed_spawns.contains(role) {
                        findings.push(finding(
                            &step,
                            "spawns",
                            format!(
                                "spawn role {role:?} is outside the {:?} firewall",
                                stage.agent
                            ),
                        ));
                    }
                }
                effective.push((index, stage));
            }
            Err(error) => {
                let field = if !recipe_stage.preset.is_empty()
                    && (error.contains("preset") || error.contains("Preset"))
                {
                    "preset"
                } else if error.contains("stage") {
                    "stage"
                } else {
                    "agent"
                };
                findings.push(finding(&step, field, error));
            }
        }
    }
    for (position, (index, stage)) in effective.iter().enumerate() {
        let is_last = *index + 1 == recipe.stages.len();
        let is_evaluator = evaluator_like(stage, &recipe.stages[*index]);
        if (is_last && !is_evaluator) || (!is_last && is_evaluator) {
            findings.push(finding(
                format!("stages[{index}]"),
                "evaluator",
                if is_last {
                    "final stage must resolve to an evaluator"
                } else {
                    "evaluator may appear only as the final stage"
                },
            ));
        }
        let _ = position;
    }
    findings
}

fn checked_pipeline_path(
    project_root: &Path,
    name: &str,
    for_write: bool,
) -> Result<PathBuf, String> {
    if !valid_pipeline_name(name) {
        return Err(format!("unsafe pipeline name {name:?}"));
    }
    let canonical_project = project_root
        .canonicalize()
        .map_err(|error| format!("invalid project root {}: {error}", project_root.display()))?;
    let github = canonical_project.join(".github");
    if github.exists() {
        let canonical_github = github
            .canonicalize()
            .map_err(|error| format!("unsafe .github root: {error}"))?;
        ensure_canonical_child(&canonical_project, &canonical_github, ".github root")?;
    }
    let pipelines = github.join("pipelines");
    if !for_write && !pipelines.is_dir() {
        return Err(format!(
            "pipeline root does not exist: {}",
            pipelines.display()
        ));
    }
    let canonical_root = if pipelines.exists() {
        let canonical = pipelines
            .canonicalize()
            .map_err(|error| format!("unsafe pipeline root: {error}"))?;
        ensure_canonical_child(&canonical_project, &canonical, "pipeline root")?;
        Some(canonical)
    } else {
        None
    };
    let directory = pipelines.join(name);
    if directory.exists() {
        let canonical = directory
            .canonicalize()
            .map_err(|error| format!("unsafe pipeline directory: {error}"))?;
        let canonical_root = canonical_root
            .as_ref()
            .ok_or_else(|| "pipeline root disappeared during safety check".to_string())?;
        ensure_exact_canonical_owner(&canonical_root.join(name), &canonical, "pipeline directory")?;
    }
    let target = directory.join("pipeline.yaml");
    if target.exists() {
        let canonical_target = target
            .canonicalize()
            .map_err(|error| format!("unsafe pipeline target: {error}"))?;
        let canonical_root = canonical_root
            .as_ref()
            .ok_or_else(|| "pipeline root disappeared during safety check".to_string())?;
        ensure_exact_canonical_owner(
            &canonical_root.join(name).join("pipeline.yaml"),
            &canonical_target,
            "pipeline target",
        )?;
    }
    Ok(target)
}

fn ensure_canonical_child(project: &Path, child: &Path, label: &str) -> Result<(), String> {
    if child.starts_with(project) {
        Ok(())
    } else {
        Err(format!(
            "{label} {} resolves outside project {}",
            child.display(),
            project.display()
        ))
    }
}

fn ensure_exact_canonical_owner(expected: &Path, actual: &Path, label: &str) -> Result<(), String> {
    if actual == expected {
        Ok(())
    } else {
        Err(format!(
            "{label} {} is an alias for {}, not its registered name",
            expected.display(),
            actual.display()
        ))
    }
}

type PipelineReplacer = dyn Fn(&Path, &Path) -> std::io::Result<()>;

static PIPELINE_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

fn pipeline_temp_path(directory: &Path) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let sequence = PIPELINE_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    directory.join(format!(
        ".pipeline.yaml.{}.{}.{}.tmp",
        std::process::id(),
        nanos,
        sequence
    ))
}

fn atomic_pipeline_writer(
    temp: &Path,
    target: &Path,
    bytes: &[u8],
    replacer: &PipelineReplacer,
) -> std::io::Result<()> {
    let mut file = OpenOptions::new().create_new(true).write(true).open(temp)?;
    let result = (|| {
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        replacer(temp, target)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(temp);
    }
    result
}

#[cfg(not(windows))]
fn atomic_replace(temp: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::rename(temp, target)
}

#[cfg(windows)]
fn atomic_replace(temp: &Path, target: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    if !target.exists() {
        return std::fs::rename(temp, target);
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn ReplaceFileW(
            replaced: *const u16,
            replacement: *const u16,
            backup: *const u16,
            flags: u32,
            exclude: *mut std::ffi::c_void,
            reserved: *mut std::ffi::c_void,
        ) -> i32;
    }
    let replaced = target
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let replacement = temp
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let result = unsafe {
        ReplaceFileW(
            replaced.as_ptr(),
            replacement.as_ptr(),
            std::ptr::null(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

/// Keys the Studio's own model owns and therefore re-renders from the draft.
///
/// `generator`/`evaluator` are on the list even though the model has no field
/// for them: they are the legacy stage shape, `read_pipeline_recipe` already
/// folds them into `stages`, and preserving them alongside the `stages:` the
/// Studio writes would leave two contradictory stage lists in one file.
const STUDIO_OWNED_PIPELINE_KEYS: [&str; 8] = [
    "name",
    "description",
    "version",
    "baseline_checks",
    "stages",
    "correction",
    "generator",
    "evaluator",
];

/// A recipe carrying a `musubi-tier` tag is one of the repository's own,
/// checked in and governed by Hard Invariant #9. `render_pipeline_recipe`
/// emits no comments, so a Studio-minted recipe can never carry the tag —
/// which makes the tag an exact marker for "shipped" needing no git
/// dependency, no schema change, and no hard-coded name list.
pub fn pipeline_is_protected(project_root: &Path, name: &str) -> bool {
    let Ok(target) = checked_pipeline_path(project_root, name, false) else {
        return false;
    };
    let Ok(raw) = std::fs::read_to_string(&target) else {
        return false;
    };
    let tagged = leading_comment_lines(&raw).any(|line| {
        line.trim_start()
            .trim_start_matches('#')
            .trim_start()
            .starts_with("musubi-tier:")
    });
    tagged
}

fn leading_comment_lines(raw: &str) -> impl Iterator<Item = &str> {
    raw.lines().take_while(|line| {
        let trimmed = line.trim_start();
        trimmed.starts_with('#') || trimmed.is_empty()
    })
}

/// What a hand-authored recipe carries that the Studio's six modelled fields do
/// not: the `musubi-tier` header block, and top-level keys like `max_credits`
/// (the credit budget), `warn_at`, and `level`. Rendering from the model alone
/// would delete all of it, so overwriting a recipe of the same name carries it
/// across. A save under a new name starts clean.
fn preserved_pipeline_prelude(target: &Path) -> (String, serde_yaml::Mapping) {
    let mut extras = serde_yaml::Mapping::new();
    let Ok(raw) = std::fs::read_to_string(target) else {
        return (String::new(), extras);
    };
    let comments = leading_comment_lines(&raw).collect::<Vec<_>>().join("\n");
    let comments = if comments.contains('#') {
        format!("{}\n", comments.trim_end())
    } else {
        String::new()
    };
    if let Ok(serde_yaml::Value::Mapping(existing)) =
        serde_yaml::from_str::<serde_yaml::Value>(&raw)
    {
        for (key, value) in existing {
            let owned = key
                .as_str()
                .is_some_and(|name| STUDIO_OWNED_PIPELINE_KEYS.contains(&name));
            if !owned {
                extras.insert(key, value);
            }
        }
    }
    (comments, extras)
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PipelineDeleteResult {
    pub deleted: bool,
    pub catalog_refreshed: bool,
    pub path: String,
    pub error: String,
}

/// Removes a Studio-minted recipe directory. Fail-closed in the same shape as
/// the rest of the pipeline surface: an unsafe name, a missing recipe, or a
/// `musubi-tier`-tagged recipe returns an error rather than touching the disk.
pub fn delete_pipeline_recipe(project_root: &Path, name: &str) -> PipelineDeleteResult {
    let mut result = PipelineDeleteResult::default();
    let target = match checked_pipeline_path(project_root, name, false) {
        Ok(target) => target,
        Err(error) => {
            result.error = error;
            return result;
        }
    };
    if !target.exists() {
        result.error = format!("pipeline {name:?} does not exist");
        return result;
    }
    if pipeline_is_protected(project_root, name) {
        result.error = format!("pipeline {name:?} is repository-owned and cannot be deleted");
        return result;
    }
    let Some(directory) = target.parent() else {
        result.error = "pipeline target has no parent".into();
        return result;
    };
    if let Err(error) = std::fs::remove_dir_all(directory) {
        result.error = format!("failed to remove pipeline directory: {error}");
        return result;
    }
    result.deleted = true;
    result.path = directory.to_string_lossy().to_string();
    result.catalog_refreshed = !read_studio_pipeline_catalog(project_root)
        .iter()
        .any(|entry| entry.name == name);
    if !result.catalog_refreshed {
        result.error = "deleted_but_refresh_failed".into();
    }
    result
}

pub fn save_pipeline_recipe(project_root: &Path, recipe: &PipelineRecipe) -> PipelineSaveResult {
    save_pipeline_recipe_with_replacer(project_root, recipe, &atomic_replace)
}

fn save_pipeline_recipe_with_replacer(
    project_root: &Path,
    recipe: &PipelineRecipe,
    replacer: &PipelineReplacer,
) -> PipelineSaveResult {
    let findings = validate_pipeline_recipe(project_root, recipe);
    let mut result = PipelineSaveResult {
        findings,
        ..PipelineSaveResult::default()
    };
    if result
        .findings
        .iter()
        .any(|finding| finding.severity == "error")
    {
        result.error = "pipeline recipe validation failed".into();
        return result;
    }
    let target = match checked_pipeline_path(project_root, &recipe.name, true) {
        Ok(target) => target,
        Err(error) => {
            result.error = error;
            return result;
        }
    };
    // Overwriting an existing recipe keeps everything the Studio does not
    // model. Without this, updating a checked-in preset would silently drop its
    // credit budget and its musubi-tier tag.
    let (comments, extras) = preserved_pipeline_prelude(&target);
    let rendered = match render_pipeline_recipe(recipe, &comments, &extras) {
        Ok(rendered) => rendered,
        Err(error) => {
            result.error = error;
            return result;
        }
    };
    let Some(directory) = target.parent() else {
        result.error = "pipeline target has no parent".into();
        return result;
    };
    if let Err(error) = std::fs::create_dir_all(directory) {
        result.error = format!("failed to create pipeline directory: {error}");
        return result;
    }
    if checked_pipeline_path(project_root, &recipe.name, true).is_err() {
        result.error = "pipeline directory failed canonical safety check".into();
        return result;
    }
    let temp = pipeline_temp_path(directory);
    if let Err(error) = atomic_pipeline_writer(&temp, &target, rendered.as_bytes(), replacer) {
        result.error = format!("atomic pipeline write failed: {error}");
        return result;
    }
    result.saved = true;
    result.path = target.to_string_lossy().to_string();
    result.catalog_refreshed = read_studio_pipeline_catalog(project_root)
        .iter()
        .any(|entry| entry.name == recipe.name);
    if !result.catalog_refreshed {
        result.error = "saved_but_refresh_failed".into();
    }
    result
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
    // Root-selected skill pushed into this worker's prompt at spawn (option 3),
    // serialized as `pushedSkill`. A pushed skill has no `musubi_get_skill`
    // tool-call, so this spawn-row field is the only evidence the worker
    // received procedural knowledge. Empty when none was pushed.
    pub pushed_skill: String,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AgentTurn {
    pub id: i64,
    pub request_id: String,
    pub chat_id: String,
    pub parent_session: String,
    // Root task text from the audit `sessions` row. This lets the console show
    // what the root worker did without parsing process logs.
    pub request: String,
    // Turn start time (epoch seconds), serialized as `startedAt`. Lets the
    // Orchestrator order driver-only turns against worker sessions by real time.
    pub started_at: f64,
    pub model_family: String,
    pub cycles: i64,
    pub tokens_in_estimate: i64,
    pub tokens_out_estimate: i64,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeLogEvent {
    pub id: i64,
    pub request_id: String,
    pub chat_id: String,
    pub seq: i64,
    pub ts: String,
    pub source: String,
    pub stream: String,
    pub agent_handle: String,
    pub role: String,
    pub category: String,
    pub message: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AgentCycle {
    pub session_id: String,
    pub stage: String,
    pub worker_id: String,
    pub cycle_idx: i64,
    pub lm_ms: i64,
    pub tokens_in: i64,
    pub cached_input_tokens: i64,
    pub tokens_out: i64,
    pub token_source: String,
    pub tool_names: Vec<String>,
    pub cycle_status: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ToolEvidence {
    pub id: i64,
    pub ts: String,
    pub session_id: String,
    pub chat_id: String,
    pub role: String,
    pub worker_id: String,
    pub tool: String,
    pub category: String,
    pub status: String,
    pub skill_id: String,
    pub detail: String,
}

#[derive(Serialize, Default, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct OrchestratorSession {
    pub chat_id: String,
    pub created_at: String,
    pub updated_at: String,
    pub latest_activity: f64,
    pub viewed_through: f64,
    pub unread: bool,
    pub title: String,
    pub last_request: String,
    pub root_turns: i64,
    pub workers: i64,
}

#[derive(Serialize, Default, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PipelineRun {
    pub session_id: String,
    pub chat_id: String,
    pub pipeline_name: String,
    pub request_id: String,
    pub profile: String,
    pub task: String,
    pub brief: String,
    pub started_at: f64,
    pub ended_at: Option<f64>,
    pub status: String,
    pub paused_at_stage: Option<String>,
    pub paused_at_chunk: Option<String>,
    pub pause_reason: Option<String>,
    pub pending_action: Option<String>,
    pub stages: Vec<Agent>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PipelineResumeDecision {
    pub session_id: String,
    pub chat_id: String,
    pub pipeline_name: String,
    pub request_id: String,
    pub profile: String,
    pub task: String,
    pub action: String,
    pub user_hint: Option<String>,
    pub extra_budget: i64,
    pub launch: bool,
}

pub fn resume_pipeline_session(
    conn: &mut Connection,
    session_id: &str,
    action: &str,
    user_hint: Option<&str>,
    extra_budget: i64,
    now: f64,
) -> Result<PipelineResumeDecision, String> {
    let tx = conn
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| format!("Cannot lock pipeline state for resume: {e}"))?;
    let checkpoint = tx
        .query_row(
            "SELECT s.pause_reason, s.paused_at_stage,
                    COALESCE(pr.chat_id,''), pr.pipeline_name,
                    COALESCE(pr.request_id,''), COALESCE(pr.profile,''),
                    COALESCE(pr.task,'')
             FROM sessions s
             JOIN pipeline_runs pr ON pr.session_id=s.session_id
             WHERE s.session_id=?1",
            [session_id],
            |row| {
                Ok((
                    row.get::<_, Option<String>>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                ))
            },
        )
        .optional()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "Paused pipeline session was not found.".to_string())?;
    let (reason, paused_stage, chat_id, pipeline_name, request_id, profile, task) = checkpoint;
    let reason = reason.ok_or_else(|| "Pipeline session is no longer paused.".to_string())?;
    if paused_stage.as_deref().unwrap_or_default().is_empty() {
        return Err("Pipeline session has no resumable stage checkpoint.".into());
    }
    let valid = match reason.as_str() {
        "stage_review" => matches!(action, "approve" | "retry" | "abort" | "auto_approve_rest"),
        "budget_exhausted" => matches!(action, "grant" | "force" | "abort"),
        _ => return Err(format!("Unknown pipeline pause reason {reason:?}.")),
    };
    if !valid {
        return Err(format!(
            "Action {action:?} does not apply to pause reason {reason:?}."
        ));
    }
    if action == "grant" && extra_budget <= 0 {
        return Err("Grant requires a positive extra budget.".into());
    }
    if action != "grant" && extra_budget != 0 {
        return Err(format!(
            "Action {action:?} does not accept an extra budget."
        ));
    }
    for (label, value) in [
        ("chat ID", chat_id.as_str()),
        ("pipeline name", pipeline_name.as_str()),
        ("request ID", request_id.as_str()),
        ("profile", profile.as_str()),
        ("task", task.as_str()),
    ] {
        if value.trim().is_empty() {
            return Err(format!("Pipeline resume checkpoint is missing {label}."));
        }
    }
    let hint = user_hint
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let launch = action != "abort";
    if launch {
        let auto_approve = i64::from(action == "auto_approve_rest");
        tx.execute(
            "UPDATE sessions SET
                paused_at_stage=NULL,
                paused_at_chunk=NULL,
                pause_reason=NULL,
                pending_action=?1,
                pending_user_hint=?2,
                pending_extra_budget=?3,
                auto_approve_remaining=MAX(auto_approve_remaining,?4),
                updated_at=?5
             WHERE session_id=?6 AND pause_reason=?7",
            rusqlite::params![
                action,
                hint,
                extra_budget,
                auto_approve,
                now.to_string(),
                session_id,
                reason
            ],
        )
        .map_err(|e| e.to_string())?;
    } else {
        tx.execute(
            "UPDATE sessions SET
                paused_at_stage=NULL,
                paused_at_chunk=NULL,
                pause_reason=NULL,
                pending_action=NULL,
                pending_user_hint=NULL,
                pending_extra_budget=0,
                status='escalated',
                updated_at=?1
             WHERE session_id=?2 AND pause_reason=?3",
            rusqlite::params![now.to_string(), session_id, reason],
        )
        .map_err(|e| e.to_string())?;
        tx.execute(
            "UPDATE pipeline_runs SET ended_at=?1, final_status='aborted'
             WHERE session_id=?2 AND ended_at IS NULL",
            rusqlite::params![now, session_id],
        )
        .map_err(|e| e.to_string())?;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(PipelineResumeDecision {
        session_id: session_id.to_string(),
        chat_id,
        pipeline_name,
        request_id,
        profile,
        task,
        action: action.to_string(),
        user_hint: hint,
        extra_budget,
        launch,
    })
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

fn parse_cycle_tools(raw: &str) -> Vec<String> {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) else {
        return vec![];
    };
    let Some(items) = value.as_array() else {
        return vec![];
    };
    items
        .iter()
        .filter_map(|item| {
            item.as_str().map(str::to_string).or_else(|| {
                item.as_object()
                    .and_then(|obj| obj.get("name"))
                    .and_then(|name| name.as_str())
                    .map(str::to_string)
            })
        })
        .collect()
}

/// The depth-0 driver's id in the runtime ledger and in `agent_cycles`.
const ROOT_WORKER_ID: &str = "root";

/// True for every spelling the depth-0 driver has ever been recorded under.
///
/// The substrate now writes `root` (see `scripts/policy_engine.py::ROOT_ROLE`),
/// but `policy_audit` and `tool_audit` are append-only, so rows written before
/// the rename still say `agent`, and the console has always printed `driver`.
/// All three must join to the same runtime node or a panel reads empty for
/// history it can see perfectly well.
fn is_root_actor(role: &str) -> bool {
    matches!(role, "root" | "agent" | "driver")
}

fn safe_tool_provenance(tool: &str, args_json: &str) -> (String, String, String) {
    if tool != "musubi_get_skill" {
        return ("tools".into(), String::new(), String::new());
    }
    let skill_id = serde_json::from_str::<serde_json::Value>(args_json)
        .ok()
        .and_then(|value| {
            value
                .get("skill_id")
                .and_then(|item| item.as_str())
                .map(str::to_string)
        })
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 80
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        })
        .unwrap_or_default();
    let detail = if skill_id.is_empty() {
        String::new()
    } else {
        format!("skill {skill_id}")
    };
    ("skills".into(), skill_id, detail)
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

/// Load the visible chat feed for one GUI session. Older databases without a
/// `chat_id` column fall back to their pre-session surface scope.
pub fn load_chat_for_session(
    conn: &Connection,
    surface: &str,
    chat_id: &str,
) -> rusqlite::Result<Vec<ChatMsg>> {
    if !table_exists(conn, "chat_log")? {
        return Ok(vec![]);
    }
    let has_surface = column_exists(conn, "chat_log", "surface")?;
    let has_chat_id = column_exists(conn, "chat_log", "chat_id")?;
    let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = if has_surface && has_chat_id {
        (
            "SELECT role,ts,text,tone FROM chat_log \
             WHERE surface=?1 AND chat_id=?2 ORDER BY id ASC",
            vec![&surface, &chat_id],
        )
    } else if has_surface {
        (
            "SELECT role,ts,text,tone FROM chat_log WHERE surface=?1 ORDER BY id ASC",
            vec![&surface],
        )
    } else {
        (
            "SELECT role,ts,text,tone FROM chat_log ORDER BY id ASC",
            vec![],
        )
    };
    let mut stmt = conn.prepare(sql)?;
    let mut messages = stmt
        .query_map(params.as_slice(), |r| {
            Ok(ChatMsg {
                role: r.get(0)?,
                ts: r.get(1)?,
                text: r.get(2)?,
                tone: r.get(3)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    trim_front(&mut messages, 60);
    Ok(messages)
}

fn load_orchestrator_sessions(conn: &Connection) -> rusqlite::Result<Vec<OrchestratorSession>> {
    if !table_exists(conn, "chat_log")?
        || !column_exists(conn, "chat_log", "surface")?
        || !column_exists(conn, "chat_log", "chat_id")?
    {
        return Ok(vec![]);
    }
    let mut stmt = conn.prepare(
        "SELECT c.chat_id,
                COALESCE((SELECT x.ts FROM chat_log x
                          WHERE x.surface='orchestrator' AND x.chat_id=c.chat_id
                          ORDER BY x.id ASC LIMIT 1), ''),
                COALESCE((SELECT x.ts FROM chat_log x
                          WHERE x.surface='orchestrator' AND x.chat_id=c.chat_id
                          ORDER BY x.id DESC LIMIT 1), ''),
                COALESCE((SELECT x.text FROM chat_log x
                          WHERE x.surface='orchestrator' AND x.chat_id=c.chat_id
                            AND x.role='you'
                          ORDER BY x.id ASC LIMIT 1), ''),
                COALESCE((SELECT x.text FROM chat_log x
                          WHERE x.surface='orchestrator' AND x.chat_id=c.chat_id
                            AND x.role='you'
                          ORDER BY x.id DESC LIMIT 1), ''),
                MAX(
                  COALESCE((SELECT MAX(CAST(REPLACE(COALESCE(x.ts,''),'epoch:','') AS REAL))
                            FROM chat_log x
                            WHERE x.surface='orchestrator' AND x.chat_id=c.chat_id), 0),
                  COALESCE((SELECT MAX(CAST(REPLACE(COALESCE(x.ts,''),'epoch:','') AS REAL))
                            FROM runtime_log_events x WHERE x.chat_id=c.chat_id), 0),
                  COALESCE((SELECT MAX(COALESCE(x.ended_at,x.started_at))
                            FROM agent_turns x WHERE x.chat_id=c.chat_id), 0),
                  COALESCE((SELECT MAX(a.ts)
                            FROM subagent_audit a
                            WHERE EXISTS (
                              SELECT 1 FROM agent_turns t
                              WHERE t.chat_id=c.chat_id
                                AND t.parent_session_id=a.parent_session_id
                            )), 0)
                ),
                COALESCE(s.viewed_through, 0)
         FROM chat_log c
         LEFT JOIN orchestrator_session_state s ON s.chat_id=c.chat_id
         WHERE c.surface='orchestrator' AND COALESCE(c.chat_id, '') <> ''
           AND s.deleted_at IS NULL
         GROUP BY c.chat_id
         ORDER BY MAX(c.id) DESC",
    )?;
    let sessions = stmt
        .query_map([], |r| {
            let latest_activity = r.get::<_, f64>(5)?;
            let viewed_through = r.get::<_, f64>(6)?;
            Ok(OrchestratorSession {
                chat_id: r.get(0)?,
                created_at: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                updated_at: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                title: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                last_request: r.get::<_, Option<String>>(4)?.unwrap_or_default(),
                latest_activity,
                viewed_through,
                unread: latest_activity > viewed_through,
                root_turns: 0,
                workers: 0,
            })
        })?
        .collect();
    sessions
}

pub fn latest_orchestrator_session_activity(
    conn: &Connection,
    chat_id: &str,
) -> rusqlite::Result<Option<f64>> {
    Ok(load_orchestrator_sessions(conn)?
        .into_iter()
        .find(|session| session.chat_id == chat_id)
        .map(|session| session.latest_activity))
}

pub fn mark_orchestrator_session_viewed(
    conn: &Connection,
    chat_id: &str,
    viewed_through: f64,
    now: f64,
) -> rusqlite::Result<bool> {
    if latest_orchestrator_session_activity(conn, chat_id)?.is_none() {
        return Ok(false);
    }
    conn.execute(
        "INSERT INTO orchestrator_session_state(chat_id,viewed_through,deleted_at,updated_at)
         VALUES(?1,?2,NULL,?3)
         ON CONFLICT(chat_id) DO UPDATE SET
           viewed_through=MAX(orchestrator_session_state.viewed_through,excluded.viewed_through),
           deleted_at=NULL,
           updated_at=excluded.updated_at",
        rusqlite::params![chat_id, viewed_through, now],
    )?;
    Ok(true)
}

pub fn delete_orchestrator_session(
    conn: &mut Connection,
    chat_id: &str,
    deleted_at: f64,
) -> rusqlite::Result<bool> {
    let exists = latest_orchestrator_session_activity(conn, chat_id)?.is_some();
    if !exists {
        return Ok(false);
    }
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO orchestrator_session_state(chat_id,viewed_through,deleted_at,updated_at)
         VALUES(?1,0,?2,?2)
         ON CONFLICT(chat_id) DO UPDATE SET
           deleted_at=excluded.deleted_at,
           updated_at=excluded.updated_at",
        rusqlite::params![chat_id, deleted_at],
    )?;
    tx.execute(
        "DELETE FROM chat_log WHERE surface='orchestrator' AND chat_id=?1",
        [chat_id],
    )?;
    tx.execute(
        "DELETE FROM session_folder_grants WHERE chat_id=?1",
        [chat_id],
    )?;
    tx.commit()?;
    Ok(true)
}

pub fn clean_orchestrator_sessions(
    conn: &mut Connection,
    deleted_at: f64,
) -> rusqlite::Result<usize> {
    let chat_ids = load_orchestrator_sessions(conn)?
        .into_iter()
        .map(|session| session.chat_id)
        .collect::<Vec<_>>();
    if chat_ids.is_empty() {
        return Ok(0);
    }
    let tx = conn.transaction()?;
    for chat_id in &chat_ids {
        tx.execute(
            "INSERT INTO orchestrator_session_state(chat_id,viewed_through,deleted_at,updated_at)
             VALUES(?1,0,?2,?2)
             ON CONFLICT(chat_id) DO UPDATE SET
               deleted_at=excluded.deleted_at,
               updated_at=excluded.updated_at",
            rusqlite::params![chat_id, deleted_at],
        )?;
        tx.execute(
            "DELETE FROM session_folder_grants WHERE chat_id=?1",
            [chat_id],
        )?;
    }
    tx.execute("DELETE FROM chat_log WHERE surface='orchestrator'", [])?;
    tx.commit()?;
    Ok(chat_ids.len())
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
    // `pushed_skill_id` was added after this table shipped (option 3). A DB
    // the Python side has not yet migrated will not have it, so gate on
    // column_exists — selecting a missing column throws and would abort the
    // whole load, blanking every panel (session history included). Fall back
    // to an empty literal when absent.
    let pushed_skill_col = if column_exists(conn, "subagent_audit", "pushed_skill_id")? {
        "COALESCE(pushed_skill_id,'')"
    } else {
        "''"
    };
    let subagent_sql = format!(
        "SELECT id, ts, event, handle_id, role, parent_session_id, parent_agent_name, \
                brief, allowed_tools, max_turns, wall_clock_timeout_s, final_status, \
                turns, tools_used, {pushed_skill_col} \
         FROM subagent_audit ORDER BY id ASC"
    );
    let mut stmt = conn.prepare(&subagent_sql)?;
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
            pushed_skill: r.get::<_, Option<String>>(14)?.unwrap_or_default(),
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
                        pushed_skill: row.pushed_skill.clone(),
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
        let request_id_expr = if column_exists(agent_turn_conn, "agent_turns", "request_id")? {
            "COALESCE(request_id, '')"
        } else {
            "''"
        };
        // Newest 120, then reversed back into ascending order — the same
        // shape `agent_cycles` uses below. `ORDER BY id ASC LIMIT 120` took
        // the OLDEST 120 turns in the whole database: past that many turns
        // the console stopped seeing any new one, so recent sessions rendered
        // as "no agent activity yet", their timelines lost rows, and the
        // per-session token ledger silently dropped every turn beyond the cap
        // while the cycle-derived economics kept counting them.
        let mut tstmt = agent_turn_conn.prepare(&format!(
            "SELECT id, {request_id_expr}, chat_id, parent_session_id, started_at, model_family, cycles, \
                    tokens_in_estimate, tokens_out_estimate \
             FROM agent_turns ORDER BY id DESC LIMIT 120"
        ))?;
        let mut turns = tstmt
            .query_map([], |r| {
                Ok(AgentTurn {
                    id: r.get(0)?,
                    request_id: r.get(1)?,
                    chat_id: r.get(2)?,
                    parent_session: r.get(3)?,
                    request: String::new(),
                    started_at: r.get(4)?,
                    model_family: r.get(5)?,
                    cycles: r.get(6)?,
                    tokens_in_estimate: r.get(7)?,
                    tokens_out_estimate: r.get(8)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        turns.reverse();
        st.agent_turns = turns;
    }

    if table_exists(conn, "runtime_log_events")? {
        let mut lstmt = conn.prepare(
            "SELECT id,request_id,chat_id,seq,ts,source,stream,agent_handle,role,category,message \
             FROM runtime_log_events ORDER BY id ASC",
        )?;
        st.runtime_log_events = lstmt
            .query_map([], |r| {
                Ok(RuntimeLogEvent {
                    id: r.get(0)?,
                    request_id: r.get(1)?,
                    chat_id: r.get(2)?,
                    seq: r.get(3)?,
                    ts: r.get(4)?,
                    source: r.get(5)?,
                    stream: r.get(6)?,
                    agent_handle: r.get::<_, Option<String>>(7)?.unwrap_or_default(),
                    role: r.get(8)?,
                    category: r.get(9)?,
                    message: r.get(10)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
    }

    if table_exists(agent_turn_conn, "agent_cycles")? {
        let expr = |column: &str, fallback: &str| -> rusqlite::Result<String> {
            Ok(if column_exists(agent_turn_conn, "agent_cycles", column)? {
                format!("COALESCE({column}, {fallback})")
            } else {
                fallback.to_string()
            })
        };
        let mut session_sources = Vec::new();
        if table_exists(agent_turn_conn, "agent_turns")?
            && column_exists(agent_turn_conn, "agent_turns", "parent_session_id")?
        {
            session_sources.push(
                "SELECT parent_session_id AS session_id FROM agent_turns \
                 WHERE COALESCE(parent_session_id, '') <> ''",
            );
        }
        if table_exists(agent_turn_conn, "pipeline_runs")?
            && column_exists(agent_turn_conn, "pipeline_runs", "session_id")?
        {
            session_sources.push(
                "SELECT session_id FROM pipeline_runs \
                 WHERE COALESCE(session_id, '') <> ''",
            );
        }
        let session_scope = session_sources.join(" UNION ");
        let has_surfaced_sessions = !session_scope.is_empty()
            && count(
                agent_turn_conn,
                &format!("SELECT COUNT(*) FROM ({session_scope})"),
            )? > 0;
        let cycle_scope = if has_surfaced_sessions {
            format!("WHERE session_id IN ({session_scope}) ORDER BY id ASC")
        } else {
            "ORDER BY id DESC LIMIT 1000".to_string()
        };
        let sql = format!(
            "SELECT session_id, stage, {}, cycle_idx, {}, {}, {}, {}, {}, {}, {} \
             FROM agent_cycles {cycle_scope}",
            expr("worker_id", "'root'")?,
            expr("lm_ms", "0")?,
            expr("tokens_in", "0")?,
            expr("cached_input_tokens", "0")?,
            expr("tokens_out", "0")?,
            expr("token_source", "'estimated'")?,
            expr("tool_calls_json", "''")?,
            expr("cycle_status", "'ok'")?,
        );
        let mut cycle_stmt = agent_turn_conn.prepare(&sql)?;
        let mut cycles = cycle_stmt
            .query_map([], |r| {
                let tool_json = r.get::<_, String>(9)?;
                Ok(AgentCycle {
                    session_id: r.get(0)?,
                    stage: r.get(1)?,
                    worker_id: r.get(2)?,
                    cycle_idx: r.get(3)?,
                    lm_ms: r.get(4)?,
                    tokens_in: r.get(5)?,
                    cached_input_tokens: r.get(6)?,
                    tokens_out: r.get(7)?,
                    token_source: r.get(8)?,
                    tool_names: parse_cycle_tools(&tool_json),
                    cycle_status: r.get(10)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        if !has_surfaced_sessions {
            cycles.reverse();
        }
        st.agent_cycles = cycles;
    }

    if table_exists(conn, "sessions")?
        && column_exists(conn, "sessions", "session_id")?
        && column_exists(conn, "sessions", "request")?
    {
        let mut request_stmt = conn.prepare("SELECT session_id, request FROM sessions")?;
        let requests = request_stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
            .collect::<rusqlite::Result<HashMap<_, _>>>()?;
        for turn in &mut st.agent_turns {
            turn.request = requests
                .get(&turn.parent_session)
                .cloned()
                .unwrap_or_default();
        }
    }

    // ── tag each run with its owning session ──
    // subagent_audit has no chat_id; agent_turns maps parent_session → chat_id.
    // The UI scopes runs to a surface by the chat_id prefix (gui-pipeline-*).
    let mut session_to_chat: std::collections::HashMap<String, String> = st
        .agent_turns
        .iter()
        .filter(|t| !t.chat_id.is_empty() && !t.parent_session.is_empty())
        .map(|t| (t.parent_session.clone(), t.chat_id.clone()))
        .collect();
    // The parent pipeline_runs row is created at driver start, while the
    // aggregate agent_turns row is written only at driver completion.
    if let Some(state_conn) = pipeline_state_conn {
        if table_exists(state_conn, "pipeline_runs")?
            && column_exists(state_conn, "pipeline_runs", "chat_id")?
        {
            let mut stmt = state_conn.prepare(
                "SELECT session_id, COALESCE(chat_id, '') FROM pipeline_runs \
                 WHERE COALESCE(chat_id, '') <> ''",
            )?;
            for row in
                stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
            {
                let (session_id, chat_id) = row?;
                session_to_chat.entry(session_id).or_insert(chat_id);
            }
        }
    }
    for agent in &mut st.subagents {
        if let Some(chat_id) = session_to_chat.get(&agent.parent_session) {
            agent.chat_id = chat_id.clone();
        }
    }

    if let Some(state_conn) = pipeline_state_conn {
        if table_exists(state_conn, "pipeline_runs")? {
            let has_pause = table_exists(state_conn, "sessions")?
                && column_exists(state_conn, "sessions", "paused_at_stage")?
                && column_exists(state_conn, "sessions", "paused_at_chunk")?
                && column_exists(state_conn, "sessions", "pause_reason")?
                && column_exists(state_conn, "sessions", "pending_action")?;
            let request_expr = if column_exists(state_conn, "pipeline_runs", "request_id")? {
                "COALESCE(pr.request_id,'')"
            } else {
                "''"
            };
            let profile_expr = if column_exists(state_conn, "pipeline_runs", "profile")? {
                "COALESCE(pr.profile,'')"
            } else {
                "''"
            };
            let task_expr = if column_exists(state_conn, "pipeline_runs", "task")? {
                "COALESCE(pr.task,'')"
            } else {
                "''"
            };
            let (join, pause_exprs) = if has_pause {
                (
                    "LEFT JOIN sessions s ON s.session_id=pr.session_id",
                    "s.paused_at_stage,s.paused_at_chunk,s.pause_reason,s.pending_action",
                )
            } else {
                ("", "NULL,NULL,NULL,NULL")
            };
            let query = format!(
                "SELECT pr.session_id,pr.pipeline_name,pr.started_at,pr.ended_at,
                        pr.final_status,{request_expr},{profile_expr},{task_expr},
                        {pause_exprs}
                 FROM pipeline_runs pr {join} ORDER BY pr.started_at ASC"
            );
            let mut pstmt = state_conn.prepare(&query)?;
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
                        .or_else(|| session_to_chat.get(&session_id))
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
                        request_id: r.get(5)?,
                        profile: r.get(6)?,
                        task: r.get(7)?,
                        brief: envelope.brief.clone(),
                        started_at: r.get(2)?,
                        ended_at: r.get(3)?,
                        status,
                        paused_at_stage: r.get(8)?,
                        paused_at_chunk: r.get(9)?,
                        pause_reason: r.get(10)?,
                        pending_action: r.get(11)?,
                        stages,
                    }))
                })?
                .collect::<rusqlite::Result<Vec<Option<_>>>>()?
                .into_iter()
                .flatten()
                .collect();
        }
    }

    // Project an audit-safe tool ledger for the runtime Logs surface. Raw
    // arguments and result bodies never cross this boundary. A worker handle
    // is attached only when (session, role) identifies exactly one audited
    // worker; retries with the same role deliberately remain unassigned.
    if table_exists(conn, "tool_audit")? {
        let mut workers_by_scope: HashMap<(String, String), Vec<String>> = HashMap::new();
        for agent in &st.subagents {
            workers_by_scope
                .entry((agent.parent_session.clone(), agent.role.clone()))
                .or_default()
                .push(agent.handle.clone());
        }
        let mut tool_stmt = conn.prepare(
            "SELECT id, ts, COALESCE(session_id,''), COALESCE(agent,''), tool,
                    COALESCE(args_json,''), COALESCE(status,'')
             FROM (SELECT id, ts, session_id, agent, tool, args_json, status
                   FROM tool_audit ORDER BY id DESC LIMIT 500)
             ORDER BY id ASC",
        )?;
        st.tool_evidence = tool_stmt
            .query_map([], |row| {
                let id = row.get(0)?;
                let ts_value = row.get::<_, Value>(1)?;
                let session_id: String = row.get(2)?;
                let role: String = row.get(3)?;
                let tool: String = row.get(4)?;
                let args_json: String = row.get(5)?;
                let status: String = row.get(6)?;
                let worker_id = match workers_by_scope.get(&(session_id.clone(), role.clone())) {
                    Some(handles) if handles.len() == 1 => handles[0].clone(),
                    _ if is_root_actor(&role) => ROOT_WORKER_ID.into(),
                    _ => String::new(),
                };
                let (category, skill_id, detail) = safe_tool_provenance(&tool, &args_json);
                Ok(ToolEvidence {
                    id,
                    ts: fmt_ts(&ts_value),
                    chat_id: session_to_chat
                        .get(&session_id)
                        .cloned()
                        .unwrap_or_default(),
                    session_id,
                    role,
                    worker_id,
                    tool,
                    category,
                    status,
                    skill_id,
                    detail,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
    }

    let pipeline_sessions = st
        .pipeline_runs
        .iter()
        .map(|run| run.session_id.as_str())
        .collect::<std::collections::HashSet<_>>();
    st.subagents
        .retain(|agent| !pipeline_sessions.contains(agent.parent_session.as_str()));

    st.orchestrator_sessions = load_orchestrator_sessions(conn)?;
    for session in &mut st.orchestrator_sessions {
        session.root_turns = st
            .agent_turns
            .iter()
            .filter(|turn| turn.chat_id == session.chat_id)
            .count() as i64;
        session.workers = st
            .subagents
            .iter()
            .filter(|agent| agent.chat_id == session.chat_id)
            .count() as i64;
    }

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
    pushed_skill: String,
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

pub fn list_session_folder_grants(
    conn: &Connection,
    chat_id: &str,
) -> rusqlite::Result<Vec<FolderGrant>> {
    let mut stmt = conn.prepare(
        "SELECT chat_id,grant_id,alias,canonical_path,ordinal
         FROM session_folder_grants WHERE chat_id=?1
         ORDER BY ordinal ASC, grant_id ASC",
    )?;
    let grants = stmt
        .query_map([chat_id], |row| {
            Ok(FolderGrant {
                chat_id: row.get(0)?,
                grant_id: row.get(1)?,
                alias: row.get(2)?,
                canonical_path: row.get(3)?,
                ordinal: row.get(4)?,
            })
        })?
        .collect();
    grants
}

pub fn insert_session_folder_grant(
    conn: &Connection,
    grant: &FolderGrant,
    now: &str,
) -> rusqlite::Result<()> {
    let current: i64 = conn.query_row(
        "SELECT COUNT(*) FROM session_folder_grants WHERE chat_id=?1",
        [&grant.chat_id],
        |row| row.get(0),
    )?;
    if current >= MAX_EXTERNAL_FOLDER_GRANTS {
        return Err(rusqlite::Error::InvalidParameterName(format!(
            "a session may attach at most {MAX_EXTERNAL_FOLDER_GRANTS} folders"
        )));
    }
    conn.execute(
        "INSERT INTO session_folder_grants
         (chat_id,grant_id,alias,canonical_path,ordinal,created_at,updated_at)
         VALUES(?1,?2,?3,?4,?5,?6,?6)",
        rusqlite::params![
            grant.chat_id,
            grant.grant_id,
            grant.alias,
            grant.canonical_path,
            grant.ordinal,
            now,
        ],
    )?;
    Ok(())
}

pub fn rename_session_folder_grant(
    conn: &Connection,
    chat_id: &str,
    grant_id: &str,
    alias: &str,
    now: &str,
) -> rusqlite::Result<bool> {
    Ok(conn.execute(
        "UPDATE session_folder_grants SET alias=?3,updated_at=?4
         WHERE chat_id=?1 AND grant_id=?2",
        rusqlite::params![chat_id, grant_id, alias, now],
    )? == 1)
}

pub fn remove_session_folder_grant(
    conn: &Connection,
    chat_id: &str,
    grant_id: &str,
) -> rusqlite::Result<bool> {
    Ok(conn.execute(
        "DELETE FROM session_folder_grants WHERE chat_id=?1 AND grant_id=?2",
        rusqlite::params![chat_id, grant_id],
    )? == 1)
}

pub fn snapshot_request_folder_grants(
    conn: &mut Connection,
    request_id: &str,
    chat_id: &str,
    musubi_root: &str,
    captured_at: &str,
) -> rusqlite::Result<()> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO request_folder_grants
         (request_id,chat_id,grant_id,alias,canonical_path,ordinal,captured_at)
         VALUES(?1,?2,'musubi','musubi',?3,-1,?4)",
        rusqlite::params![request_id, chat_id, musubi_root, captured_at],
    )?;
    tx.execute(
        "INSERT INTO request_folder_grants
         (request_id,chat_id,grant_id,alias,canonical_path,ordinal,captured_at)
         SELECT ?1,chat_id,grant_id,alias,canonical_path,ordinal,?3
         FROM session_folder_grants WHERE chat_id=?2
         ORDER BY ordinal ASC, grant_id ASC",
        rusqlite::params![request_id, chat_id, captured_at],
    )?;
    tx.commit()
}

pub fn list_request_folder_grants(
    conn: &Connection,
    request_id: &str,
) -> rusqlite::Result<Vec<FolderGrant>> {
    let mut stmt = conn.prepare(
        "SELECT chat_id,grant_id,alias,canonical_path,ordinal
         FROM request_folder_grants WHERE request_id=?1
         ORDER BY ordinal ASC, grant_id ASC",
    )?;
    let grants = stmt
        .query_map([request_id], |row| {
            Ok(FolderGrant {
                chat_id: row.get(0)?,
                grant_id: row.get(1)?,
                alias: row.get(2)?,
                canonical_path: row.get(3)?,
                ordinal: row.get(4)?,
            })
        })?
        .collect();
    grants
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
  verification_errors  TEXT,
  pushed_skill_id      TEXT                       -- root-selected skill pushed at spawn (option 3)
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
  surface TEXT NOT NULL DEFAULT 'orchestrator', -- 'orchestrator' | 'pipeline'
  chat_id TEXT NOT NULL DEFAULT ''              -- owning GUI session
);
CREATE TABLE IF NOT EXISTS agent_turns (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id           TEXT,
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
  schema_version       TEXT NOT NULL DEFAULT 'v1'
);
CREATE TABLE IF NOT EXISTS runtime_log_events (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id           TEXT NOT NULL,
  chat_id              TEXT NOT NULL,
  seq                   INTEGER NOT NULL,
  ts                    TEXT NOT NULL,
  source                TEXT NOT NULL,
  stream                TEXT NOT NULL,
  agent_handle          TEXT,
  role                  TEXT NOT NULL,
  category              TEXT NOT NULL,
  message               TEXT NOT NULL,
  UNIQUE(request_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_runtime_log_events_chat_request
  ON runtime_log_events(chat_id, request_id, seq);
CREATE INDEX IF NOT EXISTS idx_runtime_log_events_agent
  ON runtime_log_events(request_id, agent_handle, seq);
CREATE TABLE IF NOT EXISTS agent_cycles (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id           TEXT NOT NULL,
  stage                TEXT NOT NULL,
  attempt              INTEGER NOT NULL,
  chunk_id             TEXT,
  cycle_idx            INTEGER NOT NULL,
  started_at           REAL NOT NULL,
  ended_at             REAL,
  lm_ms                INTEGER NOT NULL DEFAULT 0,
  tool_calls_json      TEXT,
  text_chars           INTEGER NOT NULL DEFAULT 0,
  worker_id            TEXT NOT NULL DEFAULT 'root',
  tokens_in            INTEGER NOT NULL DEFAULT 0,
  cached_input_tokens  INTEGER NOT NULL DEFAULT 0,
  tokens_out           INTEGER NOT NULL DEFAULT 0,
  token_source         TEXT NOT NULL DEFAULT 'estimated',
  cycle_status         TEXT NOT NULL DEFAULT 'ok',
  schema_version       TEXT NOT NULL DEFAULT 'v1'
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  session_id              TEXT PRIMARY KEY,
  pipeline_name           TEXT NOT NULL,
  chat_id                 TEXT,
  request_id              TEXT,
  profile                 TEXT,
  task                    TEXT,
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
CREATE TABLE IF NOT EXISTS orchestrator_session_state (
  chat_id        TEXT PRIMARY KEY,
  viewed_through REAL NOT NULL DEFAULT 0,
  deleted_at     REAL,
  updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_folder_grants (
  chat_id        TEXT NOT NULL,
  grant_id       TEXT NOT NULL,
  alias          TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  ordinal        INTEGER NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (chat_id, grant_id),
  UNIQUE (chat_id, alias),
  UNIQUE (chat_id, canonical_path)
);
CREATE INDEX IF NOT EXISTS idx_session_folder_grants_chat_order
  ON session_folder_grants(chat_id, ordinal, grant_id);
CREATE TABLE IF NOT EXISTS request_folder_grants (
  request_id     TEXT NOT NULL,
  chat_id        TEXT NOT NULL,
  grant_id       TEXT NOT NULL,
  alias          TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  ordinal        INTEGER NOT NULL,
  captured_at    TEXT NOT NULL,
  PRIMARY KEY (request_id, grant_id),
  UNIQUE (request_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_request_folder_grants_chat
  ON request_folder_grants(chat_id, request_id, ordinal);
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
    fn orchestrator_session_unread_cursor_advances_and_later_activity_reopens_it() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(id,ts,role,text,surface,chat_id)
             VALUES(1,'epoch:100','you','first','orchestrator','chat-a')",
            [],
        )
        .unwrap();

        let initial = load_orchestrator_sessions(&conn).unwrap();
        assert_eq!(initial.len(), 1);
        assert!(initial[0].unread);
        assert_eq!(initial[0].latest_activity, 100.0);

        mark_orchestrator_session_viewed(&conn, "chat-a", 100.0, 101.0).unwrap();
        assert!(!load_orchestrator_sessions(&conn).unwrap()[0].unread);

        conn.execute(
            "INSERT INTO runtime_log_events
             (request_id,chat_id,seq,ts,source,stream,role,category,message)
             VALUES('req-a','chat-a',1,'epoch:102','host','host','host','host','done')",
            [],
        )
        .unwrap();
        let reopened = load_orchestrator_sessions(&conn).unwrap();
        assert!(reopened[0].unread);
        assert_eq!(reopened[0].latest_activity, 102.0);
        assert_eq!(reopened[0].viewed_through, 100.0);
    }

    #[test]
    fn delete_orchestrator_session_hides_chat_and_grants_but_preserves_evidence() {
        let mut conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO chat_log(id,ts,role,text,surface,chat_id)
             VALUES(1,'epoch:100','you','first','orchestrator','chat-a')",
            [],
        )
        .unwrap();
        insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "chat-a".into(),
                grant_id: "grant-a".into(),
                alias: "docs".into(),
                canonical_path: "D:/docs".into(),
                ordinal: 0,
            },
            "100",
        )
        .unwrap();
        snapshot_request_folder_grants(&mut conn, "req-a", "chat-a", "C:/Musubi", "101").unwrap();
        conn.execute(
            "INSERT INTO runtime_log_events
             (request_id,chat_id,seq,ts,source,stream,role,category,message)
             VALUES('req-a','chat-a',1,'epoch:102','host','host','host','host','done')",
            [],
        )
        .unwrap();

        assert!(delete_orchestrator_session(&mut conn, "chat-a", 103.0).unwrap());

        assert!(load_orchestrator_sessions(&conn).unwrap().is_empty());
        assert!(list_session_folder_grants(&conn, "chat-a")
            .unwrap()
            .is_empty());
        assert_eq!(list_request_folder_grants(&conn, "req-a").unwrap().len(), 2);
        assert_eq!(
            count(
                &conn,
                "SELECT COUNT(*) FROM runtime_log_events WHERE chat_id='chat-a'"
            )
            .unwrap(),
            1
        );
    }

    #[test]
    fn folder_grants_are_session_scoped_ordered_and_snapshotted() {
        let mut conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();

        insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "chat-a".into(),
                grant_id: "g-web".into(),
                alias: "web".into(),
                canonical_path: "D:/work/web".into(),
                ordinal: 1,
            },
            "100",
        )
        .unwrap();
        insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "chat-a".into(),
                grant_id: "g-api".into(),
                alias: "api".into(),
                canonical_path: "D:/work/api".into(),
                ordinal: 0,
            },
            "101",
        )
        .unwrap();
        insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "chat-b".into(),
                grant_id: "g-docs".into(),
                alias: "docs".into(),
                canonical_path: "D:/work/docs".into(),
                ordinal: 0,
            },
            "102",
        )
        .unwrap();

        let current = list_session_folder_grants(&conn, "chat-a").unwrap();
        assert_eq!(
            current
                .iter()
                .map(|grant| grant.alias.as_str())
                .collect::<Vec<_>>(),
            vec!["api", "web"]
        );

        snapshot_request_folder_grants(&mut conn, "req-1", "chat-a", "C:/Musubi", "103").unwrap();
        remove_session_folder_grant(&conn, "chat-a", "g-web").unwrap();

        assert_eq!(
            list_session_folder_grants(&conn, "chat-a").unwrap().len(),
            1
        );
        let snapshot = list_request_folder_grants(&conn, "req-1").unwrap();
        assert_eq!(
            snapshot
                .iter()
                .map(|grant| grant.alias.as_str())
                .collect::<Vec<_>>(),
            vec!["musubi", "api", "web"]
        );
        assert_eq!(snapshot[0].canonical_path, "C:/Musubi");
    }

    #[test]
    fn folder_grants_reject_duplicate_alias_path_and_seventeenth_root() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        for index in 0..16 {
            insert_session_folder_grant(
                &conn,
                &FolderGrant {
                    chat_id: "chat".into(),
                    grant_id: format!("g-{index}"),
                    alias: format!("root-{index}"),
                    canonical_path: format!("D:/work/{index}"),
                    ordinal: index,
                },
                "100",
            )
            .unwrap();
        }

        let overflow = insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "chat".into(),
                grant_id: "overflow".into(),
                alias: "overflow".into(),
                canonical_path: "D:/work/overflow".into(),
                ordinal: 16,
            },
            "100",
        );
        assert!(overflow.is_err());

        let other = insert_session_folder_grant(
            &conn,
            &FolderGrant {
                chat_id: "other".into(),
                grant_id: "other-id".into(),
                alias: "root-0".into(),
                canonical_path: "D:/work/0".into(),
                ordinal: 0,
            },
            "100",
        );
        assert!(other.is_ok(), "uniqueness is scoped to one chat");
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
        assert!(v.get("pipelineBuilderCatalog").is_some());
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
    fn spawn_row_pushed_skill_id_surfaces_on_agent() {
        // Option 3: a root-selected skill is recorded on the spawn row, not as
        // a musubi_get_skill tool-call. The reader must surface it so the
        // Console can show which skill the worker received.
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s,pushed_skill_id)\
             VALUES(1,1000.0,'spawned','w-1','session-1','driver','coder','build dashboard','[\"Write\"]',6,300,'web-ui')",
            [],
        )
        .unwrap();

        let st = load_state_at(&conn, 2000).unwrap();

        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].pushed_skill, "web-ui");
    }

    #[test]
    fn reader_tolerates_subagent_audit_without_pushed_skill_column() {
        // Regression: a DB created before pushed_skill_id existed must still
        // load. Selecting the missing column would throw and abort load_state,
        // blanking the whole Console (session history included).
        let conn = Connection::open_in_memory().unwrap();
        // Old schema: subagent_audit WITHOUT pushed_skill_id.
        conn.execute_batch(
            "CREATE TABLE subagent_audit (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,\
                handle_id TEXT NOT NULL, parent_session_id TEXT NOT NULL,\
                parent_agent_name TEXT NOT NULL, role TEXT NOT NULL, brief TEXT NOT NULL,\
                event TEXT NOT NULL, allowed_tools TEXT, max_turns INTEGER,\
                wall_clock_timeout_s INTEGER, final_status TEXT, escalated INTEGER,\
                turns INTEGER, tools_used TEXT, summary_truncated INTEGER,\
                verification_errors TEXT);\
             CREATE TABLE tool_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,\
                session_id TEXT, agent TEXT, tool TEXT, args_json TEXT, status TEXT);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(1,1000.0,'spawned','w-old','session-1','driver','coder','build','[\"Write\"]',6,300)",
            [],
        )
        .unwrap();

        // Must NOT error, and the missing column degrades to empty.
        let st = load_state_at(&conn, 2000).unwrap();
        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].pushed_skill, "");
    }

    #[test]
    fn spawn_row_without_pushed_skill_is_empty() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO subagent_audit\
             (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
             VALUES(1,1000.0,'spawned','w-2','session-1','driver','explorer','look','[\"Read\"]',6,300)",
            [],
        )
        .unwrap();

        let st = load_state_at(&conn, 2000).unwrap();

        assert_eq!(st.subagents.len(), 1);
        assert_eq!(st.subagents[0].pushed_skill, "");
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
    }

    #[test]
    fn agent_turns_keep_the_newest_when_the_cap_is_reached() {
        // The cap used to take the OLDEST 120 rows, so a console past 120
        // turns never saw another new one: fresh sessions rendered as "no
        // agent activity yet" and their token ledger came back empty.
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        for id in 1..=130 {
            conn.execute(
                "INSERT INTO agent_turns\
                 (id,chat_id,parent_session_id,started_at,model_family,cycles,tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)\
                 VALUES(?1,'chat-a',?2,?3,'deepseek',1,100,20,300,500)",
                rusqlite::params![id, format!("session-{id}"), id as f64],
            )
            .unwrap();
        }

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_turns.len(), 120);
        // Ascending order is preserved, and the window ends at the newest row.
        assert_eq!(st.agent_turns[0].parent_session, "session-11");
        assert_eq!(st.agent_turns[119].parent_session, "session-130");
    }

    #[test]
    fn orchestrator_sessions_list_only_non_empty_chat_ids_newest_first() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute_batch(
            "INSERT INTO chat_log(id,ts,role,text,surface,chat_id) VALUES
             (1,'100','you','old request','orchestrator','gui-orchestrator-project-old'),
             (2,'101','driver','old answer','orchestrator','gui-orchestrator-project-old'),
             (3,'200','you','new request','orchestrator','gui-orchestrator-project-new'),
             (4,'201','driver','new answer','orchestrator','gui-orchestrator-project-new'),
             (5,'300','you','pipeline request','pipeline','gui-pipeline-project-one');",
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.orchestrator_sessions.len(), 2);
        assert_eq!(
            st.orchestrator_sessions[0].chat_id,
            "gui-orchestrator-project-new"
        );
        assert_eq!(st.orchestrator_sessions[0].last_request, "new request");
        assert_eq!(st.orchestrator_sessions[1].title, "old request");
    }

    #[test]
    fn agent_turn_root_request_comes_from_audit_session() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (
               session_id TEXT PRIMARY KEY,
               request TEXT NOT NULL,
               status TEXT NOT NULL,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
             );
             INSERT INTO sessions(session_id,request,status,created_at,updated_at)
             VALUES('root-session','build the dashboard','complete','100','101');
             INSERT INTO agent_turns
             (id,chat_id,parent_session_id,started_at,model_family,cycles,
              tokens_in_estimate,tokens_out_estimate,lm_ms,total_ms)
             VALUES(1,'gui-orchestrator-project-one','root-session',100.0,
                    'deepseek',1,100,20,300,500);",
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_turns[0].request, "build the dashboard");
    }

    #[test]
    fn loads_current_agent_cycle_economics() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO agent_cycles\
             (session_id,stage,attempt,cycle_idx,started_at,worker_id,lm_ms,\
              tokens_in,cached_input_tokens,tokens_out,token_source,\
              tool_calls_json,cycle_status)\
             VALUES('s','code',1,2,1.0,'worker-7',150,1200,800,90,\
                    'provider','[\"musubi_read_file\",\"musubi_grep\"]','final')",
            [],
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_cycles.len(), 1);
        let cycle = &st.agent_cycles[0];
        assert_eq!(cycle.session_id, "s");
        assert_eq!(cycle.worker_id, "worker-7");
        assert_eq!(cycle.tokens_in, 1200);
        assert_eq!(cycle.cached_input_tokens, 800);
        assert_eq!(cycle.tokens_out, 90);
        assert_eq!(cycle.token_source, "provider");
        assert_eq!(cycle.tool_names, vec!["musubi_read_file", "musubi_grep"]);
    }

    #[test]
    fn tool_evidence_exposes_only_safe_provenance_and_exact_worker_when_unambiguous() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute_batch(
            "INSERT INTO subagent_audit
             (ts,handle_id,parent_session_id,parent_agent_name,role,brief,event,
              allowed_tools,max_turns,wall_clock_timeout_s)
             VALUES(10.0,'worker-1','session-1','agent','coder','build','spawned',
                    '[\"musubi_get_skill\"]',8,300);
             INSERT INTO tool_audit
             (id,ts,session_id,pipeline,agent,tool,args_json,result_hash,status)
             VALUES(7,11.0,'session-1','standalone-agent','coder','musubi_get_skill',
                    '{\"skill_id\":\"python\",\"agent_name\":\"coder\",\"secret\":\"hidden\"}',
                    'sha256:abc','ok');",
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.tool_evidence.len(), 1);
        let row = &st.tool_evidence[0];
        assert_eq!(row.worker_id, "worker-1");
        assert_eq!(row.skill_id, "python");
        assert_eq!(row.category, "skills");
        assert_eq!(row.detail, "skill python");
        let json = serde_json::to_string(row).unwrap();
        assert!(!json.contains("secret"));
        assert!(!json.contains("hidden"));
        assert!(!json.contains("argsJson"));
    }

    #[test]
    fn tool_evidence_does_not_guess_worker_for_repeated_roles() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute_batch(
            "INSERT INTO subagent_audit
             (ts,handle_id,parent_session_id,parent_agent_name,role,brief,event,
              allowed_tools,max_turns,wall_clock_timeout_s)
             VALUES
             (10.0,'worker-1','session-1','agent','coder','first','spawned','[]',8,300),
             (12.0,'worker-2','session-1','agent','coder','retry','spawned','[]',8,300);
             INSERT INTO tool_audit
             (id,ts,session_id,pipeline,agent,tool,args_json,status)
             VALUES(8,13.0,'session-1','standalone-agent','coder','musubi_read_file',
                    '{\"path\":\"private.txt\"}','ok');",
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.tool_evidence.len(), 1);
        assert_eq!(st.tool_evidence[0].worker_id, "");
        assert_eq!(st.tool_evidence[0].detail, "");
    }

    #[test]
    fn tool_evidence_rejects_unsafe_or_oversized_skill_identifiers() {
        let oversized = "x".repeat(81);
        for value in ["../secret", oversized.as_str()] {
            let args = serde_json::json!({ "skill_id": value }).to_string();
            let (category, skill_id, detail) = safe_tool_provenance("musubi_get_skill", &args);
            assert_eq!(category, "skills");
            assert!(skill_id.is_empty());
            assert!(detail.is_empty());
        }
    }

    #[test]
    fn loads_legacy_agent_cycles_with_safe_defaults() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute_batch(
            "DROP TABLE agent_cycles;
             CREATE TABLE agent_cycles (
               id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
               stage TEXT NOT NULL, cycle_idx INTEGER NOT NULL,
               tool_calls_json TEXT
             );
             INSERT INTO agent_cycles VALUES
               (1,'s','plan',0,'[{\"name\":\"old_tool\",\"ok\":true}]'),
               (2,'s','plan',1,'not-json');",
        )
        .unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_cycles.len(), 2);
        assert_eq!(st.agent_cycles[0].worker_id, "root");
        assert_eq!(st.agent_cycles[0].tokens_in, 0);
        assert_eq!(st.agent_cycles[0].token_source, "estimated");
        assert_eq!(st.agent_cycles[0].tool_names, vec!["old_tool"]);
        assert!(st.agent_cycles[1].tool_names.is_empty());
    }

    #[test]
    fn agent_cycles_reader_keeps_the_newest_thousand_rows() {
        let mut conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        let tx = conn.transaction().unwrap();
        for id in 1..=1001 {
            tx.execute(
                "INSERT INTO agent_cycles
                 (id,session_id,stage,attempt,cycle_idx,started_at)
                 VALUES(?1,?2,'agent',1,0,1.0)",
                rusqlite::params![id, format!("session-{id}")],
            )
            .unwrap();
        }
        tx.commit().unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_cycles.len(), 1000);
        assert_eq!(st.agent_cycles.first().unwrap().session_id, "session-2");
        assert_eq!(st.agent_cycles.last().unwrap().session_id, "session-1001");
    }

    #[test]
    fn agent_cycles_reader_keeps_cycles_for_older_surfaced_session() {
        let mut conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO agent_turns
             (chat_id,parent_session_id,started_at,model_family)
             VALUES('old-chat','visible-old',1.0,'openai')",
            [],
        )
        .unwrap();
        let tx = conn.transaction().unwrap();
        tx.execute(
            "INSERT INTO agent_cycles
             (id,session_id,stage,attempt,cycle_idx,started_at)
             VALUES(1,'visible-old','agent',1,0,1.0)",
            [],
        )
        .unwrap();
        for id in 2..=1002 {
            tx.execute(
                "INSERT INTO agent_cycles
                 (id,session_id,stage,attempt,cycle_idx,started_at)
                 VALUES(?1,?2,'agent',1,0,1.0)",
                rusqlite::params![id, format!("noise-{id}")],
            )
            .unwrap();
        }
        tx.commit().unwrap();

        let st = load_state(&conn).unwrap();

        assert_eq!(st.agent_cycles.len(), 1);
        assert_eq!(st.agent_cycles[0].session_id, "visible-old");
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
    fn live_subagent_chat_id_resolved_from_parent_pipeline_run() {
        let audit = Connection::open_in_memory().unwrap();
        let state = Connection::open_in_memory().unwrap();
        init_schema(&audit).unwrap();
        init_schema(&state).unwrap();
        audit
            .execute(
                "INSERT INTO subagent_audit\
                 (id,ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,allowed_tools,max_turns,wall_clock_timeout_s)\
                 VALUES(1,1000.0,'spawned','live-worker','parent-run','agent','coder','build it','[]',8,300)",
                [],
            )
            .unwrap();
        state
            .execute(
                "INSERT INTO pipeline_runs(session_id,pipeline_name,chat_id,started_at)\
                 VALUES('parent-run','feature-dev','gui-orchestrator-current',1000.0)",
                [],
            )
            .unwrap();

        let st = load_state_with_pipeline_runs(&audit, Some(&state)).unwrap();

        assert_eq!(st.subagents[0].chat_id, "gui-orchestrator-current");
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
    fn driver_status_serializes_exact_chat_id() {
        let status = DriverStatus {
            chat_id: "gui-pipeline-project-session".into(),
            ..DriverStatus::default()
        };

        let value = serde_json::to_value(status).unwrap();

        assert_eq!(value["chatId"], "gui-pipeline-project-session");
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
            AgentLaunchScope::default(),
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
            AgentLaunchScope::default(),
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
            AgentLaunchScope::default(),
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
            AgentLaunchScope {
                chat_id: Some("gui-orchestrator"),
                ..AgentLaunchScope::default()
            },
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
    fn launch_spec_adds_operator_token_budget() {
        let spec = build_agent_launch_spec(
            "task",
            "",
            "",
            None,
            Path::new("/proj"),
            &std::collections::HashMap::new(),
            AgentLaunchScope {
                max_tokens: Some(240_000),
                ..AgentLaunchScope::default()
            },
        )
        .unwrap();

        assert_eq!(
            spec.args,
            vec!["task", "--max-tokens", "240000", "--tool-surface", "agent"]
        );
    }

    #[test]
    fn launch_spec_rejects_negative_token_budget() {
        let error = build_agent_launch_spec(
            "task",
            "",
            "",
            None,
            Path::new("/proj"),
            &std::collections::HashMap::new(),
            AgentLaunchScope {
                max_tokens: Some(-1),
                ..AgentLaunchScope::default()
            },
        )
        .unwrap_err();

        assert!(error.contains("token budget"));
    }

    #[test]
    fn launch_specs_for_project_sessions_share_the_project_root() {
        let root = PathBuf::from("/proj");
        let env = std::collections::HashMap::new();
        let first = build_agent_launch_spec(
            "first",
            "",
            "",
            None,
            &root,
            &env,
            AgentLaunchScope {
                chat_id: Some("gui-pipeline-project-a"),
                pipeline_name: Some("feature-dev"),
                ..AgentLaunchScope::default()
            },
        )
        .unwrap();
        let second = build_agent_launch_spec(
            "second",
            "",
            "",
            None,
            &root,
            &env,
            AgentLaunchScope {
                chat_id: Some("gui-pipeline-project-b"),
                pipeline_name: Some("feature-dev"),
                ..AgentLaunchScope::default()
            },
        )
        .unwrap();

        assert_eq!(first.cwd, root);
        assert_eq!(second.cwd, root);
        assert_ne!(first.args, second.args);
        assert!(!first.cwd.to_string_lossy().contains("project-a"));
        assert!(!second.cwd.to_string_lossy().contains("project-b"));
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
            AgentLaunchScope {
                chat_id: Some("gui-pipeline-abc"),
                pipeline_name: Some("feature-dev"),
                ..AgentLaunchScope::default()
            },
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
    fn launch_spec_forwards_request_identity_and_enables_log_protocol() {
        let spec = build_agent_launch_spec(
            "ship it",
            "",
            "",
            None,
            Path::new("/proj"),
            &std::collections::HashMap::new(),
            AgentLaunchScope {
                chat_id: Some("gui-orchestrator-abc"),
                request_id: Some("request-42"),
                ..AgentLaunchScope::default()
            },
        )
        .unwrap();

        assert!(spec
            .env
            .contains(&("MUSUBI_REQUEST_ID".into(), "request-42".into())));
        assert!(spec
            .env
            .contains(&("MUSUBI_RUNTIME_LOG_PROTOCOL".into(), "1".into())));
    }

    #[test]
    fn state_loads_append_only_runtime_events_in_sequence() {
        let conn = Connection::open_in_memory().unwrap();
        init_schema(&conn).unwrap();
        conn.execute(
            "INSERT INTO runtime_log_events(
               request_id,chat_id,seq,ts,source,stream,agent_handle,role,category,message
             ) VALUES
               ('request-1','chat-1',1,'epoch:1','host','host',NULL,'host','host','launch'),
               ('request-1','chat-1',2,'epoch:2','worker','stderr','worker-7','coder','tools','write ok')",
            [],
        )
        .unwrap();

        let state = load_state_at(&conn, 10).unwrap();

        assert_eq!(state.runtime_log_events.len(), 2);
        assert_eq!(state.runtime_log_events[1].agent_handle, "worker-7");
        assert_eq!(state.runtime_log_events[1].category, "tools");
    }

    #[test]
    fn studio_pipeline_catalog_discovers_all_safe_valid_registered_recipes() {
        let root = temp_dir("studio-pipeline-catalog");
        write_recipe_fixture(&root);
        let pipeline_root = root.join(".github").join("pipelines");
        let presets = pipeline_root.join("presets");
        let agents = root.join(".github").join("agents").join("workers");
        for (preset, agent, stage) in [
            ("scope", "scoper", "scope"),
            ("findings", "finder", "findings"),
            ("synthesis", "synthesizer", "synthesis"),
        ] {
            std::fs::write(
                presets.join(format!("{preset}.yaml")),
                format!("id: {preset}\nagent: {agent}\nstage: {stage}\n"),
            )
            .unwrap();
            std::fs::write(
                agents.join(format!("{agent}.agent.md")),
                format!(
                    "---\nname: {agent}\nmaxTurns: 4\nmaxOutputTokens: 4096\ntools: [Read, View]\n---\n\n# {agent}\n"
                ),
            )
            .unwrap();
        }
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
            vec!["code-review", "dev-lite", "feature-dev"]
        );
        assert_eq!(catalog[0].stages, vec!["scope", "findings", "synthesis"]);
        assert_eq!(catalog[1].stages, vec!["plan", "build", "check"]);
        assert_eq!(catalog[2].description, "Ship a feature");
        assert_eq!(catalog[2].stages, vec!["planner", "coder", "reviewer"]);
        assert!(catalog.iter().all(|p| p.runnable));
    }

    fn write_recipe_fixture(root: &Path) {
        let presets = root.join(".github").join("pipelines").join("presets");
        let agents = root.join(".github").join("agents").join("workers");
        std::fs::create_dir_all(&presets).unwrap();
        std::fs::create_dir_all(&agents).unwrap();
        std::fs::write(
            presets.join("plan.yaml"),
            "id: plan\nagent: planner\nstage: plan\n",
        )
        .unwrap();
        std::fs::write(
            presets.join("build.yaml"),
            "id: build\nagent: coder\nstage: code\n",
        )
        .unwrap();
        std::fs::write(
            presets.join("check.yaml"),
            "id: check\nagent: reviewer\nstage: review\n",
        )
        .unwrap();
        for (role, turns, tools) in [
            ("planner", 4, "[Read, View]"),
            ("coder", 8, "[Read, View, Write, Edit, Bash]"),
            ("reviewer", 4, "[Read, View]"),
            ("explorer", 4, "[Read, View, Grep, Glob]"),
        ] {
            std::fs::write(
                agents.join(format!("{role}.agent.md")),
                format!(
                    "---\nname: {role}\nmaxTurns: {turns}\nmaxOutputTokens: 4096\ntools: {tools}\n---\n\n# {role}\n"
                ),
            )
            .unwrap();
        }
    }

    #[test]
    fn pipeline_builder_catalog_projects_resolved_contracts_and_spawn_precedence() {
        let root = temp_dir("pipeline-builder-catalog-contracts");
        write_recipe_fixture(&root);
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::write(
            root.join("scripts/policy_engine.py"),
            "MAIN_SUBAGENT_ALLOWLIST: dict[str, list[str]] = {\n    \"coder\": [\"explorer\"],\n}\n",
        )
        .unwrap();
        let coder = root
            .join(".github")
            .join("agents")
            .join("workers")
            .join("coder.agent.md");
        std::fs::write(
            &coder,
            "---\nname: Coder Display\nmaxTurns: 8\nmaxOutputTokens: 4096\ntools: [Read, Write]\nspawn_allowlist: [reviewer-aux]\n---\n\n# Coder prompt\n",
        )
        .unwrap();

        let catalog = read_pipeline_builder_catalog(&root);

        let build = catalog
            .presets
            .iter()
            .find(|item| item.id == "build")
            .unwrap();
        assert_eq!(build.agent, "coder");
        assert_eq!(build.stage, "code");
        assert!(build.source_path.ends_with("build.yaml"));
        assert!(build.runnable, "{}", build.blocked_reason);
        let agent = catalog
            .agents
            .iter()
            .find(|item| item.name == "coder")
            .unwrap();
        assert_eq!(agent.display_label, "Coder Display");
        assert_eq!(agent.prompt_path, coder.to_string_lossy());
        assert_eq!(agent.allowed_tools, vec!["Read", "Write"]);
        assert_eq!(agent.max_turns, 8);
        assert_eq!(agent.max_output_tokens, Some(4096));
        assert_eq!(agent.spawn_allowlist, vec!["reviewer-aux"]);
        assert_eq!(
            agent.source_paths,
            vec![coder.to_string_lossy().to_string()]
        );
        assert!(agent.runnable, "{}", agent.blocked_reason);
    }

    #[test]
    fn pipeline_builder_catalog_blocks_malformed_and_unknown_entries_fail_closed() {
        let root = temp_dir("pipeline-builder-catalog-blocked");
        write_recipe_fixture(&root);
        let presets = root.join(".github").join("pipelines").join("presets");
        let agents = root.join(".github").join("agents").join("workers");
        std::fs::write(
            presets.join("unknown.yaml"),
            "id: unknown\nagent: missing\nstage: investigate\n",
        )
        .unwrap();
        std::fs::write(presets.join("malformed.yaml"), "id: [\n").unwrap();
        std::fs::write(
            agents.join("broken.agent.md"),
            "---\nname: Broken\nmaxTurns: 4\ntools: Read\n---\n# prompt\n",
        )
        .unwrap();

        let catalog = read_pipeline_builder_catalog(&root);

        for id in ["unknown", "malformed"] {
            let item = catalog.presets.iter().find(|item| item.id == id).unwrap();
            assert!(!item.runnable);
            assert!(!item.blocked_reason.is_empty());
        }
        let broken = catalog
            .agents
            .iter()
            .find(|item| item.name == "broken")
            .unwrap();
        assert!(!broken.runnable);
        assert!(!broken.blocked_reason.is_empty());
    }

    fn valid_pipeline_recipe(name: &str) -> PipelineRecipe {
        PipelineRecipe {
            name: name.into(),
            description: "A governed recipe".into(),
            version: "1.0.0".into(),
            baseline_checks: vec![],
            correction: serde_yaml::Value::Null,
            stages: vec![
                PipelineStageRecipe {
                    preset: "plan".into(),
                    agent: String::new(),
                    stage: String::new(),
                    spawns: vec![],
                },
                PipelineStageRecipe {
                    preset: "check".into(),
                    agent: String::new(),
                    stage: String::new(),
                    spawns: vec![],
                },
            ],
            resolved_contracts: vec![],
            findings: vec![],
        }
    }

    #[test]
    fn pipeline_recipe_read_render_read_preserves_flat_order_and_overrides() {
        let root = temp_dir("pipeline-recipe-roundtrip");
        write_recipe_fixture(&root);
        let dir = root.join(".github").join("pipelines").join("custom-flow");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("pipeline.yaml"),
            "name: custom-flow\ndescription: Custom\nversion: 1.2.3\nstages:\n  - preset: plan\n    agent: coder\n    stage: implement\n    spawns: [explorer]\n  - preset: check\n",
        )
        .unwrap();

        let recipe = read_pipeline_recipe(&root, "custom-flow").unwrap();
        assert_eq!(recipe.stages[0].preset, "plan");
        assert_eq!(recipe.stages[0].agent, "coder");
        assert_eq!(recipe.stages[0].stage, "implement");
        assert_eq!(recipe.stages[0].spawns, vec!["explorer"]);
        let rendered = render_pipeline_recipe(&recipe, "", &serde_yaml::Mapping::new()).unwrap();
        assert!(rendered.contains("stages:"));
        assert!(!rendered.contains("generator:"));
        assert!(!rendered.contains("allowedTools"));
        assert!(!rendered.contains("maxTurns"));
        assert!(!rendered.contains("maxOutputTokens"));
        std::fs::write(dir.join("pipeline.yaml"), rendered).unwrap();
        let reread = read_pipeline_recipe(&root, "custom-flow").unwrap();
        assert_eq!(reread.stages, recipe.stages);
    }

    /// A hand-authored recipe: a musubi-tier header block and three top-level
    /// keys the Studio has no field for. Mirrors code-review/pipeline.yaml.
    fn write_tagged_recipe(root: &Path, name: &str) {
        let dir = root.join(".github").join("pipelines").join(name);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("pipeline.yaml"),
            format!(
                "# musubi-tier: ephemeral\n\
                 # expires-when: the 4-stage pipeline is dissolved\n\
                 # cost-lever: deletes the whole pipeline runtime\n\
                 name: {name}\n\
                 description: A governed recipe\n\
                 version: 1.0.0\n\
                 level: 2\n\
                 max_credits: 20\n\
                 warn_at: 0.8\n\
                 stages:\n  - preset: plan\n  - preset: check\n"
            ),
        )
        .unwrap();
    }

    #[test]
    fn updating_a_tagged_recipe_keeps_its_tier_block_and_unmodelled_keys() {
        let root = temp_dir("pipeline-recipe-preserve-prelude");
        write_recipe_fixture(&root);
        write_tagged_recipe(&root, "code-review");
        let mut recipe = read_pipeline_recipe(&root, "code-review").unwrap();
        recipe.description = "Edited in the Studio".into();

        assert!(save_pipeline_recipe(&root, &recipe).saved);

        let written =
            std::fs::read_to_string(root.join(".github/pipelines/code-review/pipeline.yaml"))
                .unwrap();
        // The edit lands...
        assert!(written.contains("description: Edited in the Studio"));
        // ...and nothing the Studio does not model is lost. Rendering from the
        // six modelled fields alone used to drop every line below.
        assert!(written.starts_with("# musubi-tier: ephemeral\n"));
        assert!(written.contains("# cost-lever: deletes the whole pipeline runtime"));
        assert!(written.contains("max_credits: 20"));
        assert!(written.contains("warn_at: 0.8"));
        assert!(written.contains("level: 2"));
        // The legacy stage keys are the one thing deliberately not carried:
        // `stages:` supersedes them.
        assert!(!written.contains("generator:"));
        assert_eq!(
            read_pipeline_recipe(&root, "code-review").unwrap().stages,
            recipe.stages
        );
    }

    #[test]
    fn saving_under_a_new_name_starts_from_a_clean_file() {
        let root = temp_dir("pipeline-recipe-clone-clean");
        write_recipe_fixture(&root);
        write_tagged_recipe(&root, "code-review");
        let mut recipe = read_pipeline_recipe(&root, "code-review").unwrap();
        recipe.name = "code-review-copy".into();

        assert!(save_pipeline_recipe(&root, &recipe).saved);

        // A clone is a new recipe, not a second copy of the original's
        // governance tag or its credit budget.
        let written =
            std::fs::read_to_string(root.join(".github/pipelines/code-review-copy/pipeline.yaml"))
                .unwrap();
        assert!(!written.contains("musubi-tier"));
        assert!(!written.contains("max_credits"));
        assert!(!pipeline_is_protected(&root, "code-review-copy"));
        assert!(pipeline_is_protected(&root, "code-review"));
    }

    #[test]
    fn deleting_refuses_repository_owned_recipes_and_removes_studio_ones() {
        let root = temp_dir("pipeline-recipe-delete");
        write_recipe_fixture(&root);
        write_tagged_recipe(&root, "code-review");
        assert!(save_pipeline_recipe(&root, &valid_pipeline_recipe("my-flow")).saved);

        let refused = delete_pipeline_recipe(&root, "code-review");
        assert!(!refused.deleted);
        assert!(refused.error.contains("repository-owned"));
        assert!(root
            .join(".github/pipelines/code-review/pipeline.yaml")
            .exists());

        let removed = delete_pipeline_recipe(&root, "my-flow");
        assert!(removed.deleted, "{}", removed.error);
        assert!(removed.catalog_refreshed);
        assert!(!root.join(".github/pipelines/my-flow").exists());

        // Fail-closed on the paths that never name a real recipe.
        assert!(!delete_pipeline_recipe(&root, "my-flow").deleted);
        assert!(!delete_pipeline_recipe(&root, "../escape").deleted);
        assert!(!delete_pipeline_recipe(&root, "").deleted);
    }

    #[test]
    fn the_catalog_marks_which_recipes_are_repository_owned() {
        let root = temp_dir("pipeline-catalog-protected");
        write_recipe_fixture(&root);
        write_tagged_recipe(&root, "code-review");
        assert!(save_pipeline_recipe(&root, &valid_pipeline_recipe("my-flow")).saved);

        let catalog = read_studio_pipeline_catalog(&root);
        let protected = |name: &str| {
            catalog
                .iter()
                .find(|entry| entry.name == name)
                .map(|entry| entry.protected)
        };
        assert_eq!(protected("code-review"), Some(true));
        assert_eq!(protected("my-flow"), Some(false));
    }

    #[test]
    fn pipeline_recipe_invalid_save_preserves_existing_file() {
        let root = temp_dir("pipeline-recipe-invalid-save");
        write_recipe_fixture(&root);
        let mut recipe = valid_pipeline_recipe("safe-flow");
        assert!(save_pipeline_recipe(&root, &recipe).saved);
        let path = root.join(".github/pipelines/safe-flow/pipeline.yaml");
        let before = std::fs::read_to_string(&path).unwrap();
        recipe.stages.truncate(1);

        let result = save_pipeline_recipe(&root, &recipe);

        assert!(!result.saved);
        assert_eq!(std::fs::read_to_string(path).unwrap(), before);
    }

    #[test]
    fn pipeline_recipe_atomic_writer_failure_preserves_existing_file() {
        let root = temp_dir("pipeline-recipe-atomic-failure");
        write_recipe_fixture(&root);
        let recipe = valid_pipeline_recipe("safe-flow");
        assert!(save_pipeline_recipe(&root, &recipe).saved);
        let path = root.join(".github/pipelines/safe-flow/pipeline.yaml");
        let before = std::fs::read_to_string(&path).unwrap();
        let expected_temp =
            render_pipeline_recipe(&recipe, "", &serde_yaml::Mapping::new()).unwrap();
        let observed_temp = std::rc::Rc::new(std::cell::RefCell::new(None));
        let captured_temp = observed_temp.clone();

        let result = save_pipeline_recipe_with_replacer(&root, &recipe, &move |temp, _| {
            assert!(temp.exists());
            assert_eq!(std::fs::read_to_string(temp).unwrap(), expected_temp);
            *captured_temp.borrow_mut() = Some(temp.to_path_buf());
            Err(std::io::Error::other("simulated rename failure"))
        });

        assert!(!result.saved);
        assert_eq!(std::fs::read_to_string(path).unwrap(), before);
        assert!(!observed_temp.borrow().as_ref().unwrap().exists());
    }

    #[test]
    fn pipeline_recipe_save_attempts_use_distinct_owned_temp_paths() {
        let root = temp_dir("pipeline-recipe-distinct-temp-paths");
        write_recipe_fixture(&root);
        let recipe = valid_pipeline_recipe("safe-flow");
        let temp_paths = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
        let captured_paths = temp_paths.clone();
        let replacer = move |temp: &Path, _: &Path| {
            assert!(temp.exists());
            captured_paths.borrow_mut().push(temp.to_path_buf());
            Err(std::io::Error::other("stop before mutation"))
        };

        for _ in 0..2 {
            let result = save_pipeline_recipe_with_replacer(&root, &recipe, &replacer);
            assert!(!result.saved);
        }

        let temp_paths = temp_paths.borrow();
        assert_eq!(temp_paths.len(), 2);
        assert_ne!(temp_paths[0], temp_paths[1]);
        assert_eq!(temp_paths[0].parent(), temp_paths[1].parent());
        assert!(temp_paths.iter().all(|path| !path.exists()));
    }

    #[test]
    fn pipeline_recipe_rejects_pipeline_root_outside_canonical_project() {
        let project = Path::new("C:/workspace/project");
        let inside = Path::new("C:/workspace/project/.github/pipelines");
        let outside = Path::new("C:/outside/pipelines");

        assert!(ensure_canonical_child(project, inside, "pipeline root").is_ok());
        assert!(ensure_canonical_child(project, outside, "pipeline root").is_err());
    }

    #[test]
    fn pipeline_recipe_requires_exact_canonical_name_ownership() {
        let root = Path::new("C:/workspace/project/.github/pipelines");
        let expected = root.join("safe-flow");
        let owned = root.join("safe-flow");
        let sibling_alias = root.join("other-flow");

        assert!(ensure_exact_canonical_owner(&expected, &owned, "pipeline directory").is_ok());
        assert!(
            ensure_exact_canonical_owner(&expected, &sibling_alias, "pipeline directory").is_err()
        );
    }

    #[test]
    fn pipeline_recipe_rejects_sibling_recipe_directory_alias() {
        let root = temp_dir("pipeline-recipe-sibling-alias");
        write_recipe_fixture(&root);
        let other_recipe = valid_pipeline_recipe("other-flow");
        assert!(save_pipeline_recipe(&root, &other_recipe).saved);
        let pipeline_root = root.join(".github/pipelines");
        let other_directory = pipeline_root.join("other-flow");
        let safe_directory = pipeline_root.join("safe-flow");
        let other_path = other_directory.join("pipeline.yaml");
        let before = std::fs::read_to_string(&other_path).unwrap();

        #[cfg(unix)]
        std::os::unix::fs::symlink(&other_directory, &safe_directory).unwrap();
        #[cfg(windows)]
        if std::os::windows::fs::symlink_dir(&other_directory, &safe_directory).is_err() {
            return;
        }

        let mut safe_recipe = valid_pipeline_recipe("safe-flow");
        safe_recipe.description = "must not overwrite sibling".into();
        let result = save_pipeline_recipe(&root, &safe_recipe);

        assert!(!result.saved);
        assert!(read_pipeline_recipe(&root, "safe-flow").is_err());
        assert_eq!(std::fs::read_to_string(other_path).unwrap(), before);
    }

    #[test]
    fn pipeline_recipe_rejects_unsafe_name_traversal_and_symlink_escape() {
        let root = temp_dir("pipeline-recipe-path-safety");
        write_recipe_fixture(&root);
        for name in ["../escape", "Upper", "two/slashes", "."] {
            let mut recipe = valid_pipeline_recipe(name);
            assert!(!save_pipeline_recipe(&root, &recipe).saved, "{name}");
            recipe.name = "safe-flow".into();
        }
        assert!(read_pipeline_recipe(&root, "../escape").is_err());

        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let outside = temp_dir("pipeline-recipe-outside");
            let pipeline_root = root.join(".github/pipelines");
            std::fs::create_dir_all(&pipeline_root).unwrap();
            symlink(&outside, pipeline_root.join("safe-flow")).unwrap();
            assert!(!save_pipeline_recipe(&root, &valid_pipeline_recipe("safe-flow")).saved);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::symlink_dir;
            let outside = temp_dir("pipeline-recipe-outside");
            let pipeline_root = root.join(".github/pipelines");
            std::fs::create_dir_all(&pipeline_root).unwrap();
            if symlink_dir(&outside, pipeline_root.join("safe-flow")).is_ok() {
                assert!(!save_pipeline_recipe(&root, &valid_pipeline_recipe("safe-flow")).saved);
            }
        }
    }

    #[test]
    fn pipeline_recipe_rejects_ancestor_link_outside_project() {
        let root = temp_dir("pipeline-recipe-ancestor-link");
        let outside = temp_dir("pipeline-recipe-ancestor-outside");
        std::fs::create_dir_all(outside.join("pipelines")).unwrap();

        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, root.join(".github")).unwrap();
        #[cfg(windows)]
        if std::os::windows::fs::symlink_dir(&outside, root.join(".github")).is_err() {
            return;
        }

        let result = save_pipeline_recipe(&root, &valid_pipeline_recipe("safe-flow"));
        assert!(!result.saved);
        assert!(!outside.join("pipelines/safe-flow/pipeline.yaml").exists());
    }

    #[test]
    fn pipeline_recipe_contract_errors_are_located_and_fail_closed() {
        let cases = [
            ("missing frontmatter", "# prompt only\n"),
            (
                "malformed tools",
                "---\nname: Planner\nmaxTurns: 4\nmaxOutputTokens: 4096\ntools: Read\n---\n# prompt\n",
            ),
            (
                "malformed maxTurns",
                "---\nname: Planner\nmaxTurns: 0\nmaxOutputTokens: 4096\ntools: [Read]\n---\n# prompt\n",
            ),
            (
                "malformed maxOutputTokens",
                "---\nname: Planner\nmaxTurns: 4\nmaxOutputTokens: nope\ntools: [Read]\n---\n# prompt\n",
            ),
            (
                "missing prompt",
                "---\nname: Planner\nmaxTurns: 4\nmaxOutputTokens: 4096\ntools: [Read]\n---\n",
            ),
        ];

        for (label, frontmatter) in cases {
            let root = temp_dir(&format!("pipeline-recipe-contract-{label}"));
            write_recipe_fixture(&root);
            std::fs::write(
                root.join(".github/agents/workers/planner.agent.md"),
                frontmatter,
            )
            .unwrap();
            let recipe = valid_pipeline_recipe("contract-flow");
            let directory = root.join(".github/pipelines/contract-flow");
            std::fs::create_dir_all(&directory).unwrap();
            std::fs::write(
                directory.join("pipeline.yaml"),
                render_pipeline_recipe(&recipe, "", &serde_yaml::Mapping::new()).unwrap(),
            )
            .unwrap();

            let findings = validate_pipeline_recipe(&root, &recipe);

            assert!(
                findings
                    .iter()
                    .any(|finding| { finding.step == "stages[0]" && finding.field == "contract" }),
                "{label}: {findings:?}"
            );
            let loaded = read_pipeline_recipe(&root, "contract-flow").unwrap();
            assert!(loaded
                .findings
                .iter()
                .any(|finding| finding.field == "contract"));
            assert!(!read_studio_pipeline_catalog(&root)
                .iter()
                .any(|entry| entry.name == "contract-flow"));
            assert!(!save_pipeline_recipe(&root, &recipe).saved, "{label}");
        }
    }

    #[test]
    fn pipeline_recipe_spawn_firewall_frontmatter_overrides_python_fallback() {
        for (label, declared, spawn, expected_error) in [
            ("narrower", Some("[explorer]"), "investigator", true),
            (
                "wider",
                Some("[explorer, investigator, reviewer-aux]"),
                "reviewer-aux",
                false,
            ),
            ("fallback", None, "investigator", false),
        ] {
            let root = temp_dir(&format!("pipeline-recipe-firewall-{label}"));
            write_recipe_fixture(&root);
            let agents = root.join(".github/agents/workers");
            for role in ["investigator", "reviewer-aux"] {
                std::fs::write(
                    agents.join(format!("{role}.agent.md")),
                    format!(
                        "---\nname: {role}\nmaxTurns: 4\nmaxOutputTokens: 4096\ntools: [Read]\n---\n# {role}\n"
                    ),
                )
                .unwrap();
            }
            let spawn_allowlist = declared
                .map(|value| format!("spawn_allowlist: {value}\n"))
                .unwrap_or_default();
            std::fs::write(
                agents.join("coder.agent.md"),
                format!(
                    "---\nname: coder\nmaxTurns: 8\nmaxOutputTokens: 4096\ntools: [Read, Write]\n{spawn_allowlist}---\n# coder\n"
                ),
            )
            .unwrap();
            let scripts = root.join("scripts");
            std::fs::create_dir_all(&scripts).unwrap();
            std::fs::write(
                scripts.join("policy_engine.py"),
                "MAIN_SUBAGENT_ALLOWLIST: dict[str, list[str]] = {\n    \"coder\": [\"explorer\", \"investigator\"],\n    \"reviewer\": [],\n}\n",
            )
            .unwrap();
            let mut recipe = valid_pipeline_recipe("firewall-flow");
            recipe.stages[0] = PipelineStageRecipe {
                preset: "build".into(),
                agent: String::new(),
                stage: String::new(),
                spawns: vec![spawn.into()],
            };

            let findings = validate_pipeline_recipe(&root, &recipe);
            let has_spawn_error = findings
                .iter()
                .any(|finding| finding.step == "stages[0]" && finding.field == "spawns");

            assert_eq!(has_spawn_error, expected_error, "{label}: {findings:?}");
        }
    }

    #[test]
    fn pipeline_recipe_validation_reports_every_fail_closed_case_with_location() {
        let root = temp_dir("pipeline-recipe-validation");
        write_recipe_fixture(&root);
        let mut recipe = valid_pipeline_recipe("safe-flow");
        recipe.stages = vec![
            PipelineStageRecipe {
                preset: "missing".into(),
                agent: String::new(),
                stage: String::new(),
                spawns: vec![],
            },
            PipelineStageRecipe {
                preset: String::new(),
                agent: "ghost".into(),
                stage: "ghost-stage".into(),
                spawns: vec![],
            },
            PipelineStageRecipe {
                preset: "plan".into(),
                agent: "coder".into(),
                stage: "same".into(),
                spawns: vec!["ghost-spawn".into()],
            },
            PipelineStageRecipe {
                preset: "build".into(),
                agent: "coder".into(),
                stage: "same".into(),
                spawns: vec![],
            },
            PipelineStageRecipe {
                preset: "plan".into(),
                agent: String::new(),
                stage: String::new(),
                spawns: vec![],
            },
        ];

        let findings = validate_pipeline_recipe(&root, &recipe);

        for field in ["preset", "agent", "stage", "spawns", "evaluator"] {
            assert!(
                findings
                    .iter()
                    .any(|f| f.field == field && !f.step.is_empty()),
                "missing located finding for {field}: {findings:?}"
            );
        }
    }

    #[test]
    fn pipeline_recipe_malformed_spawns_becomes_a_located_finding() {
        let root = temp_dir("pipeline-recipe-malformed-spawns");
        write_recipe_fixture(&root);
        let dir = root.join(".github/pipelines/bad-spawns");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("pipeline.yaml"),
            "name: bad-spawns\nstages:\n  - preset: plan\n    spawns: explorer\n  - preset: check\n",
        )
        .unwrap();

        let recipe = read_pipeline_recipe(&root, "bad-spawns").unwrap();
        let findings = validate_pipeline_recipe(&root, &recipe);

        assert!(findings
            .iter()
            .any(|f| f.step == "stages[0]" && f.field == "spawns"));
    }

    #[test]
    fn pipeline_recipe_successful_save_refreshes_catalog_at_canonical_path() {
        let root = temp_dir("pipeline-recipe-save");
        write_recipe_fixture(&root);
        let recipe = valid_pipeline_recipe("custom-flow");

        let result = save_pipeline_recipe(&root, &recipe);

        assert!(result.saved);
        assert!(result.catalog_refreshed);
        assert_eq!(
            PathBuf::from(result.path).canonicalize().unwrap(),
            root.join(".github/pipelines/custom-flow/pipeline.yaml")
                .canonicalize()
                .unwrap()
        );
        assert!(read_studio_pipeline_catalog(&root)
            .iter()
            .any(|entry| entry.name == "custom-flow"));
    }

    #[test]
    fn launch_spec_uses_detected_agent_cli_and_forwards_musubi_env() {
        let root = PathBuf::from("/proj");
        let cli = PathBuf::from("/scripts/agent.exe");
        let mut env = std::collections::HashMap::new();
        env.insert("MUSUBI_ROOT".to_string(), "/musubi-core".to_string());
        env.insert("MUSUBI_DB".to_string(), "/data/audit.db".to_string());
        env.insert("MUSUBI_STATE_DB".to_string(), "/data/musubi.db".to_string());
        env.insert(
            "MUSUBI_FOLDER_GRANTS_JSON".to_string(),
            "[{\"grantId\":\"musubi\"}]".to_string(),
        );
        env.insert(
            "MUSUBI_LLM_CONFIG".to_string(),
            "/proj/.musubi/llm.json".to_string(),
        );
        env.insert(
            "MUSUBI_MCP_CONFIG".to_string(),
            "/proj/.musubi/mcp.json".to_string(),
        );
        env.insert("ANTHROPIC_API_KEY".to_string(), "sk-…".to_string());

        let spec = build_agent_launch_spec(
            "task",
            "",
            "",
            Some(&cli),
            &root,
            &env,
            AgentLaunchScope::default(),
        )
        .unwrap();

        assert_eq!(spec.program, cli);
        let mut forwarded = spec.env.clone();
        forwarded.sort();
        assert_eq!(
            forwarded,
            vec![
                ("MUSUBI_DB".to_string(), "/data/audit.db".to_string()),
                (
                    "MUSUBI_FOLDER_GRANTS_JSON".to_string(),
                    "[{\"grantId\":\"musubi\"}]".to_string()
                ),
                (
                    "MUSUBI_LLM_CONFIG".to_string(),
                    "/proj/.musubi/llm.json".to_string()
                ),
                (
                    "MUSUBI_MCP_CONFIG".to_string(),
                    "/proj/.musubi/mcp.json".to_string()
                ),
                ("MUSUBI_ROOT".to_string(), "/musubi-core".to_string()),
                ("MUSUBI_STATE_DB".to_string(), "/data/musubi.db".to_string()),
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
            AgentLaunchScope::default(),
        )
        .unwrap_err();
        assert!(err.contains("empty"));
    }

    fn create_paused_pipeline_fixture() -> (Connection, Connection) {
        let audit = Connection::open_in_memory().unwrap();
        let state = Connection::open_in_memory().unwrap();
        init_schema(&audit).unwrap();
        init_schema(&state).unwrap();
        state
            .execute_batch(
                "CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    request TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    paused_at_stage TEXT,
                    paused_at_chunk TEXT,
                    pause_reason TEXT,
                    auto_approve_remaining INTEGER NOT NULL DEFAULT 0,
                    pending_action TEXT,
                    pending_user_hint TEXT,
                    pending_extra_budget INTEGER NOT NULL DEFAULT 0
                );
                ",
            )
            .unwrap();
        audit
            .execute(
                "INSERT INTO subagent_audit
                 (ts,event,handle_id,parent_session_id,parent_agent_name,role,brief,
                  allowed_tools,max_turns,wall_clock_timeout_s)
                 VALUES (10,'spawned','pipeline-session','outer-root','agent',
                         'pipeline:feature-dev','ship it','[]',2,0)",
                [],
            )
            .unwrap();
        state
            .execute(
                "INSERT INTO sessions
                 (session_id,request,status,created_at,updated_at,paused_at_stage,
                  paused_at_chunk,pause_reason)
                 VALUES ('pipeline-session','ship it','active','1','2','coder','T2','stage_review')",
                [],
            )
            .unwrap();
        state
            .execute(
                "INSERT INTO pipeline_runs
                 (session_id,pipeline_name,chat_id,started_at,request_id,profile,task)
                 VALUES ('pipeline-session','feature-dev','gui-orchestrator-project-chat',
                         10,'request-original','openai.work','ship it')",
                [],
            )
            .unwrap();
        (audit, state)
    }

    #[test]
    fn paused_pipeline_fields_project_from_state_db() {
        let (audit, state) = create_paused_pipeline_fixture();

        let snapshot = load_state_with_pipeline_runs(&audit, Some(&state)).unwrap();
        let run = &snapshot.pipeline_runs[0];

        assert_eq!(run.paused_at_stage.as_deref(), Some("coder"));
        assert_eq!(run.paused_at_chunk.as_deref(), Some("T2"));
        assert_eq!(run.pause_reason.as_deref(), Some("stage_review"));
        assert_eq!(run.pending_action, None);
        assert_eq!(run.request_id, "request-original");
        assert_eq!(run.profile, "openai.work");
        assert_eq!(run.task, "ship it");
    }

    #[test]
    fn resume_pipeline_decision_validates_matrix_and_is_single_use() {
        let (_audit, mut state) = create_paused_pipeline_fixture();

        let decision = resume_pipeline_session(
            &mut state,
            "pipeline-session",
            "retry",
            Some("keep the API stable"),
            0,
            20.0,
        )
        .unwrap();

        assert!(decision.launch);
        assert_eq!(decision.chat_id, "gui-orchestrator-project-chat");
        assert_eq!(decision.request_id, "request-original");
        assert_eq!(decision.profile, "openai.work");
        assert_eq!(decision.task, "ship it");
        assert_eq!(
            state
                .query_row(
                    "SELECT pending_action FROM sessions WHERE session_id='pipeline-session'",
                    [],
                    |row| row.get::<_, Option<String>>(0),
                )
                .unwrap()
                .as_deref(),
            Some("retry")
        );
        assert!(
            resume_pipeline_session(&mut state, "pipeline-session", "approve", None, 0, 21.0,)
                .is_err()
        );
    }

    #[test]
    fn resume_pipeline_decision_rejects_reason_mismatch_without_mutation() {
        let (_audit, mut state) = create_paused_pipeline_fixture();

        let error = resume_pipeline_session(&mut state, "pipeline-session", "grant", None, 3, 20.0)
            .unwrap_err();

        assert!(error.contains("does not apply"));
        let pause: (Option<String>, Option<String>) = state
            .query_row(
                "SELECT paused_at_stage,pending_action FROM sessions
                 WHERE session_id='pipeline-session'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(pause, (Some("coder".into()), None));
    }

    #[test]
    fn resume_pipeline_decision_accepts_only_the_reason_action_matrix() {
        for action in ["approve", "retry", "abort", "auto_approve_rest"] {
            let (_audit, mut state) = create_paused_pipeline_fixture();
            let decision =
                resume_pipeline_session(&mut state, "pipeline-session", action, None, 0, 20.0)
                    .unwrap();
            assert_eq!(decision.launch, action != "abort");
        }
        for action in ["grant", "force", "abort"] {
            let (_audit, mut state) = create_paused_pipeline_fixture();
            state
                .execute(
                    "UPDATE sessions SET pause_reason='budget_exhausted'
                     WHERE session_id='pipeline-session'",
                    [],
                )
                .unwrap();
            let extra = if action == "grant" { 3 } else { 0 };
            let decision =
                resume_pipeline_session(&mut state, "pipeline-session", action, None, extra, 20.0)
                    .unwrap();
            assert_eq!(decision.launch, action != "abort");
        }
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

/// Optional identity that scopes one agent launch to a chat and/or pipeline.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct AgentLaunchScope<'a> {
    pub chat_id: Option<&'a str>,
    pub pipeline_name: Option<&'a str>,
    pub request_id: Option<&'a str>,
    pub max_tokens: Option<i64>,
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
    scope: AgentLaunchScope<'_>,
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
    if let Some(chat_id) = scope.chat_id.map(str::trim).filter(|s| !s.is_empty()) {
        args.push("--chat-id".into());
        args.push(chat_id.to_string());
    }
    if let Some(pipeline_name) = scope.pipeline_name.map(str::trim).filter(|s| !s.is_empty()) {
        if !valid_pipeline_name(pipeline_name) {
            return Err(format!("invalid pipeline name: {pipeline_name:?}"));
        }
        args.push("--pipeline".into());
        args.push(pipeline_name.to_string());
    }
    if let Some(max_tokens) = scope.max_tokens {
        if max_tokens < 0 {
            return Err("token budget must be zero or a positive integer".into());
        }
        args.push("--max-tokens".into());
        args.push(max_tokens.to_string());
    }
    args.push("--tool-surface".into());
    args.push("agent".into());

    Ok(AgentLaunchSpec {
        program,
        args,
        cwd: project_root.to_path_buf(),
        env: {
            let mut forwarded = forwarded_spec_env(env);
            if let Some(request_id) = scope.request_id.map(str::trim).filter(|s| !s.is_empty()) {
                forwarded.push(("MUSUBI_REQUEST_ID".into(), request_id.to_string()));
                forwarded.push(("MUSUBI_RUNTIME_LOG_PROTOCOL".into(), "1".into()));
            }
            forwarded
        },
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
        "MUSUBI_STATE_DB",
        "MUSUBI_AUDIT_DB",
        "MUSUBI_FOLDER_GRANTS_JSON",
        "MUSUBI_LLM_CONFIG",
        "MUSUBI_MCP_CONFIG",
        "MUSUBI_CHAT_ID",
        "MUSUBI_PIPELINE_PROFILE",
        "MUSUBI_PIPELINE_TASK",
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
