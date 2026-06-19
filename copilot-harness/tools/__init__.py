"""Filesystem + command MCP tools.

harness-tier: substrate
expires-when: never — the harness becomes a complete substrate any
  MCP client can drive: governance + the actual file ops needed to
  do work. Closes the gap where a non-Copilot client (agent, Claude
  Code, Cursor, a custom driver) had no way to edit files through
  the harness.

Public surface mirrors the canonical client-side tools (`Read`,
`Write`, `Edit`, `Bash`) so a model trained on those affordances can
use them without rediscovery.
"""
