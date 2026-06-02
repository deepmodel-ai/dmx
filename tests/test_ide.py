"""Tests for dmx.ide.detect and dmx.ide.emitters."""

from __future__ import annotations

import pytest

from dmx.catalog import RuleDefinition
from dmx.ide.detect import resolve_ide_targets
from dmx.ide.emitters import (
    DMX_MARKER_END,
    DMX_MARKER_START,
    emit_ide_rule_files,
)

# ---------------------------------------------------------------------------
# resolve_ide_targets
# ---------------------------------------------------------------------------


class TestResolveIdeTargets:
    def test_cursor_detected_from_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "Cursor/1.0", None)
        assert "cursor" in ides
        assert source == "client_info"

    def test_claude_detected_from_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "claude-code/1.0", None)
        assert "claude" in ides
        assert source == "client_info"

    def test_copilot_detected_from_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "GitHub Copilot", None)
        assert "copilot" in ides
        assert source == "client_info"

    def test_windsurf_detected_from_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "windsurf/1.0", None)
        assert "windsurf" in ides
        assert source == "client_info"

    def test_antigravity_detected_from_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "antigravity/1.0", None)
        assert "antigravity" in ides
        assert source == "client_info"

    def test_unknown_client_returns_empty_ides(self) -> None:
        ides, source = resolve_ide_targets(None, "unknown-client/1.0", None)
        assert ides == ()
        assert source == "unknown"

    def test_none_client_returns_empty_ides(self) -> None:
        ides, source = resolve_ide_targets(None, None, None)
        assert ides == ()
        assert source == "unknown"

    def test_explicit_ides_override_client_name(self) -> None:
        ides, source = resolve_ide_targets(["cursor"], "claude-code", None)
        assert ides == ("cursor",)
        assert source == "explicit"

    def test_header_overrides_client_name(self) -> None:
        ides, source = resolve_ide_targets(None, "cursor", "claude")
        assert ides == ("claude",)
        assert source == "header"

    def test_env_var_overrides_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DMX_IDE", "cursor")
        ides, source = resolve_ide_targets(["claude"], "copilot", "windsurf")
        assert ides == ("cursor",)
        assert source == "env"

    def test_deepmodel_ide_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPMODEL_IDE", "cursor")
        ides, source = resolve_ide_targets(None, None, None)
        assert "cursor" in ides
        assert source == "env"

    def test_env_var_comma_separated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DMX_IDE", "cursor, claude")
        ides, source = resolve_ide_targets(None, None, None)
        assert set(ides) == {"cursor", "claude"}
        assert source == "env"

    def test_empty_explicit_ides_falls_through_to_client(self) -> None:
        ides, source = resolve_ide_targets([], "cursor/1.0", None)
        assert "cursor" in ides
        assert source == "client_info"

    def test_blank_only_explicit_ides_raises(self) -> None:
        from dmx.exceptions import IdeDetectionError

        with pytest.raises(IdeDetectionError):
            resolve_ide_targets(["  ", ""], "cursor/1.0", None)

    def test_case_insensitive_client_matching(self) -> None:
        ides, source = resolve_ide_targets(None, "CURSOR/2.0", None)
        assert "cursor" in ides

    def test_returns_tuple_of_strings(self) -> None:
        ides, _ = resolve_ide_targets(None, "Cursor", None)
        assert isinstance(ides, tuple)
        assert all(isinstance(i, str) for i in ides)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_rules() -> tuple[RuleDefinition, ...]:
    return (
        RuleDefinition(
            name="system-prompt",
            title="AI SDLC — Engineering Persona",
            description="Always-apply engineering persona.",
            always_apply=True,
            body="# Persona\n\nI am a staff-level pair programmer.\n",
        ),
    )


@pytest.fixture()
def multi_rules() -> tuple[RuleDefinition, ...]:
    return (
        RuleDefinition(
            name="system-prompt",
            title="Persona",
            description="Always-apply persona.",
            always_apply=True,
            body="# Persona\n\nBe a great engineer.\n",
        ),
        RuleDefinition(
            name="git-workflow",
            title="Git Workflow",
            description="Git branching rules.",
            always_apply=False,
            body="# Git\n\nAlways branch from main.\n",
        ),
    )


