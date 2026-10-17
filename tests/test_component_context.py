"""GH-86: the components line on loop messages, and ``get_context``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from dmx.server import create_app

_BRANCH = "feature-gh-7-ledger"
_LINE = "Components: ledger (.dmx/components/ledger/), payments (.dmx/components/payments/)"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _ticket(root: Path, components: str = "[payments, ledger]") -> None:
    _git(root, "checkout", "-q", "-b", _BRANCH)
    folder = root / ".dmx" / "jobs" / "GH-7"
    folder.mkdir(parents=True)
    (folder / "spec.md").write_text(
        f"---\nticket: GH-7\nbranch: {_BRANCH}\ncomponents: {components}\n---\n# Spec\n",
        encoding="utf-8",
    )


async def _call(client: Client, tool: str, root: Path, **kwargs: Any) -> Any:
    result = await client.call_tool(tool, {"workspace_root": str(root), **kwargs})
    return result.data


class TestComponentsLine:
    @pytest.mark.asyncio
    async def test_first_line_of_every_loop_message(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo)

        async with Client(create_app()) as client:
            started = await _call(client, "run_loop", reference_monorepo, name="dev")
            status = await _call(client, "loop_status", reference_monorepo)
            advanced = await _call(
                client,
                "loop_advance",
                reference_monorepo,
                skill="implement-next-phase",
                output="phase 1 done",
            )
            continued = await _call(client, "loop_continue", reference_monorepo)

        for message in (started, status, advanced, continued):
            assert message.splitlines()[0] == _LINE

    @pytest.mark.asyncio
    async def test_a_correction_shows_on_the_next_message(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            before = await _call(client, "run_loop", reference_monorepo, name="dev")
            spec = reference_monorepo / ".dmx" / "jobs" / "GH-7" / "spec.md"
            spec.write_text(
                spec.read_text(encoding="utf-8").replace("[ledger]", "[ledger, billing-ui]"),
                encoding="utf-8",
            )
            after = await _call(client, "loop_status", reference_monorepo)

        assert before.splitlines()[0] == "Components: ledger (.dmx/components/ledger/)"
        assert after.splitlines()[0] == (
            "Components: billing-ui (.dmx/components/billing-ui/), ledger (.dmx/components/ledger/)"
        )

    @pytest.mark.asyncio
    async def test_a_ticket_with_no_components(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "[]")

        async with Client(create_app()) as client:
            message = await _call(client, "loop_status", reference_monorepo)

        assert message.splitlines()[0] == "Components: none (repo-wide)"

    @pytest.mark.asyncio
    async def test_a_chat_opened_in_a_service_folder(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)

        async with Client(create_app()) as client:
            message = await client.call_tool(
                "loop_status",
                {"workspace_root": str(reference_monorepo / "services" / "payments")},
            )

        assert message.data.splitlines()[0] == ("Components: payments (.dmx/components/payments/)")

    @pytest.mark.asyncio
    async def test_no_line_without_components(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / ".dmx").mkdir()
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")

        async with Client(create_app()) as client:
            message = await _call(client, "loop_status", tmp_path)

        assert "Components:" not in message

    @pytest.mark.asyncio
    async def test_a_broken_component_is_named_once(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo)
        broken = reference_monorepo / ".dmx" / "components" / "fraud"
        broken.mkdir()
        (broken / "component.yaml").write_text("paths: [services/fraud]\n", encoding="utf-8")

        async with Client(create_app()) as client:
            first = await _call(client, "loop_status", reference_monorepo)
            second = await _call(client, "loop_status", reference_monorepo)

        note = "Skipped `.dmx/components/fraud/component.yaml`: `description` is missing."
        assert note in first
        assert first.splitlines()[0] == _LINE
        assert note not in second


class TestGetContext:
    @pytest.mark.asyncio
    async def test_a_ticket_in_the_reference_monorepo(self, reference_monorepo: Path) -> None:
        _ticket(reference_monorepo, "[ledger]")
        (reference_monorepo / "services/payments/fees.go").write_text(
            "package payments\n", encoding="utf-8"
        )
        (reference_monorepo / "Makefile").write_text("test:\n", encoding="utf-8")

        async with Client(create_app()) as client:
            context = await _call(client, "get_context", reference_monorepo)

        assert context["job_id"] == "GH-7"
        assert context["has_components"] is True
        assert context["components"] == [
            {
                "id": "ledger",
                "reason": "spec",
                "folder": ".dmx/components/ledger/",
                "description": "Ledger service, balances in minor units",
                "depends_on": [],
            },
            {
                "id": "payments",
                "reason": "changed files",
                "folder": ".dmx/components/payments/",
                "description": "Payment processing service",
                "depends_on": ["ledger"],
            },
        ]
        assert context["repo_wide_files"] == ["Makefile"]
        assert context["memory_files"] == [
            ".dmx/systemPatterns.md",
            ".dmx/components/ledger/systemPatterns.md",
        ]
        assert context["ticket_files"] == [
            ".dmx/jobs/GH-7/spec.md",
            ".dmx/jobs/GH-7/tasks.md",
            ".dmx/jobs/GH-7/activeContext.md",
        ]
        assert {v["name"] for v in context["validators"]} >= {"run_tests", "check_pr_ready"}
        assert context["waiting_learnings"] == []

    @pytest.mark.asyncio
    async def test_without_components_it_lists_the_repo_wide_files(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        (dmx / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        for name in ("techContext.md", "projectbrief.md", "glossary.md"):
            (dmx / name).write_text("# x\n", encoding="utf-8")
        (tmp_path / ".dmx" / "skills").mkdir()
        (tmp_path / ".dmx" / "skills" / "helm.md").write_text(
            "---\nname: helm\ndescription: Bump the chart\n---\nDo it.\n", encoding="utf-8"
        )

        async with Client(create_app()) as client:
            context = await _call(client, "get_context", tmp_path)

        assert context["has_components"] is False
        assert context["components_line"] is None
        assert context["components"] == []
        assert context["memory_files"] == [
            ".dmx/projectbrief.md",
            ".dmx/techContext.md",
            ".dmx/glossary.md",
        ]
        assert context["skills"] == [
            {
                "name": "helm",
                "source": "app repo",
                "path": ".dmx/skills/helm.md",
                "description": "Bump the chart",
            }
        ]


_SKILLS = Path(__file__).parent.parent / "src" / "dmx" / "skills"


class TestRuleAndSkills:
    def test_the_rule_reads_get_context_only_in_a_repo_with_components(self) -> None:
        rule = (Path(__file__).parent.parent / "src/dmx/rules/system-prompt.md").read_text(
            encoding="utf-8"
        )

        assert "I MUST read the repo-wide memory bank files" in rule
        paragraph = next(p for p in rule.split("\n\n") if p.startswith("**Components.**"))
        assert "When `.dmx/components/` exists" in paragraph
        assert "`get_context`" in paragraph
        assert "`memory_files`" in paragraph
        assert "`components` list in the ticket's `spec.md` frontmatter" in paragraph
        assert "When `.dmx/components/` does not exist, none of this applies." in paragraph

    @pytest.mark.parametrize(
        "skill",
        [
            "workflow/1-triage/dmx-create-ticket.md",
            "workflow/1-triage/dmx-derive-ticket.md",
            "workflow/1-triage/dmx-hotfix.md",
            "utility/dmx-create-branch.md",
        ],
    )
    def test_spec_writing_skills_record_components(self, skill: str) -> None:
        text = (_SKILLS / skill).read_text(encoding="utf-8")

        assert "components: [{component ids}]\n---" in text
        assert "ask the developer before writing the spec" in text
        assert "Leave the line out when the repo has no `.dmx/components/` folder." in text
