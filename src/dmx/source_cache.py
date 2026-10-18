"""Fetching locked shared sources into the git-ignored cache (GH-85).

A repo commits only ``.dmx/shared-sources.lock``: the commit and a content
hash for each source. The content lives in two caches:

- a bare git mirror per remote in ``$XDG_CACHE_HOME/dmx/git/`` (default
  ``~/.cache/dmx/git/``), shared by every worktree and repo on the machine,
  so a commit is downloaded once;
- the source's tree at the locked commit, extracted with ``git archive``
  into ``.dmx/.cache/<repo>@<commit>/`` inside the repo. It stays inside the
  repo because folder-shaped skills hand the agent a path to read
  (``get_skill_definition``), and some IDEs block reads outside the
  workspace. It is keyed by repo and commit, so two sources naming the same
  repo at one commit share it.

Each extracted tree carries a small ``.dmx-verified`` record: the hash it
was checked against and the newest file mtime at that point. A later load
re-hashes only when a file is newer than that record, so loads stay cheap
while a tampered or half-written tree is still caught.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dmx.shared_sources import SharedSourceError

if TYPE_CHECKING:
    from dmx.shared_sources import LockEntry, SharedSource

__all__ = [
    "SharedSourceFetchError",
    "cache_home",
    "cached_tree",
    "ensure_fetched",
    "extract_commit",
    "repo_slug",
    "resolve_commit",
    "tree_hash",
]

# Network-bound, like the clone in the 0.5 vendoring, so a generous but
# bounded budget: a hung fetch can't hang an MCP tool call indefinitely.
_GIT_TIMEOUT_SECONDS = 120

VERIFIED_RECORD = ".dmx-verified"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_SLUG = 48


class SharedSourceFetchError(SharedSourceError):
    """A locked shared source could not be fetched or did not match its lock."""


def cache_home() -> Path:
    """``$XDG_CACHE_HOME/dmx``, or ``~/.cache/dmx``."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "dmx"


def repo_slug(url: str) -> str:
    """A filesystem-safe name for a remote: its host and path.

    ``https://github.com/acme/skills.git`` and ``git@github.com:acme/skills``
    both become ``github.com-acme-skills``.
    """
    text = url.strip()
    text = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)
    text = text.replace(":", "/").removesuffix("/").removesuffix(".git")
    slug = _SLUG_RE.sub("-", text.replace("/", "-")).strip("-.") or "source"
    if len(slug) > _MAX_SLUG:
        # A long address, such as a local path, would push the mirror and
        # cache folders past Windows' 260-character path limit. Keep the
        # readable end and add a hash of the whole address so it stays unique.
        digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:10]
        slug = f"{slug[-(_MAX_SLUG - 11) :].strip('-.')}-{digest}"
    return slug


def remove_tree(path: Path) -> None:
    """``shutil.rmtree`` that also removes read-only files.

    Windows refuses to delete a read-only file, and git marks its object
    files read-only, as a source tree's own files may be.
    """

    def clear_and_retry(function: Any, failed: str, _info: Any) -> None:
        os.chmod(failed, stat.S_IWRITE)
        function(failed)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


def _discard(path: Path) -> None:
    """Remove a half-written mirror or tree, if there is one."""
    with contextlib.suppress(OSError):
        remove_tree(path)


def cached_tree(workspace_root: Path, url: str, commit: str) -> Path:
    """``.dmx/.cache/<repo>@<commit>/`` for one source at one commit."""
    return workspace_root / ".dmx" / ".cache" / f"{repo_slug(url)}@{commit}"


def _mirror_dir(url: str) -> Path:
    return cache_home() / "git" / f"{repo_slug(url)}.git"


def _git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(exc.cmd, 124, "", f"timed out after {exc.timeout}s")


def _fetch_error(source: SharedSource, what: str, detail: str) -> SharedSourceFetchError:
    return SharedSourceFetchError(
        f"Could not fetch shared source `{source.name}` {what} from {source.url}: "
        f"{detail.strip() or 'git failed'}. Check that this machine's git credentials can "
        "read it (for GitHub, `gh auth login`, an SSH key, or a token), then retry."
    )


def _ensure_mirror(source: SharedSource) -> Path:
    mirror = _mirror_dir(source.url)
    if (mirror / "HEAD").is_file():
        return mirror
    mirror.parent.mkdir(parents=True, exist_ok=True)
    partial = mirror.with_name(f"{mirror.name}.tmp-{os.getpid()}")
    _discard(partial)
    cloned = _git(["clone", "--mirror", "--quiet", source.url, str(partial)])
    if cloned.returncode != 0:
        _discard(partial)
        raise _fetch_error(source, "", cloned.stderr or cloned.stdout)
    try:
        partial.rename(mirror)
    except OSError:
        # Another process finished the same mirror first.
        _discard(partial)
    return mirror


def _has_commit(mirror: Path, commit: str) -> bool:
    return _git(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=mirror).returncode == 0


