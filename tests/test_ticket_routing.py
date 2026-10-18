"""GH-92: send tickets to a Jira project or GitHub repo per component."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from dmx.server import create_app
from dmx.ticket_routing import (
    allowed_destinations,
    choose_destination,
    full_ref,
    parse_branch_ref,
    read_config_list,
    short_ref,
)

_JIRA = (
    "ticketing: jira\nbranch_base: main\nproject_key: DM\ncloud_id: c\n"
    "jira_projects: [LEDG, PAY, BILL]\n"
)
_GITHUB = (
    "ticketing: github-issues\nbranch_base: main\nowner: acme\nrepo: mono\n"
    "github_issue_repos: [acme/mono, acme/ledger-issues]\n"
)


def _repo(root: Path, config: str, components: dict[str, str] | None = None) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    (root / ".dmx").mkdir(parents=True, exist_ok=True)
    (root / ".dmx" / "config.md").write_text(config, encoding="utf-8")
    for cid, ticket in (components or {}).items():
        folder = root / ".dmx" / "components" / cid
        folder.mkdir(parents=True)
        (folder / "component.yaml").write_text(
            f"description: {cid}\npaths: ['{cid}/**']\n{ticket}", encoding="utf-8"
        )
    return root


class TestConfigList:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("jira_projects: [LEDG, PAY]", ["LEDG", "PAY"]),
            ('jira_projects: ["LEDG", "PAY"]  ', ["LEDG", "PAY"]),
            ("jira_projects: LEDG", ["LEDG"]),
            ("jira_projects:", None),
            ("jira_projects: {a: b}", None),
        ],
    )
    def test_forms(self, tmp_path: Path, line: str, expected: list[str] | None) -> None:
        _repo(tmp_path, f"ticketing: jira\n{line}\n")

        assert read_config_list(tmp_path, "jira_projects") == expected

    def test_a_missing_key(self, tmp_path: Path) -> None:
        _repo(tmp_path, "ticketing: jira\n")

        assert read_config_list(tmp_path, "jira_projects") is None


class TestAllowed:
    def test_jira_keeps_project_key_as_the_default(self, tmp_path: Path) -> None:
        allowed = allowed_destinations(_repo(tmp_path, _JIRA))

        assert (allowed.kind, allowed.default, allowed.allowed) == (
            "jira",
            "DM",
            ["DM", "LEDG", "PAY", "BILL"],
        )

    def test_github_keeps_owner_repo_as_the_default(self, tmp_path: Path) -> None:
        allowed = allowed_destinations(_repo(tmp_path, _GITHUB))

        assert (allowed.kind, allowed.default, allowed.allowed) == (
            "github-issues",
            "acme/mono",
            ["acme/mono", "acme/ledger-issues"],
        )

    def test_one_destination_as_today(self, tmp_path: Path) -> None:
        allowed = allowed_destinations(_repo(tmp_path, "ticketing: jira\nproject_key: DM\n"))

        assert allowed.allowed == ["DM"]


class TestChoose:
    def test_a_single_destination_never_asks(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, "ticketing: jira\nproject_key: DM\n")

        chosen = choose_destination(root)

        assert (chosen.target, chosen.reason, chosen.confirm) == ("DM", "repo", False)

    def test_no_ticketing(self, tmp_path: Path) -> None:
        chosen = choose_destination(_repo(tmp_path, "ticketing: none\n"))

        assert (chosen.kind, chosen.target, chosen.confirm) == ("none", None, False)

    def test_named_by_the_developer(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _JIRA, {"ledger": "ticket: {jira_project: LEDG}\n"})

        chosen = choose_destination(root, requested="bill", components=["ledger"])

        assert (chosen.target, chosen.reason, chosen.confirm) == ("BILL", "named", False)

    def test_a_named_destination_must_be_allowed(self, tmp_path: Path) -> None:
        chosen = choose_destination(_repo(tmp_path, _JIRA), requested="OPS")

        assert chosen.blocked == (
            "`OPS` is not an allowed Jira project. Allowed: DM, LEDG, PAY, BILL "
            "(jira_projects in .dmx/config.md)."
        )

    def test_a_component_default_asks_first(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _JIRA, {"ledger": "ticket: {jira_project: LEDG}\n"})

        chosen = choose_destination(root, components=["ledger"])

        assert (chosen.target, chosen.reason, chosen.confirm) == ("LEDG", "component", True)
        assert chosen.question == "Create the ticket in LEDG (default for ledger)?"

    def test_the_repo_default_asks_when_there_are_others(self, tmp_path: Path) -> None:
        chosen = choose_destination(_repo(tmp_path, _JIRA))

        assert (chosen.target, chosen.reason, chosen.confirm) == ("DM", "repo", True)
        assert chosen.question == (
            "Create the ticket in DM (the repo default)? Other options: LEDG, PAY, BILL."
        )

    def test_components_that_disagree(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path,
            _JIRA,
            {
                "ledger": "ticket: {jira_project: LEDG}\n",
                "payments": "ticket: {jira_project: PAY}\n",
            },
        )

        chosen = choose_destination(root, components=["ledger", "payments"])

        assert chosen.target is None
        assert chosen.confirm is True
        assert chosen.options == [
            {"target": "LEDG", "labels": [], "components": ["ledger"]},
            {"target": "PAY", "labels": [], "components": ["payments"]},
        ]
        assert chosen.question == (
            "This ticket's components have different defaults: LEDG (ledger), PAY (payments). "
            "Which one?"
        )

    def test_an_invalid_component_default_is_ignored(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _JIRA, {"ledger": "ticket: {jira_project: LEDGR}\n"})

        chosen = choose_destination(root, components=["ledger"])

        assert (chosen.target, chosen.reason) == ("DM", "repo")
        assert chosen.notes == [
            "ledger's ticket.jira_project `LEDGR` is not in jira_projects; ignored."
        ]

    def test_github_labels_and_repo(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path,
            _GITHUB,
            {
                "ledger": (
                    "ticket: {github_repo: acme/ledger-issues, github_labels: [area/ledger]}\n"
                ),
                "billing": "ticket: {github_labels: [area/billing]}\n",
            },
        )

        ledger = choose_destination(root, components=["ledger"])
        billing = choose_destination(root, components=["billing"])

        assert (ledger.target, ledger.labels) == ("acme/ledger-issues", ["area/ledger"])
        assert ledger.question == (
            "Open the issue in acme/ledger-issues labelled area/ledger (default for ledger)?"
        )
        assert (billing.target, billing.labels) == ("acme/mono", ["area/billing"])


class TestReferences:
    def test_the_default_repo_keeps_gh_refs(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _GITHUB)

        assert short_ref(root, "acme/mono", 12) == "gh-12"
        assert full_ref(root, "acme/mono", 12) == "#12"

    def test_another_repo_is_qualified(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _GITHUB)

        assert short_ref(root, "acme/ledger-issues", 12) == "ledger-issues-12"
        assert full_ref(root, "acme/ledger-issues", 12) == "acme/ledger-issues#12"

    @pytest.mark.parametrize(
        ("branch", "expected"),
        [
            ("feature-gh-12-add-thing", ("acme/mono", 12)),
            ("feature-ledger-issues-7-fix-rounding", ("acme/ledger-issues", 7)),
            ("bug-ledger-issues-7", ("acme/ledger-issues", 7)),
            ("feature-add-ledger-issues-page", None),
            ("feature-thigh-12-strap", None),
            ("feature-gh-12abc", None),
            ("main", None),
        ],
    )
    def test_reading_a_branch_back(
        self, tmp_path: Path, branch: str, expected: tuple[str, int] | None
    ) -> None:
        root = _repo(tmp_path, _GITHUB)

        assert parse_branch_ref(root, branch) == expected


class TestTool:
    @pytest.mark.asyncio
    async def test_the_tool_uses_the_tickets_components(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _JIRA, {"ledger": "ticket: {jira_project: LEDG}\n"})
        for args in (
            [
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=T",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "init",
            ],
            ["checkout", "-q", "-b", "feature-x"],
        ):
            subprocess.run(["git", *args], cwd=root, check=True)
        spec = root / ".dmx" / "jobs" / "feature-x" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("---\nbranch: feature-x\ncomponents: [ledger]\n---\n", encoding="utf-8")

        async with Client(create_app()) as client:
            result = await client.call_tool("ticket_destination", {"workspace_root": str(root)})

        data: dict[str, Any] = result.data
        assert data["target"] == "LEDG"
        assert data["reason"] == "component"
        assert data["confirm"] is True

    @pytest.mark.asyncio
    async def test_github_reference_forms_in_the_reply(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, _GITHUB)

        async with Client(create_app()) as client:
            result = await client.call_tool(
                "ticket_destination",
                {"requested": "acme/ledger-issues", "workspace_root": str(root)},
            )

        data: dict[str, Any] = result.data
        assert data["short_ref"] == "ledger-issues-{number}"
        assert data["full_ref"] == "acme/ledger-issues#{number}"
        assert data["branch_prefixes"] == {"gh": "acme/mono", "ledger-issues": "acme/ledger-issues"}


_SKILLS = Path(__file__).parent.parent / "src" / "dmx" / "skills"


class TestSkills:
    @pytest.mark.parametrize(
        "skill",
        ["workflow/1-triage/dmx-create-ticket.md", "workflow/1-triage/dmx-derive-ticket.md"],
    )
    def test_creating_a_ticket_asks_ticket_destination(self, skill: str) -> None:
        text = (_SKILLS / skill).read_text(encoding="utf-8")

        assert "call `ticket_destination` on `user-dmx`" in text
        assert "If `confirm` is true, ask its `question` and wait" in text
        assert "add `issue_repo: {target}` to the spec frontmatter" in text
        assert "Otherwise everything below applies unchanged." in text

    @pytest.mark.parametrize(
        "skill", ["utility/dmx-create-branch.md", "workflow/1-triage/dmx-hotfix.md"]
    )
    def test_linking_a_ticket_in_another_repo(self, skill: str) -> None:
        text = (_SKILLS / skill).read_text(encoding="utf-8")

        assert "**Issue in another repo** (GH-92)" in text
        assert "A linked ticket keeps its own reference" in text

    @pytest.mark.parametrize(
        "skill",
        [
            "workflow/5-ship/dmx-create-pr.md",
            "utility/dmx-draft-pr-description.md",
            "utility/dmx-commit.md",
            "workflow/2-plan/dmx-plan.md",
            "workflow/5-ship/dmx-close-ticket.md",
        ],
    )
    def test_reading_a_ticket_in_another_repo(self, skill: str) -> None:
        text = (_SKILLS / skill).read_text(encoding="utf-8")

        assert "`issue_repo` (GH-92)" in text
        assert "`Closes acme/ledger-issues#7`" in text

    def test_release_notes_and_status_cover_every_project(self) -> None:
        notes = (_SKILLS / "workflow/6-release/dmx-draft-release-note.md").read_text(
            encoding="utf-8"
        )
        status = (_SKILLS / "utility/dmx-status.md").read_text(encoding="utf-8")

        assert "every project in `jira_projects`" in notes
        assert "each other repo's branch prefix" in notes
        assert "project in ({config.project_key} and every project in jira_projects" in status
        assert "do the same for each listed repo" in status
