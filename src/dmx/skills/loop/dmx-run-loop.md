---
name: run-loop
title: Run Loop
description: Start a dmx loop by name. Reads the loop config from .dmx/loops/{name}.yaml, initialises state, and runs the first skill.
arguments:
  - name: loop
    description: "Name of the loop to run (e.g. spec, plan, dev, validate, release)."
    required: true
---

Call the `run_loop` MCP tool now with `name` set to "{{loop}}".

Report the result to the user exactly as returned by the tool, then follow any instructions it contains.
