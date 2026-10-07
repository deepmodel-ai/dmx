"""Workflow version — independent of the package version.

Bump this constant when skills or rules change in a way that requires
developers to refresh the IDE rule files copied into the repo.
``/dmx/upgrade`` rewrites those files. ``/dmx/init`` writes them too, and
also scaffolds the memory bank.

Do NOT bump for: bug fixes, new IDE emitters, CLI changes, dependency updates.
DO bump for: new or removed skills, system-prompt rewrites, rule restructuring.
"""

WORKFLOW_VERSION = "0.5.0"
