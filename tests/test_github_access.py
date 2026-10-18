"""GH-120: skills use the gh CLI when no GitHub MCP server is connected."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dmx.setup_check import check_setup

if TYPE_CHECKING:
    import pytest

SRC = Path(__file__).parent.parent / "src" / "dmx"
RULE = (SRC / "rules" / "system-prompt.md").read_text(encoding="utf-8")
POINTER = "run the `gh` equivalent from the dmx rule's **GitHub access** table instead."
CALL_RE = re.compile(r"Call `(\w+)` on `user-github`")


def _skills_using_github() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted((SRC / "skills").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "user-github" in text and path.name != "dmx-init.md":
            found[path.name] = text
    return found


def _rule_section() -> str:
    return RULE.split("## GitHub access", 1)[1].split("\n## ", 1)[0]


class TestSkills:
    def test_every_github_skill_points_to_the_gh_table(self) -> None:
        skills = _skills_using_github()

        assert len(skills) == 10
        missing = [name for name, text in skills.items() if POINTER not in text]
        assert missing == []

    def test_the_rule_maps_every_tool_the_skills_call(self) -> None:
        called = {
            tool for text in _skills_using_github().values() for tool in CALL_RE.findall(text)
        }
        section = _rule_section()

        assert called
        assert sorted(tool for tool in called if f"`{tool}`" not in section) == []
        assert "`github_access: gh`" in section
        assert "--body-file" in section


class TestInit:
    def test_init_falls_back_to_gh_and_records_it(self) -> None:
        skill = (SRC / "skills" / "workflow" / "0-init" / "dmx-init.md").read_text(encoding="utf-8")

        assert "gh auth status" in skill
        assert "record `github_access` = `gh`" in skill
        assert "github_access:  {github_access}" in skill
        for setup in ("gh auth login", "claude mcp add", "Cursor:", "VS Code:"):
            assert setup in skill


def _config(root: Path, access: str | None) -> None:
    (root / ".dmx").mkdir(parents=True, exist_ok=True)
    line = f"github_access:  {access}\n" if access else ""
    (root / ".dmx" / "config.md").write_text(f"branch_base: main\n{line}", encoding="utf-8")


def _fake_gh(folder: Path, code: int) -> None:
    """A ``gh`` on PATH whose ``auth status`` exits *code*."""
    folder.mkdir()
    if sys.platform == "win32":
        (folder / "gh.cmd").write_text(f"@exit /b {code}\r\n", encoding="utf-8")
    else:
        script = folder / "gh"
        script.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
        script.chmod(0o755)


def _github_findings(root: Path) -> list[tuple[str, str]]:
    return [(f.level, f.text) for f in check_setup(root) if "GitHub access" in f.text]


class TestCheck:
    def test_mcp_is_reported(self, tmp_path: Path) -> None:
        _config(tmp_path, "mcp")

        assert _github_findings(tmp_path) == [
            ("ok", "GitHub access: the GitHub MCP server (user-github)")
        ]

    def test_a_signed_in_gh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _config(tmp_path, "gh")
        _fake_gh(tmp_path / "bin", 0)
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))

        assert _github_findings(tmp_path) == [("ok", "GitHub access: the gh CLI")]

    def test_a_signed_out_gh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _config(tmp_path, "gh")
        _fake_gh(tmp_path / "bin", 1)
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))

        assert _github_findings(tmp_path) == [
            ("error", "GitHub access is the gh CLI, but it isn't signed in")
        ]

    def test_gh_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _config(tmp_path, "gh")
        (tmp_path / "empty").mkdir()
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))

        assert _github_findings(tmp_path) == [
            ("error", "GitHub access is the gh CLI, but `gh` isn't on PATH")
        ]

    def test_an_older_config_says_nothing(self, tmp_path: Path) -> None:
        _config(tmp_path, None)

        assert _github_findings(tmp_path) == []