# ---------------------------------------------------------------------------
# emit_ide_rule_files — Cursor emitter
# ---------------------------------------------------------------------------


class TestEmitIdeRuleFiles:
    def test_cursor_emits_mdc_file(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        paths = {f.path for f in files}
        assert ".cursor/rules/system-prompt.mdc" in paths

    def test_cursor_emits_agents_md(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        paths = {f.path for f in files}
        assert ".cursor/AGENTS.md" in paths

    def test_all_files_have_ide_cursor(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        assert all(f.ide == "cursor" for f in files)

    def test_mdc_content_contains_frontmatter(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        mdc = next(f for f in files if f.path.endswith(".mdc"))
        assert "alwaysApply: true" in mdc.content
        assert "Always-apply engineering persona." in mdc.content

    def test_mdc_content_contains_body(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        mdc = next(f for f in files if f.path.endswith(".mdc"))
        assert "staff-level pair programmer" in mdc.content

    def test_agents_md_wrapped_in_markers(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        agents = next(f for f in files if f.path == ".cursor/AGENTS.md")
        assert DMX_MARKER_START in agents.content
        assert DMX_MARKER_END in agents.content

    def test_agents_md_lists_rule_name(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        agents = next(f for f in files if f.path == ".cursor/AGENTS.md")
        assert "system-prompt" in agents.content

    def test_unknown_ide_emits_no_files(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("unknownide",))
        assert files == ()

    def test_returns_tuple(self, sample_rules: tuple[RuleDefinition, ...]) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        assert isinstance(files, tuple)

    def test_all_files_are_frozen_dataclasses(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        for f in files:
            with pytest.raises((TypeError, AttributeError)):
                f.path = "mutated"  # type: ignore[misc]

    def test_empty_rules_emits_agents_md_with_empty_list(self) -> None:
        files = emit_ide_rule_files((), ("cursor",))
        agents = next(f for f in files if f.path == ".cursor/AGENTS.md")
        # Summary file still written even with no rules
        assert DMX_MARKER_START in agents.content

    def test_multiple_ides_emits_for_each(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor", "unknownide"))
        cursor_files = [f for f in files if f.ide == "cursor"]
        assert len(cursor_files) >= 2

    def test_snapshot_mdc_content(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        mdc = next(f for f in files if f.path.endswith(".mdc"))
        assert mdc.content == snapshot  # syrupy snapshot

    def test_snapshot_cursor_agents_md(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("cursor",))
        agents = next(f for f in files if f.path == ".cursor/AGENTS.md")
        assert agents.content == snapshot


# ---------------------------------------------------------------------------
# emit_ide_rule_files — Claude emitter
# ---------------------------------------------------------------------------


class TestEmitClaudeRules:
    def test_emits_per_rule_files(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        paths = {f.path for f in files}
        assert ".claude/rules/system-prompt.md" in paths

    def test_emits_claude_md_summary(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        paths = {f.path for f in files}
        assert "CLAUDE.md" in paths

    def test_all_files_have_ide_claude(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        assert all(f.ide == "claude" for f in files)

    def test_per_rule_file_is_plain_body(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Claude per-rule files must NOT include YAML frontmatter."""
        files = emit_ide_rule_files(sample_rules, ("claude",))
        rule_file = next(f for f in files if f.path.endswith(".md") and "rules" in f.path)
        assert "---" not in rule_file.content
        assert "alwaysApply" not in rule_file.content
        assert "# Persona" in rule_file.content

    def test_per_rule_file_ends_with_newline(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        rule_file = next(f for f in files if ".claude/rules" in f.path)
        assert rule_file.content.endswith("\n")

    def test_claude_md_wrapped_in_markers(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        summary = next(f for f in files if f.path == "CLAUDE.md")
        assert DMX_MARKER_START in summary.content
        assert DMX_MARKER_END in summary.content

    def test_claude_md_lists_rule_names(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(multi_rules, ("claude",))
        summary = next(f for f in files if f.path == "CLAUDE.md")
        assert "system-prompt" in summary.content
        assert "git-workflow" in summary.content

    def test_empty_rules_still_emits_summary(self) -> None:
        files = emit_ide_rule_files((), ("claude",))
        paths = {f.path for f in files}
        assert "CLAUDE.md" in paths

    def test_snapshot_claude_md(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        summary = next(f for f in files if f.path == "CLAUDE.md")
        assert summary.content == snapshot

    def test_snapshot_claude_rule_file(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("claude",))
        rule_file = next(f for f in files if ".claude/rules" in f.path)
        assert rule_file.content == snapshot


# ---------------------------------------------------------------------------
# emit_ide_rule_files — Copilot emitter
# ---------------------------------------------------------------------------


class TestEmitCopilotRules:
    def test_emits_copilot_instructions(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("copilot",))
        paths = {f.path for f in files}
        assert ".github/copilot-instructions.md" in paths

    def test_no_per_rule_files(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Copilot has no per-rule files — only one merged file."""
        files = emit_ide_rule_files(multi_rules, ("copilot",))
        assert len(files) == 1

    def test_all_files_have_ide_copilot(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("copilot",))
        assert all(f.ide == "copilot" for f in files)

    def test_instructions_wrapped_in_markers(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("copilot",))
        instructions = files[0]
        assert DMX_MARKER_START in instructions.content
        assert DMX_MARKER_END in instructions.content

    def test_instructions_list_rule_names(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(multi_rules, ("copilot",))
        assert "system-prompt" in files[0].content
        assert "git-workflow" in files[0].content

    def test_empty_rules_still_emits_summary(self) -> None:
        files = emit_ide_rule_files((), ("copilot",))
        assert len(files) == 1
        assert files[0].path == ".github/copilot-instructions.md"

    def test_snapshot_copilot_instructions(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("copilot",))
        assert files[0].content == snapshot


# ---------------------------------------------------------------------------
# emit_ide_rule_files — Antigravity emitter
# ---------------------------------------------------------------------------


class TestEmitAntigravityRules:
    def test_emits_per_rule_files(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        paths = {f.path for f in files}
        assert ".agents/rules/system-prompt.md" in paths

    def test_no_summary_file(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Antigravity has no merged summary file."""
        files = emit_ide_rule_files(multi_rules, ("antigravity",))
        assert len(files) == len(multi_rules)

    def test_all_files_have_ide_antigravity(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        assert all(f.ide == "antigravity" for f in files)

    def test_per_rule_file_is_plain_body(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Antigravity per-rule files are plain Markdown — no frontmatter."""
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        assert len(files) == 1
        assert "---" not in files[0].content
        assert "alwaysApply" not in files[0].content
        assert "# Persona" in files[0].content

    def test_no_marker_content(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Antigravity does not produce summary/marker files."""
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        for f in files:
            assert DMX_MARKER_START not in f.content

    def test_per_rule_file_ends_with_newline(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        assert files[0].content.endswith("\n")

    def test_empty_rules_emits_nothing(self) -> None:
        files = emit_ide_rule_files((), ("antigravity",))
        assert files == ()

    def test_snapshot_antigravity_rule_file(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("antigravity",))
        assert files[0].content == snapshot


# ---------------------------------------------------------------------------
# emit_ide_rule_files — Agents emitter
# ---------------------------------------------------------------------------


class TestEmitAgentsRules:
    def test_emits_agents_md(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("agents",))
        paths = {f.path for f in files}
        assert "AGENTS.md" in paths

    def test_no_per_rule_files(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        """Agents emitter produces only the root AGENTS.md."""
        files = emit_ide_rule_files(multi_rules, ("agents",))
        assert len(files) == 1

    def test_all_files_have_ide_agents(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("agents",))
        assert all(f.ide == "agents" for f in files)

    def test_agents_md_wrapped_in_markers(
        self, sample_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("agents",))
        agents_md = files[0]
        assert DMX_MARKER_START in agents_md.content
        assert DMX_MARKER_END in agents_md.content

    def test_agents_md_lists_rule_names(
        self, multi_rules: tuple[RuleDefinition, ...]
    ) -> None:
        files = emit_ide_rule_files(multi_rules, ("agents",))
        assert "system-prompt" in files[0].content
        assert "git-workflow" in files[0].content

    def test_empty_rules_still_emits_summary(self) -> None:
        files = emit_ide_rule_files((), ("agents",))
        assert len(files) == 1
        assert files[0].path == "AGENTS.md"

    def test_snapshot_agents_md(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        snapshot: object,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, ("agents",))
        assert files[0].content == snapshot


# ---------------------------------------------------------------------------
# Marker summary — idempotent content structure
# ---------------------------------------------------------------------------


class TestMarkerSummaryStructure:
    """Verify the shared marker block format used in merged summary files."""

    @pytest.mark.parametrize("ide", ["cursor", "claude", "copilot", "agents"])
    def test_summary_has_both_markers(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        ide: str,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, (ide,))
        # Find the summary file (no 'rules/' subdirectory in its path)
        summary = next((f for f in files if "rules/" not in f.path), None)
        assert summary is not None
        assert DMX_MARKER_START in summary.content
        assert DMX_MARKER_END in summary.content

    @pytest.mark.parametrize("ide", ["cursor", "claude", "copilot", "agents"])
    def test_summary_marker_ordering(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        ide: str,
    ) -> None:
        """The start marker must precede the end marker."""
        files = emit_ide_rule_files(sample_rules, (ide,))
        summary = next((f for f in files if "rules/" not in f.path), None)
        assert summary is not None
        start_pos = summary.content.index(DMX_MARKER_START)
        end_pos = summary.content.index(DMX_MARKER_END)
        assert start_pos < end_pos

    @pytest.mark.parametrize("ide", ["cursor", "claude", "copilot", "agents"])
    def test_summary_contains_rule_descriptions(
        self,
        sample_rules: tuple[RuleDefinition, ...],
        ide: str,
    ) -> None:
        files = emit_ide_rule_files(sample_rules, (ide,))
        summary = next((f for f in files if "rules/" not in f.path), None)
        assert summary is not None
        assert "Always-apply engineering persona." in summary.content


# ---------------------------------------------------------------------------
# TestRuleIdesFiltering
# ---------------------------------------------------------------------------


class TestRuleIdesFiltering:
    def test_empty_ides_rule_included_for_all(self) -> None:
        """Rules with ides=() are emitted for every IDE target."""
        rules = (
            RuleDefinition(name="all-rule", title="All", description="x", ides=()),
        )
        files = emit_ide_rule_files(rules, ("cursor",))
        paths = {f.path for f in files}
        assert ".cursor/rules/all-rule.mdc" in paths

    def test_cursor_only_rule_excluded_for_unknown_ide(self) -> None:
        rules = (
            RuleDefinition(
                name="cursor-rule", title="C", description="x", ides=("cursor",)
            ),
        )
        # 'unknownide' has no emitter; even if it did, this rule targets cursor only.
        files = emit_ide_rule_files(rules, ("unknownide",))
        assert files == ()

    def test_rule_with_matching_ide_is_included(self) -> None:
        rules = (
            RuleDefinition(
                name="cursor-rule", title="C", description="x", ides=("cursor",)
            ),
        )
        files = emit_ide_rule_files(rules, ("cursor",))
        paths = {f.path for f in files}
        assert ".cursor/rules/cursor-rule.mdc" in paths

    def test_rule_without_matching_ide_is_excluded(self) -> None:
        cursor_rule = RuleDefinition(
            name="cursor-only", title="C", description="x", ides=("cursor",)
        )
        # Emitting for "unknownide" — cursor-only rule filtered out at ides check.
        rules = (cursor_rule,)
        files = emit_ide_rule_files(rules, ("unknownide",))
        assert all("cursor-only" not in f.path for f in files)

    def test_claude_only_rule_excluded_for_cursor(self) -> None:
        rules = (
            RuleDefinition(
                name="claude-only", title="C", description="x", ides=("claude",)
            ),
        )
        files = emit_ide_rule_files(rules, ("cursor",))
        assert all("claude-only" not in f.path for f in files)
