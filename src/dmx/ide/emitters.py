"""IDE rule file emitters: format rule content for each supported IDE."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yaml

from dmx._workflow_version import WORKFLOW_VERSION
from dmx.catalog import RuleDefinition  # noqa: TCH001 — used at runtime in emitter logic
from dmx.exceptions import EmitterError

__all__ = ["DMX_MARKER_END", "DMX_MARKER_START", "IdeRuleFile", "emit_ide_rule_files"]

logger = logging.getLogger(__name__)

# Markers used to wrap dmx-managed content in merged summary files.
# The start marker embeds the workflow version so staleness can be detected
# by comparing it against WORKFLOW_VERSION on a future re-init.
DMX_MARKER_START = f"<!-- deepmodel:dmx:start {WORKFLOW_VERSION} -->"
DMX_MARKER_END = "<!-- deepmodel:dmx:end -->"


@dataclass(frozen=True)
class IdeRuleFile:
    """A generated rule file ready to be written to a workspace.

    Attributes:
        path: Relative path within the workspace (e.g.
            ``.cursor/rules/system-prompt.mdc``).
        content: Full file content to write.
        ide: Canonical IDE identifier this file targets.
    """

    path: str
    content: str
    ide: str


def emit_ide_rule_files(
    rules: tuple[RuleDefinition, ...],
    ides: tuple[str, ...],
) -> tuple[IdeRuleFile, ...]:
    """Generate rule files for all requested IDE targets.

    Rules are filtered by their ``ides`` field before emission: a rule with
    ``ides: []`` (empty) is included for all IDEs; a rule with ``ides:
    ["cursor"]`` is only included when emitting for Cursor.

    Each IDE target calls its dedicated emitter. Unknown targets are logged
    and skipped rather than raising.

    Args:
        rules: Parsed rule definitions to emit.
        ides: Canonical IDE identifiers to generate files for.

    Returns:
        Tuple of :class:`IdeRuleFile` objects ready to write to disk.
    """
    files: list[IdeRuleFile] = []
    for ide in ides:
        ide_rules = _filter_rules_for_ide(rules, ide)
        try:
            files.extend(_emit_for_ide(ide_rules, ide))
        except EmitterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmitterError(
                f"unexpected error emitting rules for {ide!r}: {exc}", ide=ide
            ) from exc
    return tuple(files)


# ---------------------------------------------------------------------------
# Per-IDE emitters
# ---------------------------------------------------------------------------


def _filter_rules_for_ide(
    rules: tuple[RuleDefinition, ...], ide: str
) -> tuple[RuleDefinition, ...]:
    """Return only rules that target *ide*.

    A rule with an empty ``ides`` tuple is included for all IDEs.
    A rule with a non-empty ``ides`` tuple is only included when *ide* is
    listed.
    """
    return tuple(r for r in rules if not r.ides or ide in r.ides)


def _emit_for_ide(rules: tuple[RuleDefinition, ...], ide: str) -> tuple[IdeRuleFile, ...]:
    """Dispatch to the correct emitter for *ide*."""
    if ide == "cursor":
        return _emit_cursor(rules)
    if ide == "claude":
        return _emit_claude(rules)
    if ide == "copilot":
        return _emit_copilot(rules)
    if ide == "antigravity":
        return _emit_antigravity(rules)
    if ide == "agents":
        return _emit_agents(rules)
    # Windsurf emitter deferred to a follow-on PR — format TBD.
    logger.warning("no emitter implemented for IDE %r — skipping", ide)
    return ()


# ---------------------------------------------------------------------------
# Cursor emitter
# ---------------------------------------------------------------------------


def _emit_cursor(rules: tuple[RuleDefinition, ...]) -> tuple[IdeRuleFile, ...]:
    """Emit Cursor rule files.

    Per-rule:  ``.cursor/rules/{name}.mdc``
    Summary:   ``.cursor/AGENTS.md`` (dmx-managed block)
    """
    per_rule_files = tuple(
        IdeRuleFile(
            path=f".cursor/rules/{rule.name}.mdc",
            content=_cursor_mdc_content(rule),
            ide="cursor",
        )
        for rule in rules
    )
    summary = IdeRuleFile(
        path=".cursor/AGENTS.md",
        content=_marker_summary(rules),
        ide="cursor",
    )
    return (*per_rule_files, summary)


def _cursor_mdc_content(rule: RuleDefinition) -> str:
    """Render a Cursor ``.mdc`` file for *rule*.

    Frontmatter values are emitted via ``yaml.dump`` to ensure proper quoting
    of strings that contain colons, quotes, or newlines.
    """
    frontmatter: dict[str, object] = {
        "description": rule.description,
        "alwaysApply": rule.always_apply,
    }
    if rule.globs:
        frontmatter["globs"] = rule.globs

    # yaml.dump adds a trailing newline; strip it so we control the layout.
    fm_body = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).rstrip("\n")

    body = rule.body.rstrip("\n")
    return f"---\n{fm_body}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Claude emitter
# ---------------------------------------------------------------------------


def _emit_claude(rules: tuple[RuleDefinition, ...]) -> tuple[IdeRuleFile, ...]:
    """Emit Claude rule files.

    Per-rule:  ``.claude/rules/{name}.md`` (plain Markdown body — no frontmatter)
    Summary:   ``CLAUDE.md`` (dmx-managed block)
    """
    per_rule_files = tuple(
        IdeRuleFile(
            path=f".claude/rules/{rule.name}.md",
            content=rule.body.rstrip("\n") + "\n",
            ide="claude",
        )
        for rule in rules
    )
    summary = IdeRuleFile(
        path="CLAUDE.md",
        content=_marker_summary(rules),
        ide="claude",
    )
    return (*per_rule_files, summary)


# ---------------------------------------------------------------------------
# Copilot emitter
# ---------------------------------------------------------------------------


def _emit_copilot(rules: tuple[RuleDefinition, ...]) -> tuple[IdeRuleFile, ...]:
    """Emit Copilot rule files.

    No per-rule files — Copilot uses a single merged instructions file.
    Summary:   ``.github/copilot-instructions.md`` (dmx-managed block)
    """
    return (
        IdeRuleFile(
            path=".github/copilot-instructions.md",
            content=_marker_summary(rules),
            ide="copilot",
        ),
    )


# ---------------------------------------------------------------------------
# Antigravity emitter
# ---------------------------------------------------------------------------


def _emit_antigravity(rules: tuple[RuleDefinition, ...]) -> tuple[IdeRuleFile, ...]:
    """Emit Antigravity rule files.

    Per-rule:  ``.agents/rules/{name}.md`` (plain Markdown body — no frontmatter)
    No summary file — Antigravity applies all rules in ``.agents/rules/`` directly.
    """
    return tuple(
        IdeRuleFile(
            path=f".agents/rules/{rule.name}.md",
            content=rule.body.rstrip("\n") + "\n",
            ide="antigravity",
        )
        for rule in rules
    )


# ---------------------------------------------------------------------------
# Agents emitter
# ---------------------------------------------------------------------------


def _emit_agents(rules: tuple[RuleDefinition, ...]) -> tuple[IdeRuleFile, ...]:
    """Emit a root-level ``AGENTS.md`` summary for generic agent runners.

    No per-rule files.
    Summary:   ``AGENTS.md`` (dmx-managed block)
    """
    return (
        IdeRuleFile(
            path="AGENTS.md",
            content=_marker_summary(rules),
            ide="agents",
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _marker_summary(rules: tuple[RuleDefinition, ...]) -> str:
    """Render a dmx-managed summary block wrapped in idempotency markers.

    The block is designed to be injected into (or replace an existing block
    in) any Markdown summary file.  The markers allow the agent to locate and
    replace the block on subsequent calls without clobbering user content that
    sits outside the markers.
    """
    rule_lines = "\n".join(f"- **{rule.name}**: {rule.description}" for rule in rules)
    body = f"# dmx — Active Rules\n\n{rule_lines}\n"
    return f"{DMX_MARKER_START}\n{body}{DMX_MARKER_END}\n"
