# Console Now-First Orchestrator — Design Record

**Goal:** Make the Orchestrator answer "what is the agent doing right now?" at a
glance, and give the Console a token layer so its three parallel styling systems
stop drifting.

**Architecture:** Presentation only. `viewModel.js` gains derivations (`nowRun`,
`railGroups`, `trustCounters`) and the React views render them; no substrate,
audit, policy, or LMRouter path is touched, and no new Tauri command is added.

**Tech Stack:** React 18, plain CSS with `:root` custom properties, Node test
runner.

---

## Why

The screen used to answer that question spent ~206 px of vertical chrome — trust
strip (46) + run config (~56) + runtime status (44) + evidence header (~60) —
before any evidence, and the answer itself was an 11 px pill sitting between
"feature-dev mode" and "37 log rows". Everything else on screen was history.

Three findings drove everything else:

1. **Orange meant four things** — brand, navigation-active, selection, *and*
   running. In a typical screenshot the running session, the selected session,
   and three finished requests were all orange, so the one colour that should
   mean "look here" meant "here is a thing".
2. **Zeros were typeset like data.** A sparse run rendered
   `0 tools · 0 tokens · 0 log rows` in 74 px rows, so the eye could not skip
   the noughts.
3. **No design system.** `index.css` was 32 KB of BEM-ish classes with *zero*
   custom properties, alongside React inline style objects and CSS-strings-in-JS
   parsed at runtime. Nothing forced the three to agree, and they drifted into
   ~20 greys across two hue families, 17 font sizes (several at half-pixels),
   and 11 radii. Several rules were defined twice — `.workspace-kicker`,
   `.runtime-node__identity strong`, `.runtime-logs__controls` — once mid-file
   and again in the appended "Request runtime history" block.

## What changed

### Token layer (`index.css`)

A `:root` block is now the only place a raw value lives: 3 surfaces + 1 well,
3 text greys (one blue-tinted family), 5 semantic colours, 3 role hues, 2 line
weights, 6 type sizes (10/11/12/13/16/22 — no half-pixels, 10 px floor), 3 radii
(6/10/999), and a 4 px spacing base. The duplicated override block was folded
into the canonical rules rather than left to win by source order.

`--text-3` was raised to `#7d8b9e`: the old faint grey was ~2.6:1 on a node
surface at 9–10 px, under the 4.5:1 floor, and it was used for counts meant to
be scanned. A `:focus-visible` rule now exists, which it did not before.

`.ui-button` — previously the only reusable button in the app and reachable only
from Pipeline Studio — was promoted to the shared layer and switched to sans,
because a button label is language.

### Orange means one thing

Live attention, and nothing else. Selection became a neutral raise plus a blue
bar (`--select`); finished rows are grey with a small green dot; amber is
reserved for escalated. The profile picker in the trust strip lost its orange
border — a picker is a thing, not an alert.

### The Now banner

The largest element on the screen names the actor, the act, the elapsed time,
and the way out. `actPhrase()` turns the last log line into language ("Planner
is reading your workspace") while the exact call stays in mono beneath it. The
banner ticks its own elapsed clock on a 1 s interval because the data source is
event-driven and carries no tick. When nothing is running it collapses to a
quiet one-line idle state in the same slot, so the workspace does not reflow on
start.

### Rows earn their height

Finished requests collapse to one line; absent values render as `—` at 40 %
opacity instead of a typeset `0`. The running request expands in place with its
last three log lines, so the answer is visible without a click and without
leaving the timeline. The `.request-graph{max-width:980px}` cap was removed —
it wasted ~110 px of gutter per side on a wide display.

### Chrome moved, not deleted

Execution mode and the pipeline recipe are start-of-run decisions, so they moved
into the composer. The runtime status strip is gone: its counts already exist in
the banner and on the timeline rows that carry them.

### Stop is where you are looking

The composer's send button used to swap glyph and colour in place while busy, so
the only destructive control in the app sat exactly where the safe one had been.
Send is now always send, disabled while a run owns the driver with a title that
says why; stopping is a labelled **Stop run** button in the banner.

**Deviation from the design brief:** the brief also wanted the input usable for
steering mid-run. That needs a queue the substrate does not have — the driver
holds the runtime and `sendChat` is gated on `driverStatus.running` — so it was
not invented here. The misclick hazard is fixed; mid-run steering is not.

### Trust strip proves rather than claims

Four hard-coded invariant strings became four counters that move:
`policy 14 allow / 0 deny`, `audit N rows appended`,
`firewall N evaluator isolated`, `substrate 0 LM calls`. Unchanging green
teaches the eye to ignore green; a deny is now visible the moment it lands.

### Two factual bugs

- **Policy** showed a hard-coded `4` for "policy roles defined" while its two
  neighbours were live — a credibility leak in the view that sells credibility.
  It reads `vals.policyRoles.length`.
- **Models** labelled its config sample `.musubi/llm.toml` and typeset it as
  TOML while the product reads `.musubi/llm.json`, with invented contents
  (`gw.corp.internal`, `gpt-4o`) and only `default =` live. It is now the
  documented JSON schema, labelled as a schema, beside the operator's real path.
  The file itself is deliberately **not** rendered: a profile may carry an
  inline `api_key`, and the Console must not put a secret on screen. The stale
  `--vendor → --profile` precedence subtitle was corrected — `--profile` is the
  only endpoint switch. The same `llm.toml` string was fixed in `data.js`.

Also fixed in passing: `1 workers` had no plural handling anywhere, and
`sessionSubtitle` carried a double-encoded `·`.

## Deliberately unchanged

The three-pane shape, the activity bar, the knot mark, the dark ledger register,
chat bubble geometry, and the request→agent nesting. The diagnosis was not
"wrong structure" — it was "right structure, wrong emphasis".

## Not done

- `Audit.jsx` is still a `div` grid rather than a table: no sort, no sticky
  header, no row selection, no export, and its filter labels are lowercase while
  every other filter set is Title Case.
- No cross-view deep links (an escalated agent cannot jump to its policy
  verdicts or audit rows), and no session scope outside the Orchestrator.
- Drill-in still swaps the centre pane rather than opening beside the timeline.
- No keyboard shortcuts (`⌘K` sessions, `⌘.` stop, `1–7` views).
- Pipeline Studio, Skills, and Settings keep their own headers and card styles;
  the token layer is available to them but they have not been migrated.
