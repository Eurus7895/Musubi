"""Vendor abstraction for the butler.

harness-tier: substrate
expires-when: never — the LM-call boundary IS the harness's vendor
  neutrality. Hard Invariant #1 says "zero LLM calls inside the
  harness"; the butler honours it by routing every LLM call through
  an LMRouter the user picks at startup.

Adding a new vendor is one file: implement LMRouter.call() returning
an LMResponse with the Anthropic-shaped content_blocks the loop
expects, then register it in build_vendor().
"""

from butler.vendors.base import LMResponse, LMRouter
from butler.vendors.factory import build_vendor

__all__ = ["LMResponse", "LMRouter", "build_vendor"]
