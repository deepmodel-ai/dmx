"""Tests for dmx.loop_memory — loop-level memory hooks.

Covers reading Open Learnings / Open Decisions before a loop runs, and
appending Session Notes breadcrumbs when a loop finishes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.loop_memory import append_session_note, read_memory_context

if TYPE_CHECKING:
    from pathlib import Path


def _active_context_path(tmp_path: Path) -> Path:
    return tmp_path / ".dmx" / "activeContext.md"


class TestReadMemoryContext:
    def test_missing_file_returns_empty_string(self, tmp_path: Path) -> None:
        assert read_memory_context(tmp_path) == ""

    def test_empty_sections_return_empty_string(self, tmp_path: Path) -> None:
        path = _active_context_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(
            "## Open Learnings\n\n## Open Decisions\n\n## Session Notes\n- did a thing\n",
            encoding="utf-8",
        )
        assert read_memory_context(tmp_path) == ""

    def test_returns_open_learnings_and_decisions(self, tmp_path: Path) -> None:
        path = _active_context_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(
            "## Open Learnings\n"
            "- validators must return JSON on stdout only\n\n"
            "## Open Decisions\n"
            "- should retries be capped? not yet decided\n\n"
            "## Session Notes\n"
            "- spec loop completed\n",
            encoding="utf-8",
        )

        context = read_memory_context(tmp_path)

        assert "Open Learnings:" in context
        assert "validators must return JSON on stdout only" in context
        assert "Open Decisions:" in context
        assert "should retries be capped" in context
        # Session Notes are not surfaced as pre-run context.
        assert "spec loop completed" not in context

    def test_only_learnings_present(self, tmp_path: Path) -> None:
        path = _active_context_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(
            "## Open Learnings\n- some learning\n\n## Open Decisions\n\n## Session Notes\n",
            encoding="utf-8",
        )

        context = read_memory_context(tmp_path)

        assert "Open Learnings:" in context
        assert "Open Decisions:" not in context


class TestAppendSessionNote:
    def test_creates_file_with_skeleton_when_missing(self, tmp_path: Path) -> None:
        append_session_note(tmp_path, "spec loop completed (job J)")

        path = _active_context_path(tmp_path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "## Open Learnings" in content
        assert "## Open Decisions" in content
        assert "## Session Notes" in content
        assert "- spec loop completed (job J)" in content

    def test_appends_without_touching_other_sections(self, tmp_path: Path) -> None:
        path = _active_context_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(
            "## Open Learnings\n- existing learning\n\n"
            "## Open Decisions\n- existing decision\n\n"
            "## Session Notes\n- first note\n",
            encoding="utf-8",
        )

        append_session_note(tmp_path, "second note")

        content = path.read_text(encoding="utf-8")
        assert "- existing learning" in content
        assert "- existing decision" in content
        assert "- first note" in content
        assert "- second note" in content

    def test_trims_to_max_session_notes(self, tmp_path: Path) -> None:
        for i in range(12):
            append_session_note(tmp_path, f"note {i}")

        content = _active_context_path(tmp_path).read_text(encoding="utf-8")
        notes = [line for line in content.splitlines() if line.startswith("- note")]

        assert len(notes) == 10
        assert "- note 0\n" not in content
        assert "- note 1\n" not in content
        assert "- note 11" in content

    def test_adds_session_notes_header_to_file_without_one(self, tmp_path: Path) -> None:
        path = _active_context_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("## Open Learnings\n- something\n", encoding="utf-8")

        append_session_note(tmp_path, "a note")

        content = path.read_text(encoding="utf-8")
        assert "## Session Notes" in content
        assert "- a note" in content
        assert "- something" in content
