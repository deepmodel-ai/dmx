"""IDE sub-package: detection and rule-file emission."""

from __future__ import annotations

from dmx.ide.detect import resolve_ide_targets
from dmx.ide.emitters import IdeRuleFile, emit_ide_rule_files

__all__ = [
    "IdeRuleFile",
    "emit_ide_rule_files",
    "resolve_ide_targets",
]
