---
name: summarizer
description: >
  Procedure for the Summarizer sub-agent. Pushed by the harness through
  validation/subagent_context.SUBAGENT_ROLE_SKILLS["summarizer"].
harness-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

# summarizer — procedure

The Summarizer compresses an older conversation window into a bounded
markdown summary. One LM round-trip, no tools, no follow-up spawns.

## Procedure

1. Parse the brief into turns by splitting on lines that start with
   `[user]`, `[assistant]`, or `[tool]`. Discard malformed lines.
2. Walk the turns and accumulate four buckets:
   - **Decisions**: anything an `[assistant]` turn concluded that
     the user accepted (no objection in the next `[user]` turn).
   - **Files touched**: any `path/to/file.py` patterns referenced by
     either the user or the assistant; record what changed if the
     turn says so.
   - **Pending**: sub-agent handles spawned but not yet awaited;
     `TODO`-marked tasks the assistant flagged.
   - **Open questions**: user questions the assistant did not
     resolve, or assistant questions the user did not answer.
3. Emit the markdown with the four section headings exactly as
   specified in the agent's Output Contract. Use bullets, not
   paragraphs. Skip empty sections entirely.
4. Stop. Do not add a "summary of the summary" section, prose
   preamble, or closing remarks. The agent splices your
   output verbatim.

## Bullet style

- Start each bullet with a verb in the past tense for Decisions
  and Files touched (`"adopted X"`, `"renamed Y to Z"`).
- Start each bullet with a noun phrase for Pending and Open
  questions (`"explorer handle 4f2a awaiting follow-up"`,
  `"user has not chosen between option A and option B"`).
- One line per bullet. If a thought needs two lines, break it into
  two bullets.

## Identifier preservation

Always quote file paths in backticks and verbatim, never abbreviate
("`copilot-harness/server.py`", not "the server file"). Do the same
for handle ids, commit SHAs, function names, and skill ids. Future
turns find prior work by exact-string search.

## Length

Cap output at ~1500 tokens. If the input window is too large to fit
that ceiling losslessly, drop content from the *Pending* and *Files
touched* sections first — they regenerate from new tool calls.
Decisions and Open questions are the highest-signal; preserve them
even at the cost of brevity elsewhere.

## What you do NOT do

- No code blocks longer than ~5 lines. Quote file paths and function
  names instead.
- No invented decisions. If the input has nothing concrete in a
  bucket, omit the section.
- No recommendations or planning. You compress; you do not advise.
- No secrets. Strip API keys, tokens, private keys; the harness's
  secrets scanner is a backstop, not your defense.
