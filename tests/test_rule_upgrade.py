"""IDE rule refresh: one dmx block, a version stamp, and /dmx/upgrade."""

from __future__ import annotations

import asyncio
import importlib.resources as pkg
from pathlib import Path

import pytest

from dmx._workflow_version import WORKFLOW_VERSION
from dmx.catalog import load_rules
from dmx.ide.emitters import emit_ide_rule_files
from dmx.ide.rules_refresh import (
    RULE_WRITE_INSTRUCTIONS,
    UnclosedDmxBlock,
    merge_dmx_block,
    previous_version_label,
    rules_reminder,
)
from dmx.loop_tools import _with_rules_reminder

_OLD_BLOCK = "<!-- deepmodel:dmx:start 0.4.0 -->\nold\n<!-- deepmodel:dmx:end -->\n"
_REMINDER_040 = f"Your dmx rules are from 0.4.0 (dmx is {WORKFLOW_VERSION}). Run `/dmx/upgrade`."
_REMINDER_EARLIER = (
    f"Your dmx rules are from an earlier version (dmx is {WORKFLOW_VERSION}). Run `/dmx/upgrade`."
)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_versioned_block_keeps_developer_content() -> None:
    above = "# mine\n\n"
    below = "\n# still mine\n"
    existing = above + _OLD_BLOCK + below
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    merged = merge_dmx_block(existing, block)

    assert merged.startswith(above)
    assert merged.endswith(below)
    assert merged.count("<!-- deepmodel:dmx:start") == 1
    assert "old\n" not in merged
    assert above + block + below == merged


def test_two_blocks_collapse_to_one() -> None:
    existing = "# mine\n" + _OLD_BLOCK + "# still mine\n" + _OLD_BLOCK + "# tail\n"
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    merged = merge_dmx_block(existing, block)

    assert merged == "# mine\n" + block + "# still mine\n" + "# tail\n"
    assert merged.count("<!-- deepmodel:dmx:start") == 1


def test_missing_block_is_appended() -> None:
    existing = "# mine\n"
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    merged = merge_dmx_block(existing, block)

    assert merged == existing + block


def test_missing_block_on_a_file_without_a_trailing_newline() -> None:
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    assert merge_dmx_block("hello", block) == "hello\n" + block


def test_newer_numeric_version_is_current(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ".cursor/rules/system-prompt.mdc",
        "<!-- dmx-workflow-version: 0.10.0 -->\n",
    )

    assert rules_reminder(tmp_path) is None


def test_current_rules_have_no_reminder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ".cursor/rules/system-prompt.mdc",
        f"<!-- dmx-workflow-version: {WORKFLOW_VERSION} -->\n",
    )

    assert rules_reminder(tmp_path) is None


def test_no_dmx_rule_files_have_no_reminder(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "# my own notes\n")

    assert rules_reminder(tmp_path) is None


