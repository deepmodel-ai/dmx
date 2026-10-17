"""GH-86: component folders under ``.dmx/components/`` and which files they own."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dmx.components import has_components, load_components, path_matches

if TYPE_CHECKING:
    from pathlib import Path


def _component(root: Path, component_id: str, body: str) -> None:
    folder = root / ".dmx" / "components" / component_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "component.yaml").write_text(body, encoding="utf-8")


class TestLoading:
    def test_the_reference_monorepo(self, reference_monorepo: Path) -> None:
        components = load_components(reference_monorepo)

        assert components.ids == (
            "billing-ui",
            "hasher",
            "ledger",
            "lua-lint",
            "payments",
            "pyutils",
        )
        assert components.skipped == ()
        payments = components.get("payments")
        assert payments is not None
        assert payments.description == "Payment processing service"
        assert payments.paths == ("services/payments/**",)
        assert payments.depends_on == ("ledger",)
        assert payments.folder == ".dmx/components/payments/"
        lua = components.get("lua-lint")
        assert lua is not None
        assert lua.test == {"cmd": "./test.sh", "cwd": "tools/lua-lint"}

    def test_no_components_folder(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir()

        assert has_components(tmp_path) is False
        assert load_components(tmp_path).components == ()

    def test_unknown_keys_are_ignored(self, tmp_path: Path) -> None:
        _component(tmp_path, "ledger", "description: Ledger\npaths: [ledger]\nowner: '@acme'\n")

        assert load_components(tmp_path).ids == ("ledger",)

    def test_an_unknown_dependency_is_kept_as_written(self, tmp_path: Path) -> None:
        _component(tmp_path, "payments", "description: P\npaths: [p]\ndepends_on: [ledgr]\n")

        payments = load_components(tmp_path).get("payments")
        assert payments is not None
        assert payments.depends_on == ("ledgr",)

    def test_a_folder_without_component_yaml_is_not_a_component(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx" / "components" / "notes").mkdir(parents=True)

        loaded = load_components(tmp_path)

        assert loaded.components == ()
        assert loaded.skipped == ()

    @pytest.mark.parametrize(
        ("folder", "body", "reason"),
        [
            ("ledger", "description: [unclosed\n", "not valid YAML"),
            ("ledger", "- just\n- a list\n", "not a mapping"),
            ("ledger", "paths: [ledger]\n", "`description` is missing"),
            ("ledger", "description: L\n", "`paths` is missing"),
            ("ledger", "description: L\npaths: []\n", "`paths` is empty"),
            ("ledger", "description: L\npaths: ledger/**\n", "`paths` must be a list"),
            ("ledger", "description: L\npaths: [../outside]\n", "`..`"),
            ("ledger", "description: L\npaths: [x]\ndepends_on: ledger\n", "`depends_on`"),
            ("ledger", "description: L\npaths: [x]\ntest: make test\n", "`test`"),
            ("ledger", "description: L\npaths: [x]\ntest: {cwd: x}\n", "`test.cmd`"),
            ("ledger", "description: L\npaths: [x]\nticket: PAY\n", "`ticket`"),
            ("ledger", "description: L\npaths: [x]\nvalidators: [a]\n", "`validators`"),
            ("Ledger", "description: L\npaths: [x]\n", "not a valid component id"),
        ],
    )
    def test_a_broken_file_is_skipped_with_its_reason(
        self, tmp_path: Path, folder: str, body: str, reason: str
    ) -> None:
        _component(tmp_path, "good", "description: G\npaths: [good]\n")
        _component(tmp_path, folder, body)

        loaded = load_components(tmp_path)

        assert loaded.ids == ("good",)
        [skipped] = loaded.skipped
        assert skipped.path == f".dmx/components/{folder}/component.yaml"
        assert reason in skipped.reason


class TestPathMatching:
    @pytest.mark.parametrize(
        ("pattern", "path", "matches"),
        [
            ("services/payments/**", "services/payments/api/main.go", True),
            ("services/payments/**", "services/payments/go.mod", True),
            ("services/payments/**", "services/payments-sdk/x.go", False),
            ("services/payments", "services/payments/main.go", True),
            ("services/payments/", "services/payments/main.go", True),
            ("/services/payments", "services/payments/main.go", True),
            ("services/payments", "services/paymentsx/main.go", False),
            ("services/*/api/**", "services/payments/api/h.go", True),
            ("services/*/api/**", "services/payments/sub/api/h.go", False),
            ("services/**/api/**", "services/payments/sub/api/h.go", True),
            ("services/**/api/**", "services/api/h.go", True),
            ("services/payments-*", "services/payments-sdk/x.go", True),
            ("**/*.proto", "protos/ledger/v1/ledger.proto", True),
            ("libs/py?tils/**", "libs/pyutils/x.py", True),
            ("libs/py?tils/**", "libs/py/tils/x.py", False),
        ],
    )
    def test_globs(self, pattern: str, path: str, matches: bool) -> None:
        assert path_matches(pattern, path) is matches

    def test_files_map_to_components(self, reference_monorepo: Path) -> None:
        components = load_components(reference_monorepo)

        owner = components.component_for("services/payments/payments.go")
        assert owner is not None
        assert owner.id == "payments"
        assert components.component_for("go.mod") is None
        assert components.component_for(".github/workflows/ci.yml") is None

    def test_the_most_specific_nested_path_wins(self, tmp_path: Path) -> None:
        _component(tmp_path, "payments", "description: P\npaths: ['services/payments/**']\n")
        _component(tmp_path, "sdk", "description: S\npaths: ['services/payments/sdk/**']\n")
        _component(tmp_path, "everything", "description: E\npaths: ['**']\n")
        components = load_components(tmp_path)

        def owner(path: str) -> str | None:
            found = components.component_for(path)
            return found.id if found else None

        assert owner("services/payments/sdk/client.go") == "sdk"
        assert owner("services/payments/api.go") == "payments"
        assert owner("README.md") == "everything"

    def test_a_tie_goes_to_the_first_id(self, tmp_path: Path) -> None:
        _component(tmp_path, "beta", "description: B\npaths: ['shared/**']\n")
        _component(tmp_path, "alpha", "description: A\npaths: ['shared/**']\n")

        owner = load_components(tmp_path).component_for("shared/x.py")

        assert owner is not None
        assert owner.id == "alpha"

    def test_a_longer_pattern_beats_a_shorter_one_at_the_same_depth(self, tmp_path: Path) -> None:
        _component(tmp_path, "any", "description: A\npaths: ['services/*']\n")
        _component(tmp_path, "pay", "description: P\npaths: ['services/pay*']\n")

        owner = load_components(tmp_path).component_for("services/payments/x.go")

        assert owner is not None
        assert owner.id == "pay"

    def test_components_for_splits_out_repo_wide_files(self, reference_monorepo: Path) -> None:
        components = load_components(reference_monorepo)

        owners, repo_wide = components.components_for(
            ["services/ledger/ledger.go", "Makefile", "services/ledger/ledger_test.go", "go.mod"]
        )

        assert [component.id for component in owners] == ["ledger"]
        assert repo_wide == ("Makefile", "go.mod")
