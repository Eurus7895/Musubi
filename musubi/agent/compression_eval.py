"""Driver-side compression eval runner.

musubi-tier: substrate
expires-when: never - optional real-LM probing belongs at the driver inject
  point; the default eval path remains deterministic and zero-LLM.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from agent.config import load_profile
from agent.vendors import build_from_profile
from compression.eval import run_compression_eval
from compression.router import compress


def real_lm_eval_enabled(real_lm: bool) -> bool:
    """Return True only when the caller explicitly requests a real LM probe."""
    return bool(real_lm)


def run_real_lm_probe(profile: str | None, *, db_path: Path) -> dict[str, Any]:
    """Ask a real model whether it calls retrieve for an exact-detail task."""
    router = build_from_profile(load_profile(profile))
    payload = json.dumps(
        {
            "items": [
                {"id": i, "exact_code": f"CODE-{i:04d}", "value": "x" * 20}
                for i in range(220)
            ]
        },
        indent=2,
    )
    packed = compress(payload, hint="probe.json", db_path=db_path)
    target = "CODE-0219"
    response = router.call(
        [
            {
                "role": "user",
                "content": (
                    "Find the exact item containing CODE-0219. If the visible "
                    "payload is compressed, call musubi_retrieve before answering.\n\n"
                    f"{packed.compressed}"
                ),
            }
        ],
        [
            {
                "name": "musubi_retrieve",
                "description": "Return the verbatim original for a compression ref_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {"ref_id": {"type": "string"}},
                    "required": ["ref_id"],
                },
            }
        ],
        max_tokens=512,
    )
    retrieve_calls = [
        block for block in response.content
        if block.get("type") == "tool_use" and block.get("name") == "musubi_retrieve"
    ]
    return {
        "profile": profile,
        "model": router.model,
        "target": target,
        "compressed_ref": packed.ref_id,
        "retrieve_requested": bool(retrieve_calls),
        "retrieve_call_count": len(retrieve_calls),
        "stop_reason": response.stop_reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Musubi compression eval gate.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the eval JSON report to this path.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite DB path for eval compression blobs.",
    )
    parser.add_argument(
        "--real-lm",
        action="store_true",
        help="Also run the optional real-LM retrieve probe.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="LM profile for --real-lm; defaults to .musubi/llm.json default.",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = args.db or Path(tmp) / "compression_eval.db"
        report = run_compression_eval(db_path=db_path)
        if real_lm_eval_enabled(args.real_lm):
            report["real_lm_probe"] = run_real_lm_probe(args.profile, db_path=db_path)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
