"""Ledger's own validator: amounts are int64 minor units, never floats."""

import json
import sys
from pathlib import Path

contract = json.loads(sys.stdin.read() or "{}")
root = Path(contract.get("loop_context", {}).get("workspace_root", "."))
offenders = [
    str(path.relative_to(root))
    for path in sorted((root / "services" / "ledger").rglob("*.go"))
    if "float" in path.read_text(encoding="utf-8")
]
result = {
    "pass": not offenders,
    "message": "no floats in ledger" if not offenders else f"floats in {', '.join(offenders)}",
    "checks": [{"name": "no_floats", "pass": not offenders, "message": ", ".join(offenders)}],
}
print(json.dumps(result))
sys.exit(0 if result["pass"] else 1)
