"""Vendor abstraction for the agent.

musubi-tier: substrate
expires-when: never — the LM-call boundary IS the harness's vendor
  neutrality. Hard Invariant #1 says "zero LLM calls inside the
  harness"; the agent honours it by routing every LLM call through
  an LMRouter the user picks at startup.

Adding a new vendor is one file: implement LMRouter.call() returning
an LMResponse with the Anthropic-shaped content_blocks the loop
expects, then register it in build_vendor().
"""

from agent.vendors.base import LMResponse, LMRouter
from agent.vendors.factory import build_vendor

__all__ = ["LMResponse", "LMRouter", "build_vendor"]
