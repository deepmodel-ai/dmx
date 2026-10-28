"""GH-158: always-loaded and repeated text stays short and correct."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "dmx"


def test_rule_asks_loop_status_instead_of_reading_state_files() -> None:
    rule = (SRC / "rules" / "system-prompt.md").read_text(encoding="utf-8")
    loop_mode = rule.split("## Loop mode", 1)[1].split("\n## ", 1)[0]

    assert "I call `loop_status`" in loop_mode
    assert ".json" not in loop_mode
    assert "I don't end my turn while validators are running" in loop_mode
    assert "`reworking` loop with no rework run open is waiting for `loop_continue`" in loop_mode


def test_skills_read_config_from_the_file() -> None:
    """No IDE file loads .dmx/config.md, so a skill must read it itself."""
    for path in (SRC / "skills").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "injected into your context as a rule" not in text, path.name
        assert "available in context" not in text, path.name


def test_ticket_files_paragraph_is_short() -> None:
    paragraphs = [
        line
        for path in (SRC / "skills").rglob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("**Ticket files.**")
    ]

    assert len(paragraphs) == 8
    assert all(len(line) < 400 for line in paragraphs)
