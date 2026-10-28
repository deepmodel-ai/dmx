---
name: extend
title: Add a Skill, Loop or Validator
description: Add or change a skill, loop or validator for this repo, a shared source, or one component of a monorepo. Says where each file goes, the format of each, and how to check the result.
arguments:
  - name: what
    description: "What to add, in a sentence, e.g. 'a review loop for the payments component that fails on unclosed resources'."
    required: false
---

You are adding to dmx's own configuration: a skill, a loop or a validator. Use only the formats on this page; don't read dmx's source to find them. Request: {{what}}

## Step 1 — Decide what to write

- **A skill** is instructions the agent follows, in Markdown. Write one for a step a person or a loop will ask for by name.
- **A loop** is a sequence of skills, with validators that decide whether the work can move on. Write one for a repeatable process with a gate at the end, such as a team's review.
- **A validator** is a small program that checks the work and reports pass or fail. Write one for a check that must be decided by code, not by the agent's judgement.

A review loop usually needs all three: a skill that does the review and writes a report, a validator that grades the report, and a loop that runs them.

## Step 2 — Decide where it goes

| For | Skill | Loop | Validator |
|---|---|---|---|
| This repo | `.dmx/skills/<name>.md` | `.dmx/loops/<name>.yaml` | `validators/<name>.py` (at the repo root) |
| One component | `.dmx/components/<id>/skills/<name>.md` | `.dmx/components/<id>/loops/<name>.yaml` | `.dmx/components/<id>/validators/<name>.py` |
| A shared source | `skills/<name>.md` in the source repo | `loops/<name>.yaml` there | `validators/<name>.py` there |

- **Names:** use lowercase letters, digits and `-`. Validator file names may also use `_`.
- **A component's skills and loops** are called by `<id>/<name>`, such as `payments/refund-review`. Only tickets that include the component can use them.
- **The standard loops can't be replaced per component.** `spec`, `plan`, `dev`, `validate` and `release` can't be replaced inside a component; add a new loop, or checks (Step 6), instead.
- **Overriding repo-wide.** A repo file with the same name as a shared-source or bundled one replaces it for this repo.

## Step 3 — Write a skill

A Markdown file with YAML frontmatter, then the instructions:

```markdown
---
name: refund-review
title: Refund Review
description: Review the branch's refund code for double-charge risks and write the findings to the job folder.
---

Instructions, written to the agent, step by step. Say exactly what to read,
what to decide, what file to write (for example
`.dmx/jobs/{job_id}/refund-review.json`), and what to output at the end.
```

`description` is what `list_skills` shows, so make it say when to use the skill. A skill that needs helper files can be a folder instead: `<name>/SKILL.md`, with `scripts/`, `references/` or `assets/` next to it.

## Step 4 — Write a loop

```yaml
name: refund-review          # must match the file name, without .yaml
skills:                       # in order; a component's own skills as <id>/<name>
  - payments/refund-review
trigger:
  type: manual                # manual, or on_complete when another loop chains here
goal_state: "No refund path can charge twice"
validators:
  - tool: refund_findings     # a validator name: see Step 5
    timeout_seconds: 300      # optional; at least 60; default 630
    checks:
      - name: no_double_charge
        required: true        # a required check that fails stops the loop
on_optional_failure: warn     # warn or ignore, for checks with required: false
failure_handling: pause       # pause or fail, for required checks
human_gate: true              # pause after each skill for review
on_complete:
  on_success:
    trigger_loop: null        # a loop to start next, or null
  on_failure:
    rework:                   # optional: turn failed checks into tasks and fix them
      loop: dev
      max_rounds: 2
      approve: true
  on_warning:
    trigger_loop: null
```

- **`rework` or `trigger_loop`.** `on_failure` takes one or the other, not both.
- **What `rework` does.** Each failed required check becomes a task in a new phase of the ticket's `tasks.md`. After approval, the `loop` implements it, and this loop runs again from its first skill.
- **When it doesn't apply.** A check the validator marks `"reworkable": false` pauses the loop instead.
- **Waiting at the gate.** After the last skill, the loop pauses (with `human_gate: true`) and runs its validators on `loop_continue`. While they run, keep calling `loop_status` until it returns the result.

### A component's loop and its own validators

In a component's loop, name the component's validator plainly (`tool: refund_findings`) or with the id (`tool: payments/refund_findings`). If `.dmx/components/<id>/validators/refund_findings.py` exists, dmx runs it, and its checks are reported as `payments/no_double_charge`; you can write the check names either way. A validator the component doesn't have, such as `run_tests`, runs from the repo as usual.

## Step 5 — Write a validator

A validator is a Python script. dmx runs it with the same Python that runs dmx, from the repo root, so use only the standard library. To run your project's own tools, call them with `subprocess`.

**Input**, as JSON on stdin:

```json
{
  "skill_outputs": {"payments/refund-review": "the agent's summary of what the skill did"},
  "goal_state": "No refund path can charge twice",
  "loop_context": {
    "job_id": "GH-412", "task_id": "...", "loop_name": "payments/refund-review",
    "branch": "feature-gh-412-refunds", "ticket_ref": "GH-412",
    "job_dir": ".../.dmx/jobs/GH-412", "spec_path": "...", "tasks_path": "...",
    "active_context_path": "...", "workspace_root": "...", "timeout_seconds": 300
  }
}
```

`skill_outputs` holds the agent's own summary of each skill, 10 lines or fewer. Check facts in the files the skill wrote, such as the files under `job_dir`, not in the summary.

**Output**, as JSON on stdout:

```json
{
  "pass": false,
  "message": "One refund path can charge twice",
  "checks": [
    {
      "name": "no_double_charge",
      "pass": false,
      "message": "refunds.go:88 retries the charge after a timeout",
      "findings": ["refunds.go:88: a retry after a timeout charges again"]
    }
  ]
}
```

- **Exit codes.** Exit 0 when it passes and 1 when it fails. Any other exit code, output that isn't JSON, or running past `timeout_seconds` counts as an error, and every check fails.
- **Report every check the loop declares.** A declared check that isn't reported counts as failed.
- **`findings`** (optional) lists one line per problem. Rework turns them into the task's sub-points.
- **`reworkable: false`** marks a failure that isn't a code change, such as a missing or stale report, or a missing tool. Rework then leaves the check alone.
- **Grading a report.** When a validator grades a report a skill wrote, compare the report's recorded commit with `git rev-parse HEAD`. Fail as not reworkable when it's stale, so the skill is run again rather than the code being changed.

## Step 6 — Add checks to a standard loop for one component

To run a validator in `validate` (or another standard loop) on every ticket that includes a component, list it in the component's `component.yaml`, not in a loop:

```yaml
validators:
  validate: [refund_findings]
```

A bare name makes every check it reports required. For more control, use the same mapping as a loop's validator (`tool`, `timeout_seconds`, `checks`).

## Step 7 — Check the result

1. Run `dmx check` (or the `check_setup` tool). Fix every `✗` it reports for the files you added.
2. Call `list_skills` and confirm the new skill appears with its description.
3. For a validator, run it once by hand: `echo '{"loop_context": {"workspace_root": "."}}' | python validators/<name>.py`. Confirm it prints valid JSON and exits 0 or 1.
4. For a loop, start it with `run_loop`. For a component's loop, use a ticket that includes the component.

Then tell the developer what you added, where, and how to use it. For a loop, that's `/dmx/run-loop <name>`.
