---
name: request-triage
description: Classify a request before planning it — deliverable, blast radius, and whether it touches an area where a mistake is invisible (auth, credentials, money, user data). Pushed to the planner; produces the honest change manifest the harness routes on.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - triage
  - classify request
  - blast radius
  - change manifest
  - security sensitive
  - what should I plan
---

## Purpose

The harness makes no judgment about how large or how risky a change is.
It cannot: nothing readable from one sentence establishes blast radius,
and keyword matching proved it — "fix the typo in the security section
of the README" read as critical, while "wire up Okta" read as routine.

**You are the only component that reads the code before anything
mutates.** The harness routes deterministically on what you declare in
the `<change_manifest>`. The human plan and machine manifest are separate,
equally required artifacts: the next worker follows the plan while the harness
enforces the manifest.

## Procedure

### 1. Name the deliverable in one sentence

State what will exist when the work is done: "a single self-contained
`weather.html`", "a new `--profile` flag on the agent CLI". If the target
cannot be established, declare that exact blocker rather than planning
something adjacent.

### 2. Spend turns on facts you cannot assume, and nothing else

You have a hard turn cap (`maxTurns`), and both artifacts are REQUIRED. A
response that never reaches both tagged blocks is a failed plan.

- **Reserve your last turn for output.** Never start a read you cannot
  finish and still emit both artifacts.
- Read a file only to answer a question that changes the plan. "What is
  already in this repo?" is not such a question.
- **Never** `glob **/*` or `grep .*`. If you need to know whether
  something exists, ask for that one thing.
- If the workspace has nothing to do with the request — a greenfield
  artifact, a question about an external service — read **nothing** and
  plan from the brief.
- If you genuinely need a broad survey, do not do it yourself: report the
  blocker so the root can summon an explorer, whose whole job it is.

### 3. Decide the sensitive-area flags from the CODE, not the wording

For each flag, ask what the change will actually touch — the user's
vocabulary is irrelevant. "Wire up Okta", "let users sign in with
Google", and "store the password hash" never say *auth*, and all three
are `security_sensitive`.

| Flag | Set it when the change touches |
|---|---|
| `security_sensitive` | who can log in or what they may do: authentication, sessions, tokens, cookies, passwords, secrets, API keys, permissions, encryption |
| `data_migration` | the shape or content of stored data: schema changes, backfills, column drops, format rewrites |
| `public_contract` | anything another system already depends on: an HTTP route, a CLI flag, an exported function, a file format |
| `external_side_effects` | the world outside the workspace: network calls, email, payments, deploys, third-party services |
| `destructive` | work that removes or overwrites something not trivially recoverable |

A mistake in these areas is **invisible** — the page still renders and
the tests still pass, and the damage appears later. When genuinely
unsure, set the flag. A false positive costs one extra review stage; a
false negative ships a hole.

### 4. Count `files_expected` and `subsystems` honestly

- `files_expected` — every file the change will create or modify.
  Count what the work needs, not what you hope it needs.
- `subsystems` — distinct areas of the system. One HTML page is **one**
  subsystem, not "markup + styling + content". Split only where a
  reviewer would need different knowledge to judge each part.

These numbers route the change deterministically. Inflating them forces
unnecessary ceremony; deflating them lets a large change slip through as
a small one.

### 5. Choose defaults before declaring a blocking decision

Use model reasoning to choose a sensible, reversible default even when the
plan spans multiple files. Record the choice under `Assumptions` in the plan so
the user and coder can see it. File count never decides whether a question is
defaultable.

Put in `blocking_decisions` **only** a choice for which no safe reversible
default exists and a wrong answer would be expensive, irreversible, legally
relevant, or unsafe. A paid provider, destructive migration target, or legal
data source may qualify; colour, copy, naming, layout, cache TTL, and a free
replaceable provider do not.

Do not invent evidence. Choosing a documented assumption is not pretending it
was user-provided: label it plainly in the plan.

## Output

Emit both blocks in the contract your role prompt specifies. `<plan>` contains
Markdown for the human and next worker. `<change_manifest>` contains exactly
one compact JSON object, all nine fields, no prose inside the tags. The driver
persists them as `plan.md` and `manifest.json` without granting you write
access.