def resolve_commit(source: SharedSource) -> str:
    """Look up the commit *source*'s ``ref`` points to now, on its remote."""
    mirror = _ensure_mirror(source)
    fetched = _git(["fetch", "--quiet", "--prune", "--tags", "origin"], cwd=mirror)
    if fetched.returncode != 0:
        raise _fetch_error(source, "", fetched.stderr or fetched.stdout)
    parsed = _git(["rev-parse", "--verify", "--quiet", f"{source.ref}^{{commit}}"], cwd=mirror)
    if parsed.returncode != 0 or not parsed.stdout.strip():
        raise SharedSourceFetchError(
            f"Ref `{source.ref}` not found in shared source `{source.name}` ({source.url}). "
            "It may have been deleted or renamed; check it exists upstream and update "
            "`.dmx/shared-sources.yaml`."
        )
    return parsed.stdout.strip()


def _ensure_commit(source: SharedSource, commit: str) -> Path:
    mirror = _ensure_mirror(source)
    if _has_commit(mirror, commit):
        return mirror
    for args in (["fetch", "--quiet", "--tags", "origin"], ["fetch", "--quiet", "origin", commit]):
        _git(args, cwd=mirror)
        if _has_commit(mirror, commit):
            return mirror
    raise _fetch_error(source, f"at {commit[:12]}", "that commit is not on the remote")


def _ensure_cache_root(workspace_root: Path) -> Path:
    """``.dmx/.cache/``, ignoring itself so ``git add .dmx/`` never commits it."""
    root = workspace_root / ".dmx" / ".cache"
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    if not ignore.is_file():
        ignore.write_text("# dmx shared-source cache — never committed.\n*\n", encoding="utf-8")
    return root


def extract_commit(
    workspace_root: Path, source: SharedSource, commit: str, *, fresh: bool = False
) -> Path:
    """Extract *source*'s whole tree at *commit* into the repo cache.

    A no-op when that tree is already extracted, unless *fresh* is set: then
    any cached copy is replaced, so its hash can be trusted for a lock.
    Returns its path.
    """
    target = cached_tree(workspace_root, source.url, commit)
    if target.is_dir():
        if not fresh:
            return target
        remove_tree(target)
    mirror = _ensure_commit(source, commit)
    _ensure_cache_root(workspace_root)
    partial = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    _discard(partial)
    partial.mkdir()
    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", commit],
        cwd=mirror,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert archive.stdout is not None
        with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(partial, filter="data")
            else:  # pragma: no cover — Python 3.11 before 3.11.4
                tar.extractall(partial)  # noqa: S202 — git archive output of a locked commit
    except (tarfile.TarError, OSError) as exc:
        archive.kill()
        _discard(partial)
        raise _fetch_error(source, f"at {commit[:12]}", str(exc)) from exc
    finally:
        _out, err = archive.communicate()
    if archive.returncode != 0:
        _discard(partial)
        raise _fetch_error(source, f"at {commit[:12]}", err.decode("utf-8", "replace"))
    try:
        partial.rename(target)
    except OSError:
        _discard(partial)
    return target


def _files(tree: Path) -> list[Path]:
    return sorted(
        path for path in tree.rglob("*") if path.is_file() and path.name != VERIFIED_RECORD
    )


def tree_hash(tree: Path) -> str:
    """``sha256:`` over every file's relative path and content, in path order."""
    digest = hashlib.sha256()
    for path in _files(tree):
        digest.update(path.relative_to(tree).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _newest_mtime(tree: Path) -> float:
    return max((path.stat().st_mtime for path in _files(tree)), default=0.0)


def _record(tree: Path, expected: str) -> None:
    payload = {"hash": expected, "newest_mtime": _newest_mtime(tree)}
    (tree / VERIFIED_RECORD).write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _still_verified(tree: Path, expected: str) -> bool:
    try:
        payload = json.loads((tree / VERIFIED_RECORD).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("hash") != expected:
        return False
    recorded = payload.get("newest_mtime")
    return isinstance(recorded, int | float) and _newest_mtime(tree) <= recorded


def ensure_fetched(workspace_root: Path, source: SharedSource, entry: LockEntry) -> Path:
    """The cached tree for *source* at its locked commit, fetched if needed.

    The tree must hash to the lock's ``hash``. A cached tree that doesn't is
    deleted and fetched again; if the fresh copy still doesn't match, the
    lock and the remote disagree and this raises.

    Raises:
        SharedSourceFetchError: The fetch failed, or the content does not
            match the lock.
    """
    tree = cached_tree(workspace_root, source.url, entry.commit)
    if tree.is_dir() and _still_verified(tree, entry.hash):
        return tree
    if tree.is_dir() and tree_hash(tree) == entry.hash:
        _record(tree, entry.hash)
        return tree
    if tree.is_dir():
        remove_tree(tree)
    tree = extract_commit(workspace_root, source, entry.commit)
    actual = tree_hash(tree)
    if actual != entry.hash:
        with contextlib.suppress(OSError):
            remove_tree(tree)
        raise SharedSourceFetchError(
            f"Shared source `{source.name}` at {entry.commit[:12]} does not match "
            f"`.dmx/shared-sources.lock` (expected {entry.hash}, got {actual}). "
            "Run `/dmx/update-shared-sources` to lock it again."
        )
    _record(tree, entry.hash)
    return tree
