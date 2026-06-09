"""Workflow version — independent of the package version.

Bump this constant when skills or rules change in a way that requires
developers to re-run ``/dmx/init`` to get current behaviour.

Do NOT bump for: bug fixes, new IDE emitters, CLI changes, dependency updates.
DO bump for: new or removed skills, system-prompt rewrites, rule restructuring.
"""

WORKFLOW_VERSION = "0.2.0"