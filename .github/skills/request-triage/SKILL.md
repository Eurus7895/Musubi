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
the `<change_manifest>`. An honest manifest is the deliverable; the prose
plan is secondary.

## Procedure

### 1. Name the deliverable in one sentence

State what will exist when the work is done: "a single self-contained
`weather.html`", "a new `--profile` flag on the agent CLI". If you cannot
say it in one sentence, the request is not yet plannable — say so in
`unknowns` rather than planning something adjacent.

### 2. Spend turns on facts you cannot assume, and nothing else

You have a hard turn cap (`maxTurns`), and the manifest is REQUIRED. A
plan that never reaches the manifest is a failed plan.

- **Reserve your last turn for output.** Never start a read you cannot
  finish and still emit the manifest.
- Read a file only to answer a question that changes the plan. "What is
  already in this repo?" is not such a question.
- **Never** `glob **/*` or `grep .*`. If you need to know whether
  something exists, ask for that one thing.
- If the workspace has nothing to do with the request — a greenfield
  artifact, a question about an external service — read **nothing** and
  plan from the brief.
- If you genuinely need a broad survey, do not do it yourself: say so in
  `unknowns` so the root can summon an explorer, whose whole job it is.

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

### 5. Separate what you may default from what only the user can decide

Put in `unknowns` **only** decisions that are expensive or irreversible
to get wrong, or that you cannot infer at all: which provider, which
data source, which existing system to integrate with, anything with a
cost or a legal consequence.

Do **not** put in `unknowns` anything the next worker can pick a
reasonable default for and the user can redirect in one turn: colour
palette, typography, spacing, copy, file naming, layout breakpoints.
Listing those halts the whole conversation to ask questions nobody
needed asked.

Never invent a value to make the manifest look complete.

## Output

Emit the plan in the contract your role prompt specifies. The
`<change_manifest>` block is mandatory on a `done` plan: exactly one
compact JSON object, all nine fields, no prose inside the tags.
