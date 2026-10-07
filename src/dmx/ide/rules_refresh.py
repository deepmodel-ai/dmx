"""Refresh IDE rule files that an earlier dmx version copied into a repo.

The start marker never includes the workflow version. A block is any line
that starts with that marker through the next end marker, so a 0.4.0
versioned marker and the current marker are the same block.
"""

from __future__ import annotations

import importlib.resources as pkg
import re
from pathlib import Path

from dmx._workflow_version import WORKFLOW_VERSION

__all__ = [
    "RULE_WRITE_INSTRUCTIONS",
    "SUMMARY_PATHS",
    "UnclosedDmxBlock",
    "ides_with_dmx_rules",
    "merge_dmx_block",
    "previous_version_label",
    "rules_reminder",
]

# Identical in setup_ide_rules notes and the init and upgrade skills.
RULE_WRITE_INSTRUCTIONS = (
    "Write each returned file as the complete file at <workspace_root>/<path>, "
    "creating parent directories as needed. "
    "Per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, .agents/rules/*.md) "
    "and summary files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, "
    ".github/copilot-instructions.md) are both complete file contents. "
    "A dmx block runs from any line that starts with `<!-- deepmodel:dmx:start` "
    "to the next `<!-- deepmodel:dmx:end -->`. "
    "The returned summary file already has every old dmx block removed and the new "
    "block written once, where the first old block was. "
    "If there was no dmx block, the block is appended. "
    "Content outside dmx blocks is never changed. "
    "Do not splice markers yourself. "
    "After writing, open a new chat for the rules to take effect."
)

_START_PREFIX = "<!-- deepmodel:dmx:start"
_END_PREFIX = "<!-- deepmodel:dmx:end"

SUMMARY_PATHS = frozenset(
    {
        ".cursor/AGENTS.md",
        "CLAUDE.md",
        "AGENTS.md",
        ".github/copilot-instructions.md",
    }
)

_PER_RULE_DIRS: tuple[tuple[str, str, str], ...] = (
    ("cursor", ".cursor/rules", ".mdc"),
    ("claude", ".claude/rules", ".md"),
    ("antigravity", ".agents/rules", ".md"),
)

_SUMMARY_FILES: tuple[tuple[str, str], ...] = (
    ("cursor", ".cursor/AGENTS.md"),
    ("claude", "CLAUDE.md"),
    ("agents", "AGENTS.md"),
    ("copilot", ".github/copilot-instructions.md"),
)

_VERSION_RE = re.compile(r"<!-- dmx-workflow-version:\s*([0-9]+(?:\.[0-9]+)*)\s*-->")
_OLD_START_RE = re.compile(r"<!-- deepmodel:dmx:start\s+([0-9]+(?:\.[0-9]+)*)")

_EARLIER = "an earlier version"


class UnclosedDmxBlock(Exception):
    """A summary file has a dmx start marker and no matching end marker."""


def merge_dmx_block(existing: str, block: str) -> str:
    """Replace every dmx block in *existing* with *block*, once.

    Lines outside those blocks are kept with their original newlines. Marker
    lines inside a fenced code block are not markers. When *existing* has no
    block, *block* is appended, using the line ending the file already has.
    A non-empty file that does not end in a newline gets one newline before
    the appended block, so the marker stays on its own line and the original
    bytes stay a prefix.

    Raises:
        UnclosedDmxBlock: A start marker has no end marker. The caller must
        leave the file unchanged.
    """
    lines = existing.splitlines(keepends=True)
    spans = _dmx_spans(lines)
    newline = "\r\n" if "\r\n" in existing else "\n"
    block = _with_ending(block, newline)
    if not spans:
        if existing and not existing.endswith(("\n", "\r")):
            return existing + newline + block
        return existing + block
    drop = {line_no for start, end in spans for line_no in range(start, end)}
    first = spans[0][0]
    kept_before = "".join(lines[:first])
    kept_after = "".join(
        line for line_no, line in enumerate(lines) if line_no not in drop and line_no >= first
    )
    return kept_before + block + kept_after


