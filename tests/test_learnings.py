"""GH-88: learnings tagged for their owner, waiting in job folders until folded."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from dmx.learnings import mark_folded, read_inbox, waiting_counts, waiting_learnings
from dmx.server import create_app

if TYPE_CHECKING:
    from pathlib import Path

_INBOX = """\
## Open Learnings
- [ledger] Amounts are int64 minor units, never floats.
- [payments] Retries must be idempotent:
  use the request id as the key.
- Untagged note about CI.
- [ledger] Folded already → ledger, #418

## Open Decisions
- [repo-wide] Pick one queue for every service.

## Session Notes
- [ledger] a session note, not a learning
"""


def _inbox(root: Path, job: str, text: str = _INBOX) -> Path:
    path = root / ".dmx" / "jobs" / job / "activeContext.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestReadInbox:
    def test_tags_marks_sections_and_continuations(self, tmp_path: Path) -> None:
        items = read_inbox(_inbox(tmp_path, "GH-1"))

        assert [(i.section, i.owner, i.text, i.folded_to, i.ref) for i in items] == [
            (
                "Open Learnings",
                "ledger",
                "Amounts are int64 minor units, never floats.",
                None,
                None,
            ),
            (
                "Open Learnings",
                "payments",
                "Retries must be idempotent:\nuse the request id as the key.",
                None,
                None,
            ),
            ("Open Learnings", "repo-wide", "Untagged note about CI.", None, None),
            ("Open Learnings", "ledger", "Folded already", "ledger", "#418"),
            ("Open Decisions", "repo-wide", "Pick one queue for every service.", None, None),
        ]
        assert [i.tagged for i in items] == [True, True, False, True, True]

    def test_prose_without_bullets_is_an_item(self, tmp_path: Path) -> None:
        items = read_inbox(_inbox(tmp_path, "GH-1", "## Open Learnings\nA paragraph.\n"))

        assert [i.text for i in items] == ["A paragraph."]

    def test_a_missing_file(self, tmp_path: Path) -> None:
        assert read_inbox(tmp_path / "missing.md") == []


class TestWaiting:
    def test_unmarked_items_in_other_jobs(self, tmp_path: Path) -> None:
        _inbox(tmp_path, "GH-1")
        _inbox(tmp_path, "GH-2", "## Open Learnings\n- [ledger] From GH-2.\n")
        _inbox(tmp_path, "GH-9", "## Open Learnings\n- [ledger] The current ticket's own.\n")

        found = waiting_learnings(tmp_path, {"ledger"}, exclude_job="GH-9")

        assert [(i.job, i.text) for i in found] == [
            ("GH-1", "Amounts are int64 minor units, never floats."),
            ("GH-2", "From GH-2."),
        ]

    def test_counts_per_owner(self, tmp_path: Path) -> None:
        _inbox(tmp_path, "GH-1")
        _inbox(tmp_path, "GH-2", "## Open Learnings\n- [ledger] From GH-2.\n")

        assert waiting_counts(tmp_path, exclude_job=None) == {
            "ledger": 2,
            "payments": 1,
            "repo-wide": 2,
        }


class TestMarking:
    def test_marks_exactly_the_named_items(self, tmp_path: Path) -> None:
        path = _inbox(tmp_path, "GH-1")

        result = mark_folded(
            path,
            ["Amounts are int64 minor units, never floats.", "Retries must be idempotent:", "nope"],
            target="ledger",
            ref="#420",
        )

        assert result.marked == (
            "Amounts are int64 minor units, never floats.",
            "Retries must be idempotent:",
        )
        assert result.not_found == ("nope",)
        text = path.read_text(encoding="utf-8")
        assert "- [ledger] Amounts are int64 minor units, never floats. → ledger, #420\n" in text
        assert "  use the request id as the key. → ledger, #420\n" in text
        assert "- Untagged note about CI.\n" in text
        assert "- [ledger] a session note, not a learning\n" in text
        assert waiting_learnings(tmp_path, {"ledger", "payments"}, exclude_job=None) == []

    def test_marking_twice_changes_nothing(self, tmp_path: Path) -> None:
        path = _inbox(tmp_path, "GH-1")
        mark_folded(path, ["Untagged note about CI."], target="repo-wide", ref="feature-x")
        before = path.read_text(encoding="utf-8")

        again = mark_folded(path, ["Untagged note about CI."], target="repo-wide", ref="#1")

        assert again.marked == ()
        assert again.already == ("Untagged note about CI.",)
        assert path.read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_the_tool(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir(exist_ok=True)
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        _inbox(tmp_path, "GH-1")

        async with Client(create_app()) as client:
            done = await client.call_tool(
                "mark_learnings_folded",
                {
                    "job": "GH-1",
                    "items": ["Pick one queue for every service."],
                    "target": "repo-wide",
                    "ref": "#421",
                    "workspace_root": str(tmp_path),
                },
            )
            bad = await client.call_tool(
                "mark_learnings_folded",
                {
                    "job": "../GH-1",
                    "items": ["x"],
                    "target": "repo-wide",
                    "ref": "#421",
                    "workspace_root": str(tmp_path),
                },
            )

        assert done.data["marked"] == ["Pick one queue for every service."]
        assert "not a job" in bad.data["error"]


_SRC = __import__("pathlib").Path(__file__).parent.parent / "src" / "dmx"


def _text(rel: str) -> str:
    return (_SRC / rel).read_text(encoding="utf-8")


class TestSkillsAndRule:
    @pytest.mark.parametrize(
        "skill", ["skills/utility/dmx-commit.md", "skills/workflow/5-ship/dmx-create-pr.md"]
    )
    def test_promotion_writes_only_editable_memory(self, skill: str) -> None:
        text = _text(skill)

        assert "**In a repo with components** (`.dmx/components/` exists)" in text
        assert "`editable_memory`" in text
        assert "`[<component id>]` or `[repo-wide]`" in text
        assert "Never edit the memory of an owner that is not in `editable_memory`" in text

    def test_create_pr_folds_waiting_learnings_and_lists_them(self) -> None:
        text = _text("skills/workflow/5-ship/dmx-create-pr.md")

        assert "`waiting_learnings` whose `owner` is in `editable_memory`" in text
        assert "call `mark_learnings_folded`" in text
        assert "**Memory:** line to the description's Notes" in text

    def test_update_memory_folds_one_owner_on_demand(self) -> None:
        text = _text("skills/utility/dmx-update-memory.md")

        assert "name: target" in text
        assert '`waiting_for` set to `["{{target}}"]`' in text
        assert "`mark_learnings_folded`" in text

    def test_sync_branch_keeps_one_copy(self) -> None:
        text = _text("skills/utility/dmx-sync-branch.md")

        assert "If both sides folded in the same learning, keep one copy" in text

    def test_status_shows_waiting_counts(self) -> None:
        text = _text("skills/utility/dmx-status.md")

        assert "`waiting_counts`" in text
        assert "(run /dmx/update-memory {owner})" in text

    def test_the_rule_tags_new_items(self) -> None:
        text = _text("rules/system-prompt.md")

        assert "starts with its owner: `[<component id>]` or `[repo-wide]`" in text
        assert "I write memory only for owners in `get_context`'s `editable_memory`" in text