def test_old_summary_reminds_once(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/AGENTS.md", "# mine\n" + _OLD_BLOCK)

    reminder = rules_reminder(tmp_path)
    message = _with_rules_reminder(tmp_path, "status")
    again = _with_rules_reminder(tmp_path, message)

    assert reminder == _REMINDER_040
    assert message == f"status\n\n{_REMINDER_040}"
    assert again == message


def test_unclosed_start_marker_is_not_merged() -> None:
    existing = "# My notes\n<!-- deepmodel:dmx:start 0.4.0 -->\nOLD\n## My team section\nkeep me\n"
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    with pytest.raises(UnclosedDmxBlock):
        merge_dmx_block(existing, block)


def test_marker_inside_a_fence_is_not_a_block() -> None:
    existing = (
        "# mine\n"
        "```\n"
        "<!-- deepmodel:dmx:start -->\n"
        "quoted\n"
        "```\n"
        "<!-- deepmodel:dmx:start 0.4.0 -->\nold\n<!-- deepmodel:dmx:end -->\n"
        "# tail\n"
    )
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    merged = merge_dmx_block(existing, block)

    assert "```\n<!-- deepmodel:dmx:start -->\nquoted\n```\n" in merged
    assert "old\n" not in merged
    assert merged.endswith("# tail\n")
    assert merged.count("<!-- deepmodel:dmx:start") == 2


def test_crlf_file_keeps_crlf() -> None:
    existing = (
        "# mine\r\n\r\n"
        "<!-- deepmodel:dmx:start 0.4.0 -->\r\n"
        "old\r\n"
        "<!-- deepmodel:dmx:end -->\r\n"
        "# tail\r\n"
    )
    block = "<!-- deepmodel:dmx:start -->\nnew\n<!-- deepmodel:dmx:end -->\n"

    merged = merge_dmx_block(existing, block)

    assert "\n" not in merged.replace("\r\n", "")
    assert merged.startswith("# mine\r\n\r\n")
    assert merged.endswith("# tail\r\n")
    assert "old\r\n" not in merged


def test_unversioned_rule_file_reminds(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/rules/system-prompt.mdc", "# old rules\n")

    assert rules_reminder(tmp_path) == _REMINDER_EARLIER


def test_mixed_repo_reports_the_oldest_known_version(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/AGENTS.md", _OLD_BLOCK)
    _write(tmp_path, ".cursor/rules/system-prompt.mdc", "# old rules\n")

    assert rules_reminder(tmp_path) == _REMINDER_040
    assert previous_version_label(tmp_path, ("cursor",)) == "0.4.0"


def test_unreadable_rule_file_skips_the_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, ".cursor/rules/system-prompt.mdc", "# old rules\n")
    original = Path.read_text

    def guarded(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise OSError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    assert rules_reminder(tmp_path) is None


def test_every_emitted_file_carries_the_workflow_version() -> None:
    rules_dir = Path(str(pkg.files("dmx") / "rules"))
    rules = load_rules(rules_dir)
    stamp = f"dmx-workflow-version: {WORKFLOW_VERSION}"

    for ide in ("cursor", "claude", "copilot", "antigravity", "agents"):
        files = emit_ide_rule_files(rules, (ide,))
        assert files
        for item in files:
            assert stamp in item.content


def _bundled_skill(name: str) -> str:
    path = Path(str(pkg.files("dmx") / "skills/workflow/0-init" / name))
    return path.read_text(encoding="utf-8")


def test_upgrade_skill_refreshes_rules_and_leaves_the_repo_state() -> None:
    skill = _bundled_skill("dmx-upgrade.md")

    assert "name: upgrade" in skill
    assert "include_existing" in skill
    assert "already_current" in skill
    assert "Do not ask questions." in skill
    assert ".dmx/config.md" in skill
    assert "memory bank" in skill
    assert ".dmx/jobs/" in skill
    assert "Do not commit" in skill
    assert "invoking IDE" in skill
    assert RULE_WRITE_INSTRUCTIONS in skill
    assert (
        "If `notes` lists files that could not be read or have no end to their dmx block, "
        "tell the developer which files were not changed and that they need to be fixed "
        "by hand before re-running `/dmx/upgrade`."
    ) in skill

    from dmx.server import create_app

    prompts = asyncio.run(create_app().list_prompts())
    assert any(prompt.name == "upgrade" for prompt in prompts)


def test_write_instructions_match_in_notes_and_init() -> None:
    init = _bundled_skill("dmx-init.md")
    assert RULE_WRITE_INSTRUCTIONS in init


class TestSetupMergesSummaryFiles:
    @pytest.mark.asyncio
    async def test_notes_use_the_shared_paragraph(self, tmp_path: Path) -> None:
        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": str(tmp_path)},
            )

        assert RULE_WRITE_INSTRUCTIONS in result.data["notes"]

    @pytest.mark.asyncio
    async def test_versioned_block_and_developer_content(self, tmp_path: Path) -> None:
        above = "# mine\n\n"
        below = "\n# still mine\n"
        _write(tmp_path, "CLAUDE.md", above + _OLD_BLOCK + below)
        content = await _claude_summary(tmp_path)

        assert content.startswith(above)
        assert content.endswith(below)
        assert content.count("<!-- deepmodel:dmx:start") == 1
        assert "old\n" not in content
        assert f"dmx-workflow-version: {WORKFLOW_VERSION}" in content

    @pytest.mark.asyncio
    async def test_two_blocks_become_one(self, tmp_path: Path) -> None:
        existing = "# mine\n" + _OLD_BLOCK + "# still mine\n" + _OLD_BLOCK + "# tail\n"
        _write(tmp_path, "CLAUDE.md", existing)
        content = await _claude_summary(tmp_path)

        assert content.count("<!-- deepmodel:dmx:start") == 1
        assert "# mine\n" in content
        assert "# still mine\n" in content
        assert content.endswith("# tail\n")
        assert "old\n" not in content

    @pytest.mark.asyncio
    async def test_summary_without_a_block_is_appended(self, tmp_path: Path) -> None:
        existing = "# mine\n"
        _write(tmp_path, "CLAUDE.md", existing)
        content = await _claude_summary(tmp_path)

        assert content.startswith(existing)
        assert content.count("<!-- deepmodel:dmx:start") == 1

    @pytest.mark.asyncio
    async def test_unreadable_summary_is_left_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write(tmp_path, "CLAUDE.md", "# mine\n")
        original = Path.read_text

        def guarded(self: Path, *args: object, **kwargs: object) -> str:
            if self == path:
                raise OSError("denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded)

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "claude", "workspace_root": str(tmp_path)},
            )

        paths = [item["path"] for item in result.data["files"]]
        assert "CLAUDE.md" not in paths
        assert any(item.startswith(".claude/rules/") for item in paths)
        assert "Could not read and did not replace: CLAUDE.md" in result.data["notes"]

    @pytest.mark.asyncio
    async def test_unclosed_block_is_left_unchanged_and_reported(self, tmp_path: Path) -> None:
        original = (
            "# My notes\n<!-- deepmodel:dmx:start 0.4.0 -->\nOLD\n## My team section\nkeep me\n"
        )
        path = _write(tmp_path, "CLAUDE.md", original)

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "claude", "workspace_root": str(tmp_path)},
            )
            for item in result.data["files"]:
                _write(tmp_path, item["path"], item["content"])
            again = await client.call_tool(
                "setup_ide_rules",
                {"ides": "claude", "workspace_root": str(tmp_path)},
            )

        paths = [item["path"] for item in result.data["files"]]
        assert "CLAUDE.md" not in paths
        assert path.read_text(encoding="utf-8") == original
        note = "Could not find the end of the dmx block in CLAUDE.md; fix it by hand and re-run."
        assert note in result.data["notes"]
        assert result.data["already_current"] is False
        assert again.data["already_current"] is False
        assert "CLAUDE.md" not in [item["path"] for item in again.data["files"]]

    @pytest.mark.asyncio
    async def test_include_existing_adds_ides_already_on_disk(self, tmp_path: Path) -> None:
        _write(tmp_path, ".cursor/rules/system-prompt.mdc", "# old\n")

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {
                    "ides": "claude",
                    "include_existing": True,
                    "workspace_root": str(tmp_path),
                },
            )

        assert result.data["resolved_ides"] == ["claude", "cursor"]
        assert result.data["previous_version"] == "an earlier version"
        assert result.data["already_current"] is False

    @pytest.mark.asyncio
    async def test_unrelated_claude_md_is_not_a_dmx_rule_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "CLAUDE.md", "# my own notes\n")

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {
                    "ides": "cursor",
                    "include_existing": True,
                    "workspace_root": str(tmp_path),
                },
            )

        assert result.data["resolved_ides"] == ["cursor"]

    @pytest.mark.asyncio
    async def test_already_current_upgrade_writes_nothing(self, tmp_path: Path) -> None:
        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            first = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": str(tmp_path)},
            )
            for item in first.data["files"]:
                _write(tmp_path, item["path"], item["content"])
            second = await client.call_tool(
                "setup_ide_rules",
                {
                    "ides": "cursor",
                    "include_existing": True,
                    "workspace_root": str(tmp_path),
                },
            )

        assert second.data["already_current"] is True
        assert second.data["files"] == []
        assert second.data["previous_version"] == WORKFLOW_VERSION

    @pytest.mark.asyncio
    async def test_init_still_returns_files_when_current(self, tmp_path: Path) -> None:
        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            first = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": str(tmp_path)},
            )
            for item in first.data["files"]:
                _write(tmp_path, item["path"], item["content"])
            second = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": str(tmp_path)},
            )

        assert second.data["already_current"] is True
        assert second.data["files"]