def _dmx_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Start/end line pairs for dmx blocks outside fenced code.

    Raises:
        UnclosedDmxBlock: Any start marker has no end marker.
    """
    spans: list[tuple[int, int]] = []
    in_fence = False
    index = 0
    while index < len(lines):
        if _is_fence(lines[index]):
            in_fence = not in_fence
            index += 1
            continue
        if not in_fence and lines[index].startswith(_START_PREFIX):
            end = index + 1
            closed = False
            while end < len(lines):
                if _is_fence(lines[end]):
                    in_fence = not in_fence
                    end += 1
                    continue
                if not in_fence and lines[end].startswith(_END_PREFIX):
                    spans.append((index, end + 1))
                    index = end + 1
                    closed = True
                    break
                end += 1
            if not closed:
                raise UnclosedDmxBlock
        else:
            index += 1
    return spans


def _is_fence(line: str) -> bool:
    """True for a Markdown fence line (backticks or tildes)."""
    return line.strip().startswith(("```", "~~~"))


def _with_ending(text: str, newline: str) -> str:
    """Rewrite *text* so every line ends with *newline*."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    if normalized and not normalized.endswith(newline):
        normalized += newline
    return normalized


def ides_with_dmx_rules(workspace_root: Path) -> tuple[str, ...]:
    """IDEs that already have a dmx rule file under *workspace_root*."""
    scan = _scan(workspace_root)
    return tuple(dict.fromkeys(ide for ide, _path in scan.files))


def previous_version_label(workspace_root: Path, ides: tuple[str, ...]) -> str | None:
    """Oldest workflow version among dmx rule files for *ides*.

    A file with no version line is ignored when another file has one.
    ``"an earlier version"`` when none of the files have a version. ``None``
    when none of those IDEs have a dmx rule file. A file that cannot be read
    is skipped.
    """
    versions = _versions_for(workspace_root, ides)
    if not versions:
        return None
    return _oldest_label(versions)


def rules_reminder(workspace_root: Path) -> str | None:
    """One line when a dmx rule file is older than this package, else ``None``.

    No dmx rule files, current files, or any read error: ``None``. The caller
    still returns its normal response.
    """
    scan = _scan(workspace_root)
    if scan.read_error or not scan.files:
        return None
    versions: list[str | None] = []
    for _ide, path in scan.files:
        try:
            versions.append(workflow_version_in(path.read_text(encoding="utf-8")))
        except OSError:
            return None
    if all(_version_is_current(version) for version in versions):
        return None
    label = _oldest_label(versions)
    return f"Your dmx rules are from {label} (dmx is {WORKFLOW_VERSION}). Run `/dmx/upgrade`."


def workflow_version_in(text: str) -> str | None:
    """Version stamped in *text*, or the version on an old start marker."""
    match = _VERSION_RE.search(text)
    if match:
        return match.group(1)
    old = _OLD_START_RE.search(text)
    if old:
        return old.group(1)
    return None


class _Scan:
    def __init__(self, files: tuple[tuple[str, Path], ...], read_error: bool) -> None:
        self.files = files
        self.read_error = read_error


def _scan(workspace_root: Path) -> _Scan:
    stems = _bundled_rule_stems()
    found: list[tuple[str, Path]] = []
    read_error = False
    for ide, rel_dir, suffix in _PER_RULE_DIRS:
        folder = workspace_root / rel_dir
        try:
            if not folder.is_dir():
                continue
            paths = sorted(folder.glob(f"*{suffix}"))
        except OSError:
            read_error = True
            continue
        for path in paths:
            if path.stem in stems:
                found.append((ide, path))
    for ide, rel in _SUMMARY_FILES:
        path = workspace_root / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            read_error = True
            continue
        if "deepmodel:dmx:" in text or "dmx-workflow-version:" in text:
            found.append((ide, path))
    return _Scan(tuple(found), read_error)


def _versions_for(workspace_root: Path, ides: tuple[str, ...]) -> list[str | None]:
    wanted = set(ides)
    versions: list[str | None] = []
    for ide, path in _scan(workspace_root).files:
        if ide not in wanted:
            continue
        try:
            versions.append(workflow_version_in(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return versions


def _bundled_rule_stems() -> set[str]:
    rules_dir = Path(str(pkg.files("dmx") / "rules"))
    return {path.stem for path in rules_dir.glob("*.md")}


def _version_tuple(value: str) -> tuple[int, ...] | None:
    parts = value.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _version_is_current(value: str | None) -> bool:
    if value is None:
        return False
    got = _version_tuple(value)
    current = _version_tuple(WORKFLOW_VERSION)
    if got is None or current is None:
        return False
    return got >= current


def _oldest_label(versions: list[str | None]) -> str:
    known = [
        version
        for version in versions
        if version is not None and _version_tuple(version) is not None
    ]
    if not known:
        return _EARLIER
    return min(known, key=lambda version: _version_tuple(version) or ())
