"""Bundled validator: spec_adherence.

The loop runtime design document notes that a ``spec_adherence`` validator
typically "calls an LLM API directly to check whether the implementation
matches the spec." This bundled default does **not** call an LLM — it has
no configured API key or provider to call — and instead applies a
deterministic textual-overlap heuristic between ``.dmx/spec.md``'s Scope
section and the skills' combined output.

This is a conservative baseline, not a real adherence check. Override
``validators/spec_adherence.py`` in the app repo with an LLM-backed
implementation for meaningful signal — the orchestrator does not care what
happens inside a validator, only that it returns the standard contract.

Contract
--------
Called by the orchestrator via subprocess. The input contract is written to
stdin as JSON::

    python validators/spec_adherence.py < contract.json

    {
      "skill_outputs": {...},
      "goal_state": "...",
      "loop_context": {..., "workspace_root": "/path/to/repo"}
    }

Exits 0 on pass, 1 on failure.
Writes JSON to stdout::

    {
      "pass": true,
      "message": "Spec adherence heuristic passed",
      "checks": [
        {"name": "scope_matches_spec",   "pass": true},
        {"name": "edge_cases_addressed", "pass": true},
        {"name": "no_regressions",       "pass": true}
      ]
    }
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_SCOPE_SECTION_RE = re.compile(
    r"^#{1,4}\s+scope\b.*?\n(.*?)(?=^#{1,4}\s|\Z)",
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$", re.MULTILINE)
_WORD_RE = re.compile(r"[a-zA-Z]{4,}")

_SCOPE_COVERAGE_THRESHOLD = 0.6
_EDGE_CASE_KEYWORDS = (
    "edge case",
    "boundary",
    "error handling",
    "exception",
    "invalid input",
    "empty input",
)
_REGRESSION_KEYWORDS = (
    "regression",
    "broke",
    "broken test",
    "test failure",
    "tests failing",
    "failing test",
)


def _combined_skill_text(skill_outputs: dict[str, str]) -> str:
    return "\n".join(str(v) for v in skill_outputs.values())


def _extract_scope_bullets(spec_content: str) -> list[str]:
    match = _SCOPE_SECTION_RE.search(spec_content)
    if not match:
        return []
    return [b.strip() for b in _BULLET_RE.findall(match.group(1)) if b.strip()]


def _scope_matches_spec(spec_content: str, skill_text: str) -> tuple[bool, str]:
    bullets = _extract_scope_bullets(spec_content)
    if not bullets:
        return False, "No Scope section found in spec.md"

    skill_lower = skill_text.lower()
    covered = 0
    for bullet in bullets:
        words = _WORD_RE.findall(bullet.lower())
        if not words:
            covered += 1
            continue
        hits = sum(1 for w in words if w in skill_lower)
        if hits >= max(1, len(words) // 2):
            covered += 1

    ratio = covered / len(bullets)
    passed = ratio >= _SCOPE_COVERAGE_THRESHOLD
    return passed, f"{covered}/{len(bullets)} scope item(s) referenced in skill output"


def _edge_cases_addressed(skill_text: str) -> tuple[bool, str]:
    lower = skill_text.lower()
    hits = [k for k in _EDGE_CASE_KEYWORDS if k in lower]
    if hits:
        return True, f"Edge-case handling referenced: {', '.join(hits)}"
    return False, "No explicit mention of edge-case handling in skill output"


def _no_regressions(skill_text: str) -> tuple[bool, str]:
    lower = skill_text.lower()
    hits = [k for k in _REGRESSION_KEYWORDS if k in lower]
    if hits:
        return False, f"Potential regression language found: {', '.join(hits)}"
    return True, "No regression language detected in skill output"


def run(workspace_root: Path, skill_outputs: dict[str, Any]) -> dict:
    spec_path = workspace_root / ".dmx" / "spec.md"
    spec_content = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    skill_text = _combined_skill_text(skill_outputs)

    checks_raw = [
        ("scope_matches_spec", _scope_matches_spec(spec_content, skill_text)),
        ("edge_cases_addressed", _edge_cases_addressed(skill_text)),
        ("no_regressions", _no_regressions(skill_text)),
    ]

    checks = [{"name": name, "pass": passed, "message": msg} for name, (passed, msg) in checks_raw]

    overall = all(c["pass"] for c in checks)
    summary_msg = (
        "Spec adherence heuristic passed"
        if overall
        else "Spec adherence heuristic failed — see checks for details"
    )

    return {
        "pass": overall,
        "message": summary_msg,
        "checks": checks,
    }


if __name__ == "__main__":
    contract = json.loads(sys.stdin.read() or "{}")
    workspace_root = contract.get("loop_context", {}).get("workspace_root") or "."
    result = run(Path(workspace_root), contract.get("skill_outputs", {}))
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)
