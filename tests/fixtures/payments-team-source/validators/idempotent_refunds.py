"""The payments team's validator: every refund handler takes an idempotency key."""

import json
import sys
from pathlib import Path

contract = json.loads(sys.stdin.read() or "{}")
root = Path(contract.get("loop_context", {}).get("workspace_root", "."))
missing = [
    str(path.relative_to(root))
    for path in sorted((root / "services" / "payments").rglob("*.go"))
    if "func Refund" in (text := path.read_text(encoding="utf-8")) and "idempotencyKey" not in text
]
result = {
    "pass": not missing,
    "message": "refunds are idempotent" if not missing else f"no idempotency key in {', '.join(missing)}",
    "checks": [{"name": "keyed_refunds", "pass": not missing, "message": ", ".join(missing)}],
}
print(json.dumps(result))
sys.exit(0 if result["pass"] else 1)
