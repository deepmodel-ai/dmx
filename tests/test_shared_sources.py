"""Tests for dmx.shared_sources — GH-27 phase 1: config parsing + path resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from dmx.shared_sources import (
    SharedSource,
    SharedSourceError,
    parse_source_address,
    read_shared_sources,
    source_root,
    vendor_dir,
    vendor_lock_path,
)

# ---------------------------------------------------------------------------
# parse_source_address
# ---------------------------------------------------------------------------


class TestParseSourceAddress:
    def test_url_and_ref_only(self) -> None:
        url, subdir, ref = parse_source_address(
            "git::https://github.com/acme/skills.git?ref=v1.0.0"
        )
        assert url == "https://github.com/acme/skills.git"
        assert subdir is None
        assert ref == "v1.0.0"

    def test_url_subdir_and_ref(self) -> None:
        url, subdir, ref = parse_source_address(
            "git::https://github.com/acme/skills.git//shared?ref=main"
        )
        assert url == "https://github.com/acme/skills.git"
        assert subdir == "shared"
        assert ref == "main"

    def test_commit_sha_ref(self) -> None:
        _, _, ref = parse_source_address("git::https://x//?ref=abc123def456")
        assert ref == "abc123def456"

    def test_empty_subdir_marker_is_none(self) -> None:
        # `//?ref=...` (empty subdir between the double-slash and `?`) means
        # repo root, same as omitting `//` entirely.
        _, subdir, _ = parse_source_address("git::https://x//?ref=v1")
        assert subdir is None

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(SharedSourceError, match="Invalid shared source address"):
            parse_source_address("https://github.com/acme/skills.git?ref=v1")

    def test_missing_ref_raises(self) -> None:
        with pytest.raises(SharedSourceError, match="missing `\\?ref="):
            parse_source_address("git::https://github.com/acme/skills.git")

    def test_missing_ref_with_subdir_raises(self) -> None:
        with pytest.raises(SharedSourceError, match="missing `\\?ref="):
            parse_source_address("git::https://github.com/acme/skills.git//shared")

    def test_subdir_starting_with_slash_raises(self) -> None:
        # Path("x") / "/etc" resolves to the absolute "/etc", silently
        # discarding "x" entirely — must be rejected before it ever reaches
        # source_root()/checked_out_root's `Path.__truediv__`.
        with pytest.raises(SharedSourceError, match="cannot start with `/`"):
            parse_source_address("git::https://x///etc?ref=v1")

    def test_subdir_with_dotdot_segment_raises(self) -> None:
        with pytest.raises(SharedSourceError, match="`\\.\\.` segment"):
            parse_source_address("git::https://x//../../etc?ref=v1")

    def test_subdir_with_dotdot_in_middle_raises(self) -> None:
        with pytest.raises(SharedSourceError):
            parse_source_address("git::https://x//shared/../../etc?ref=v1")


# ---------------------------------------------------------------------------
# read_shared_sources
# ---------------------------------------------------------------------------


def _write(root: Path, content: str) -> None:
    path = root / ".dmx" / "shared-sources.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestReadSharedSources:
    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        assert read_shared_sources(tmp_path) == []

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        _write(tmp_path, "")
        assert read_shared_sources(tmp_path) == []

    def test_parses_declared_order(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            """\
shared_sources:
  - name: team-frontend
    source: "git::https://github.com/acme/dmx-frontend-skills.git//?ref=v2.1.0"
  - name: org-wide
    source: "git::https://github.com/acme/dmx-shared.git//?ref=v1.4.0"
""",
        )
        sources = read_shared_sources(tmp_path)
        assert [s.name for s in sources] == ["team-frontend", "org-wide"]
        assert sources[0] == SharedSource(
            name="team-frontend",
            source="git::https://github.com/acme/dmx-frontend-skills.git//?ref=v2.1.0",
            url="https://github.com/acme/dmx-frontend-skills.git",
            subdir=None,
            ref="v2.1.0",
        )

    def test_not_a_mapping_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(SharedSourceError, match="must be a mapping"):
            read_shared_sources(tmp_path)

    def test_shared_sources_not_a_list_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "shared_sources: not-a-list\n")
        with pytest.raises(SharedSourceError, match="must be a list"):
            read_shared_sources(tmp_path)

    def test_entry_missing_name_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, 'shared_sources:\n  - source: "git::https://x?ref=v1"\n')
        with pytest.raises(SharedSourceError, match="name.*source"):
            read_shared_sources(tmp_path)

    def test_entry_missing_source_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "shared_sources:\n  - name: acme\n")
        with pytest.raises(SharedSourceError, match="name.*source"):
            read_shared_sources(tmp_path)

    def test_duplicate_name_raises(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            """\
shared_sources:
  - name: acme
    source: "git::https://x//a?ref=v1"
  - name: acme
    source: "git::https://y//b?ref=v1"
""",
        )
        with pytest.raises(SharedSourceError, match="duplicate shared source name"):
            read_shared_sources(tmp_path)

    def test_invalid_source_address_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "shared_sources:\n  - name: acme\n    source: not-a-valid-address\n")
        with pytest.raises(SharedSourceError, match="Invalid shared source address"):
            read_shared_sources(tmp_path)

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        _write(tmp_path, "shared_sources: [unterminated\n")
        with pytest.raises(SharedSourceError, match="Could not parse"):
            read_shared_sources(tmp_path)

    @pytest.mark.parametrize(
        "name",
        [
            "../../etc",  # path traversal — the primary risk this guards against
            "acme/skills",  # embedded path separator
            "-leading-hyphen",  # would look like a CLI flag if ever shelled out
            "<app repo>",  # the detect_collisions sentinel — must never be a real name
            "has space",
            "",
        ],
    )
    def test_invalid_name_raises(self, tmp_path: Path, name: str) -> None:
        _write(
            tmp_path, f'shared_sources:\n  - name: "{name}"\n    source: "git::https://x?ref=v1"\n'
        )
        with pytest.raises(SharedSourceError, match="invalid shared source name"):
            read_shared_sources(tmp_path)

    @pytest.mark.parametrize("name", ["acme", "org-wide", "team_frontend", "acme2"])
    def test_valid_names_pass(self, tmp_path: Path, name: str) -> None:
        _write(
            tmp_path, f'shared_sources:\n  - name: "{name}"\n    source: "git::https://x?ref=v1"\n'
        )
        sources = read_shared_sources(tmp_path)
        assert sources[0].name == name


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_vendor_dir(self, tmp_path: Path) -> None:
        assert vendor_dir(tmp_path, "acme") == tmp_path / ".dmx" / "vendor" / "acme"

    def test_vendor_lock_path(self, tmp_path: Path) -> None:
        assert vendor_lock_path(tmp_path, "acme") == (
            tmp_path / ".dmx" / "vendor" / "acme" / ".lock.json"
        )

    def test_source_root_without_subdir(self, tmp_path: Path) -> None:
        source = SharedSource(
            name="acme", source="git::https://x?ref=v1", url="https://x", subdir=None, ref="v1"
        )
        assert source_root(tmp_path, source) == tmp_path / ".dmx" / "vendor" / "acme"

    def test_source_root_with_subdir(self, tmp_path: Path) -> None:
        source = SharedSource(
            name="acme",
            source="git::https://x//shared?ref=v1",
            url="https://x",
            subdir="shared",
            ref="v1",
        )
        assert source_root(tmp_path, source) == tmp_path / ".dmx" / "vendor" / "acme" / "shared"
