"""Exception hierarchy for dmx."""

from __future__ import annotations

__all__ = [
    "DmxError",
    "SkillLoadError",
    "RuleLoadError",
    "EmitterError",
    "IdeDetectionError",
    "WorkspaceRootInvalid",
]


class DmxError(Exception):
    """Base exception for all dmx errors."""


class SkillLoadError(DmxError):
    """Raised when a skill file cannot be parsed or loaded.

    Attributes:
        path: The path to the skill file that failed to load.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class RuleLoadError(DmxError):
    """Raised when a rule file cannot be parsed or loaded.

    Attributes:
        path: The path to the rule file that failed to load.
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class EmitterError(DmxError):
    """Raised when rule file generation fails for an IDE target.

    Attributes:
        ide: The IDE target that caused the failure.
    """

    def __init__(self, message: str, ide: str | None = None) -> None:
        super().__init__(message)
        self.ide = ide


class IdeDetectionError(DmxError):
    """Raised when IDE detection produces an unresolvable state."""


class WorkspaceRootInvalid(DmxError):
    """Raised when a resolved workspace root fails validation.

    Attributes:
        path: The resolved (but rejected) path.
        reason: Human-readable explanation for why it was rejected.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"'{path}': {reason}")
        self.path = path
        self.reason = reason
