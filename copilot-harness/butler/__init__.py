"""Vendor-agnostic butler — Python tool-use loop over the MCP harness.

harness-tier: substrate
expires-when: never — the butler is the model's native mode (per
  CLAUDE.md). The harness controls the environment; the model reasons.
  This package is the LLM-vendor-agnostic surface that lets ANY MCP
  client + LLM API drive the substrate.

Public surface:
    from butler.run import run_butler, main
    from butler.vendors import build_vendor, LMRouter, LMResponse
"""
