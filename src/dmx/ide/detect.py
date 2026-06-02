"""IDE detection: resolve which IDE is making the current MCP request."""

from __future__ import annotations

import logging
import os
import re

from dmx.exceptions import IdeDetectionError

__all__ = ["resolve_ide_targets"]

logger = logging.getLogger(__name__)

# Agents target has no standard client name; it is set explicitly via
# DMX_IDE env var or the `ides` argument to setup_ide_rules.  The patterns
# below cover the IDEs that have known, stable client name strings.
_CLIENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bcursor\b", re.IGNORECASE), "cursor"),
    (re.compile(r"\bantigravity\b", re.IGNORECASE), "antigravity"),
    (re.compile(r"claude|claude-code", re.IGNORECASE), "claude"),
    (re.compile(r"copilot|github copilot", re.IGNORECASE), "copilot"),
    (re.compile(r"windsurf", re.IGNORECASE), "windsurf"),
)


def resolve_ide_targets(
    explicit_ides: list[str] | None,
    client_name: str | None,
    ide_from_header: str | None,
) -> tuple[tuple[str, ...], str]:
    """Resolve the IDE targets for the current MCP request.

    Detection order:
    1. ``DMX_IDE`` / ``DEEPMODEL_IDE`` environment variable (comma-separated).
    2. ``X-Dmx-IDE`` HTTP header value.
    3. Explicit *explicit_ides* argument from the tool call.
    4. MCP ``clientInfo.name`` pattern matching.
    5. Fallback: empty tuple with source ``"unknown"``.

    Args:
        explicit_ides: IDE names passed explicitly by the caller (e.g. from
            a tool argument). May be ``None`` or empty.
        client_name: MCP ``clientInfo.name`` from the protocol handshake.
        ide_from_header: Value of the ``X-Dmx-IDE`` HTTP header, or ``None``
            for stdio transport.

    Returns:
        A ``(ides, source)`` tuple where *ides* is a tuple of canonical IDE
        identifiers and *source* describes how they were resolved.
    """
    # 1. Environment variable overrides everything.
    env_ide = os.environ.get("DMX_IDE") or os.environ.get("DEEPMODEL_IDE")
    if env_ide:
        ides = tuple(i.strip() for i in env_ide.split(",") if i.strip())
        logger.debug("IDE resolved from env var: %s", ides)
        return ides, "env"

    # 2. X-Dmx-IDE HTTP header.
    if ide_from_header:
        ides = tuple(i.strip() for i in ide_from_header.split(",") if i.strip())
        logger.debug("IDE resolved from header: %s", ides)
        return ides, "header"

    # 3. Explicit argument from the tool call.
    if explicit_ides is not None:
        ides = tuple(i.strip() for i in explicit_ides if i.strip())
        if ides:
            logger.debug("IDE resolved from explicit argument: %s", ides)
            return ides, "explicit"
        if explicit_ides:
            # Non-empty list provided but every entry was blank — programming error.
            raise IdeDetectionError(
                "explicit_ides contained only blank entries; "
                "check the caller is passing valid IDE identifiers"
            )

    # 4. clientInfo.name pattern matching.
    if client_name:
        for pattern, ide in _CLIENT_PATTERNS:
            if pattern.search(client_name):
                logger.debug("IDE resolved from clientInfo as %s: %s", ide, client_name)
                return (ide,), "client_info"

    logger.debug("IDE could not be determined; client_name=%r", client_name)
    return (), "unknown"
