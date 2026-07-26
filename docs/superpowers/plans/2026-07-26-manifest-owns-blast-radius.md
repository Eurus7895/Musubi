# The manifest owns blast radius; the catalog tells the truth

## Context

A GUI run on `deepseek-v4-flash` ("create a website" → "it should show the
weather of city in vietnma") spent 10 cycles and 56,724 input tokens and
delivered no file. Investigating it surfaced defects across routing, the
planner contract, the recovery phase, and the agent catalog.

**Two lexical rules claimed to know how large a change was**, before anything
had read a line of code, and both were measurably wrong in both directions:

- `_CRITICAL_RISK_RE` (`change_assessment.py`) refused a request outright with
  zero model calls. It turned away "fix the typo in the security section of
  the README", "rename the payments variable in utils.py", and "add a login
  button to the landing page" — while "wire up Okta", "add SSO", "let users
  sign in with Google", "store user passwords in the users table", and "add a
  session cookie" all passed through untouched.
- `_LARGE_RISK_RE >= 2` (`scope.py`) counted distinct risky-sounding words in
  the sentence. Two synonyms for one idea escalated ("auth" + "login"); two
  typos in `auth.py` and `payment.py` scored as a large feature; "rewrite the
  entire user system" and "migrate all 40 services to the new runtime" scored
  zero. No document in the repository explains the threshold of 2.

The two also disagreed with each other in 11 places (`_LARGE_RISK_RE` was
written `payment` without `s?`, so the plural escaped the planner-first
guard entirely) and shared 5 blind spots.

**The agent catalog was not truthful.** `tools:` frontmatter is documentation
— nothing reads it — and it had drifted: planner, designer and reviewer each
declared `["Read","View"]` while `SUBAGENT_POLICIES` granted Grep and Glob as
well. That is why the planner in the traced run, "restricted" on paper, could
`glob **/*` across 403 files. `model:` was dead in every path, false at
runtime, and contrary to HI #1.

**The planner's contract was unreachable.** It requires a `<change_manifest>`,
has `maxTurns: 4`, holds Grep and Glob, and was told only "inspect only files
needed" — with nothing reserving a turn for output. The observed failure (four
turns of reading, no manifest) is what that design guarantees.

**Recovery inverted its own purpose.** `root_decision_tools` returned the whole
catalog during recovery, so the root investigated on its own (a grep across
392 files, two reads of one file, a retrieve), spent both analysis cycles, and
halted via `_recovery_incomplete` without ever spawning a replacement.

**Vendor markup reached the user as an answer.** On cycle exhaustion the loop
makes one final call with no tools, assuming the model will answer in words.
DeepSeek emitted `<｜｜DSML｜｜tool_calls>…` as prose; the harness stored it as
the planner's plan, showed it to the user, wrote it to the audit DB, and would
have fed it to `parse_change_manifest`.

## Goal

One component decides blast radius, and it is the one that read the code.
Everything the substrate keeps must be deterministic, honest, and verifiable.

## Tech stack

Python 3.11, existing modules. One new skill. No new dependency.

## Implementation steps

- [x] **Step 1: delete the lexical size judgments.** Remove
  `_CRITICAL_RISK_RE` from `assess_request` (it now never returns
  `plan_design_workflow`), the `>= 2` branch, and `_mentions_large_workflow`.
  "Large" is decided only by `assess_manifest`.

- [x] **Step 2: keep one narrow guard, and say what it does.**
  `_LARGE_RISK_RE` → `_NO_SHORTCUT_RE`. It makes no size claim; its only
  effect is withholding the `single_coder` shortcut so a read-only planner
  reads before mutation. Vocabulary widened to what the old list missed —
  plurals, SSO, OIDC, Okta, SAML, JWT, tokens, sessions, cookies, passwords,
  credentials, secrets, API keys, refunds, subscriptions, checkout. A false
  positive costs one read-only planner run.

- [x] **Step 3: push a triage procedure to the planner.**
  `.github/skills/request-triage/SKILL.md`, wired through
  `SUBAGENT_ROLE_SKILLS` (pushed, HI #2) and `AGENT_SKILL_ALLOWLIST`. It sets
  the flags from what the change touches rather than how it was worded, gives
  each flag a concrete test, reserves the last turn for the manifest, forbids
  `glob **/*` and `grep .*`, routes genuine surveys to an explorer, and
  separates user decisions from worker defaults. `planner.agent.md` carries
  the same instructions.

- [x] **Step 4: route surveys to an explorer.** Medium-route guidance tells
  the root to summon an explorer for workspace facts and pass its findings
  into the planner's brief.

- [x] **Step 5: reject vendor tool-call markup.**
  `_looks_like_vendor_tool_markup` at both `_extract_text` call sites; the
  text is discarded and the worker reported as not having answered.

- [x] **Step 6: make the catalog truthful.** All four divergent agents declare
  what policy grants; `model:` removed from all fourteen. Two tests walk the
  catalog so neither can drift back.

- [x] **Step 7: narrow recovery to its decision.** Spawn plus skill selection
  during analysis; spawn alone once the analysis cycles are spent.

- [x] **Step 8: verify the declaration.** `GoalState.declared_files_expected`
  plus `manifest_overrun()` compares the declared radius against the files
  workers actually touched, surfaced in the decision block. With the lexical
  gates gone the manifest is the sole routing input, so it must be checked
  rather than trusted.

- [x] **Step 9: verify GREEN.**

```bash
python3 -m pytest musubi/tests/ -q   # 1598 passed, 1 skipped
```

Ten tests that pinned the deleted behaviour were rewritten to pin the new
contract rather than removed: `assess_request` never returns the large route;
every sensitive area loses the shortcut; a sensitive request runs instead of
being refused; the planner holds exactly one skill; recovery offers only its
decision.

## Deliberately not done

- **Narrowing the planner's tools.** Considered and rejected: the traced
  failure was caused by the missing turn budget, the absent stop condition,
  and degenerate queries — all now fixed — not by the presence of Glob.
  `Read`/`View` need a concrete path, so removing Glob would blind the planner
  to any file the brief does not name and force an explorer round trip for
  every "does this exist?". It would also change `feature-dev`, since
  `validate_policy_table` aborts the boot when `SUBAGENT_POLICIES` and
  `PIPELINE_POLICIES` diverge. Per the repo's own decision rule, enforcement
  waits for documented repeat failure of the instruction layer.

- **Per-stage `profile:` in pipeline.yaml.** The `model:` frontmatter was
  intended for pipelines but never wired: `pipeline.yaml` declares `agent:`
  and `skill:` only, and `run_pipeline` receives one vendor for every stage.
  Reviving the cost-tier intent vendor-neutrally (a stage naming a profile
  from `.musubi/llm.json`) is a separate change.
