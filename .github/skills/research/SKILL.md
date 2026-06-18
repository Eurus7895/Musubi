---
name: research
description: Gather and synthesise information from a codebase or external sources to answer a specific question. Use when the user asks "how does X work?", "where does Y come from?", "compare X vs Y", "what are best practices for Z" — anything where the deliverable is a written finding, not code.
---

## Purpose

Produce a written answer that lets the user act on it without redoing the
research. Synthesis is the deliverable — not a dump of raw sources.

## Procedure

1. **Restate the question.** One sentence at the top, in the user's words.
   If the question is ambiguous, surface the ambiguity before researching;
   don't research the wrong question.
2. **Scope the sources.** Decide before searching:
   - Codebase-only? Code + docs? Code + external?
   - Time budget — how many sources before you write?
   The answer is "enough to be confident, no more". Resist depth-first.
3. **Take notes per source.** For each file/page consulted:
   - One-line claim it supports.
   - File:line or URL (for the user to verify).
   Do not paste long excerpts into notes — your summary is what's
   load-bearing.
4. **Write the synthesis FIRST.** Draft the answer to the user's question
   in 2-5 sentences before structuring further. If you can't write it
   from your notes, you haven't researched enough.
5. **Layer evidence below the synthesis.** Headline answer at the top.
   Supporting findings below, each with the file/URL reference inline.
6. **Note what you couldn't find.** A "gaps" section is honest and helps
   the user decide whether to dig further or trust the conclusion.

## Output contract

A Markdown document with:

- **Heading**: restate the user's question.
- **Answer**: 2-5 sentences, the synthesis. Acts on its own without the
  reader scrolling.
- **Evidence**: bulleted findings, each citing a `file_path:line` (for
  code) or URL (for external). One claim per bullet.
- **Gaps** (when applicable): what you looked for and couldn't find, or
  what you deliberately didn't investigate.

## Anti-patterns

- Pasting long code blocks or page excerpts without distillation. The
  user can read the source themselves; your job is the synthesis.
- "Here are 8 things I found" without a headline answer. The user
  asked a question — answer it.
- Confident claims without a citation. Every assertion needs a file or
  URL the user can verify.
- Researching beyond what the question needs. A 200-line report when
  3 lines would do is friction, not value.
- Inventing a structure (TL;DR, Background, Deep Dive, Conclusion)
  longer than the answer warrants. Match the depth to the question.

## When NOT to use

- The user wants you to write code or edit files — that's the coding /
  python / docs-writing skills, not this one.
- The question has a one-line answer you already know. Just answer it
  inline.
- The question is "should we do X?" (advice) rather than "what is X?"
  (research). Advice needs a recommendation with a tradeoff, not a
  finding.