class TestStaleRulesReminder:
    @pytest.mark.asyncio
    async def test_tools_add_the_reminder_once(self, tmp_path: Path) -> None:
        _write(tmp_path, ".cursor/AGENTS.md", _OLD_BLOCK)

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            listed = await client.call_tool("list_skills", {"workspace_root": str(tmp_path)})
            status = await client.call_tool("loop_status", {"workspace_root": str(tmp_path)})
            advance = await client.call_tool(
                "loop_advance",
                {"output": "done", "skill": "plan", "workspace_root": str(tmp_path)},
            )
            started = await client.call_tool(
                "run_loop",
                {"name": "no-such-loop", "workspace_root": str(tmp_path)},
            )

        assert listed.data == f"No local or shared skills.\n\n{_REMINDER_040}"
        assert status.data == (
            "No active loop run found. "
            "Start a loop with `run_loop` or check if the previous loop completed.\n\n"
            f"{_REMINDER_040}"
        )
        assert advance.data == "No active loop run found. Start a loop with `run_loop` first."
        assert str(started.data).startswith("Error:")
        assert str(started.data).count(_REMINDER_040) == 1

    @pytest.mark.asyncio
    async def test_current_rules_add_nothing(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            ".cursor/rules/system-prompt.mdc",
            f"<!-- dmx-workflow-version: {WORKFLOW_VERSION} -->\n",
        )

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            listed = await client.call_tool("list_skills", {"workspace_root": str(tmp_path)})

        assert listed.data == "No local or shared skills."

    @pytest.mark.asyncio
    async def test_unreadable_rule_file_still_returns_the_tool_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write(tmp_path, ".cursor/rules/system-prompt.mdc", "# old\n")
        original = Path.read_text

        def guarded(self: Path, *args: object, **kwargs: object) -> str:
            if self == path:
                raise OSError("denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded)

        from fastmcp import Client

        from dmx.server import create_app

        app = create_app()
        async with Client(app) as client:
            listed = await client.call_tool("list_skills", {"workspace_root": str(tmp_path)})

        assert listed.data == "No local or shared skills."


async def _claude_summary(tmp_path: Path) -> str:
    from fastmcp import Client

    from dmx.server import create_app

    app = create_app()
    async with Client(app) as client:
        result = await client.call_tool(
            "setup_ide_rules",
            {"ides": "claude", "workspace_root": str(tmp_path)},
        )
    summary = next(item for item in result.data["files"] if item["path"] == "CLAUDE.md")
    return str(summary["content"])
