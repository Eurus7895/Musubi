"""Vendor-agnostic agent — Python tool-use loop over the MCP harness.

musubi-tier: substrate
expires-when: never — the agent is the model's native mode (per
  CLAUDE.md). The harness controls the environment; the model reasons.
  This package is the LLM-vendor-agnostic surface that lets ANY MCP
  client + LLM API drive the substrate.

Public surface:
    from agent.run import run_agent, main
    from agent.vendors import build_vendor, LMRouter, LMResponse
"""
