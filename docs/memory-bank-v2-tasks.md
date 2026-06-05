# Tasks: Memory Bank v2

**Spec:** [`memory-bank-v2-spec.md`](memory-bank-v2-spec.md)  
**Workflow version target:** `0.1.0`  
**Branch:** _(implementation branch TBD)_

---

## Phase 1 — Workflow version & infrastructure ✓

Foundation: adopt SemVer for workflow version string.

> **Testing policy:** do not add tests that hardcode a specific version. Existing tests import `WORKFLOW_VERSION` and assert wiring — that pattern is sufficient. Snapshots regenerate automatically on the next test run after bumping the constant.

- [x] Set `WORKFLOW_VERSION = "0.1.0"` in `src/dmx/_workflow_version.py`; update docstring with pre-release bump rules
- [x] Confirm `src/dmx/ide/emitters.py` marker uses `WORKFLOW_VERSION` unchanged
- [x] Confirm `src/dmx/tools.py` returns `WORKFLOW_VERSION` in `setup_ide_rules` response
- [x] Refresh `tests/__snapshots__/test_ide.ambr` (regenerated via test run after constant bump)
- [ ] _(Optional)_ `dmx workflow-version` CLI subcommand

---

## Phase 2 — System prompt & rules

Rewrite the always-apply rule to describe v2 memory bank layout and behaviour.

- [x] Rewrite memory bank diagram in `src/dmx/rules/system-prompt.md` — flat `spec.md` / `tasks.md`; remove `tickets/` tree
- [x] Document `activeContext.md` as learning inbox (Open Learnings, Open Decisions, Session Notes) — not ticket pointer
- [x] Document branch-per-ticket principle — one issue per branch
- [x] Document three-tier memory sync (commit light, create-pr full, update-memory deep) and promotion rules
- [x] Update persona behaviour — append learnings to `activeContext` during work; remove “one ticket at a time / close before new work”
- [x] Update commands table — `close-ticket` description (git-clean, no archive); remove stale memory bank references
- [x] Remove auto-init inference that writes ticket pointer into `activeContext` from branch names
- [x] Review `project-config` rule — does not exist in this repo; only `system-prompt.md` is bundled

---

## Phase 3 — Init skill

`/dmx/init` scaffolds v2 layout and reports workflow version.

- [x] Update `src/dmx/skills/workflow/0-init/dmx-init.md` — scaffold learning-inbox `activeContext.md` (empty sections)
- [x] Remove `mkdir -p .dmx/tickets/active .dmx/tickets/archived` from init steps
- [x] Remove v1 `activeContext` template with `## Active Ticket`
- [x] Do not scaffold `spec.md` or `tasks.md` at init — document they appear on first ticket branch
- [x] Update init result output — remove `tickets/` paths from memory bank listing

---

## Phase 4 — Entry-point skills (branch + spec)

Create branch, write fresh `.dmx/spec.md` with YAML frontmatter.

- [x] Update `src/dmx/skills/workflow/1-triage/dmx-create-ticket.md`
  - [x] Write spec to `.dmx/spec.md` with frontmatter (`ticket`, `branch`, `summary`, `ticketing`)
  - [x] Remove Step 9 `activeContext` ticket update
  - [x] Remove “already has active ticket” guard
  - [x] Remove all `tickets/active/{ref}/` path references
- [x] Update `src/dmx/skills/workflow/1-triage/dmx-derive-ticket.md`
  - [x] Same spec path and frontmatter changes
  - [x] Remove `activeContext` ticket update
  - [x] Remove `tickets/active/` paths
- [x] Update `src/dmx/skills/workflow/1-triage/dmx-hotfix.md`
  - [x] Write spec to `.dmx/spec.md` with frontmatter
  - [x] Remove `activeContext` ticket update
  - [x] Remove `tickets/active/` paths
- [x] Update `src/dmx/skills/utility/dmx-create-branch.md`
  - [x] Write spec to `.dmx/spec.md` with frontmatter
  - [x] Remove `activeContext` ticket update
  - [x] Remove `tickets/active/` paths

---

## Phase 5 — Build & validate skills

Read flat spec/tasks only; no ticket resolution.

- [x] Update `src/dmx/skills/workflow/2-plan/dmx-plan.md`
  - [x] Remove `ticket_id` argument from frontmatter
  - [x] Remove “Resolve the active ticket” step
  - [x] Read `.dmx/spec.md`; write `.dmx/tasks.md` at flat path
  - [x] Remove Step 7 `activeContext` update
  - [x] Update tasks.md template (Spec + Branch refs, no ticket folder paths)
  - [x] Hard stop if `spec.md` missing
- [x] Update `src/dmx/skills/workflow/3-build/dmx-implement-next-phase.md`
  - [x] Remove `ticket_id` argument
  - [x] Remove ticket resolution step
  - [x] Read `.dmx/spec.md` + `.dmx/tasks.md` directly
  - [x] Add note: may append to `activeContext` Open Learnings when flagging patterns
  - [x] Hard stop if `tasks.md` missing
- [x] Update `src/dmx/skills/workflow/3-build/dmx-implement-next-task.md`
  - [x] Same changes as implement-next-phase
- [x] Update `src/dmx/skills/workflow/4-validate/dmx-validate.md`
  - [x] Remove `ticket_id` argument
  - [x] Remove ticket resolution step
  - [x] Read `.dmx/spec.md` + `.dmx/tasks.md` directly
  - [x] Update validation report header to use spec summary, not ticket ID

---

## Phase 6 — Memory sync skills

Three-tier sync: light (commit), full (create-pr), deep (update-memory).

- [x] Update `src/dmx/skills/utility/dmx-commit.md`
  - [x] Remove ticket resolution from `activeContext`
  - [x] Light sync: read `activeContext.md` + diff; promote qualifying inbox items to core files
  - [x] Trim promoted items from `activeContext`
  - [x] Remove `tickets/active/` path reads
  - [x] Ticket footer from `spec.md` frontmatter or branch name parse
  - [x] Remove “Keep Active Ticket as-is” logic
- [x] Update `src/dmx/skills/workflow/5-ship/dmx-create-pr.md`
  - [x] Full sync: read `.dmx/spec.md`, `.dmx/tasks.md`, `activeContext.md`, all core files
  - [x] Extract durable learnings from spec/tasks/inbox (not `tickets/active/` paths)
  - [x] Remove `activeContext` ticket pointer refresh
  - [x] Ticket ref from spec frontmatter or branch parse for PR title / transitions
  - [x] Memory commit message unchanged in intent
- [x] Update `src/dmx/skills/utility/dmx-update-memory.md`
  - [x] Remove `ticket_id` argument from frontmatter
  - [x] Remove ticket resolution step
  - [x] Read `.dmx/spec.md`, `.dmx/tasks.md`, `activeContext.md`, all core files
  - [x] Deep sync: reconcile contradictions; promote all qualifying inbox items
  - [x] Refresh `activeContext` structure (Open Learnings / Open Decisions / Session Notes) — no Active Ticket section
  - [x] Update guards — ticket-specific details stay in spec/tasks, not core files

---

## Phase 7 — Ship & integration skills

Git-clean close; integration skills use spec frontmatter.

- [x] Update `src/dmx/skills/workflow/5-ship/dmx-close-ticket.md`
  - [x] Remove Step 9 — archive folder move (`mv tickets/active/ → archived/`)
  - [x] Remove Step 11 — `activeContext` clear
  - [x] Update skill description — external cleanup only, no `.dmx/` mutations
  - [x] Renumber steps; update result output (remove Folder archived / memory synced lines)
- [x] Update `src/dmx/skills/utility/dmx-draft-pr-description.md`
  - [x] Read ticket ref from `.dmx/spec.md` frontmatter or branch parse
  - [x] Read spec/tasks content from flat paths for PR body generation
  - [x] Remove `tickets/active/` references if any

---

## Phase 8 — Tests & catalog

Ensure bundled skills, rules, and snapshots are consistent with v2. Follow [Phase 1 testing policy](#testing-policy-workflow-version) — no hardcoded workflow version asserts.

- [x] Grep entire `src/dmx/skills/` for stale references — `tickets/active`, `tickets/archived`, `## Active Ticket`, `activeContext` ticket resolution
- [x] Grep `src/dmx/rules/` for v1 path references
- [x] Update `tests/test_catalog.py` if skill argument counts changed (removed `ticket_id` from plan/implement/validate/update-memory)
- [x] Update `tests/test_server.py` if argument substitution tests reference removed args
- [x] Run full test suite — fix any failures from marker or skill content changes
- [x] Verify skill count unchanged (23 skills)

---

## Phase 9 — Documentation & release

User-facing docs and changelog for memory bank v2.

- [ ] Update `README.md` — v2 memory bank layout; `activeContext` as learning inbox; branch-scoped spec/tasks
- [ ] Update `CHANGELOG.md` — memory bank v2; workflow marker `0.1.0`; breaking layout change; migration note
- [ ] Update `CONTRIBUTING.md` — flat spec/tasks model; workflow version bump rules (pre-release `0.x.y`)
- [ ] Add migration section to README or link to spec migration section
- [ ] Update `docs/memory-bank-v2-spec.md` status from Draft → Implemented (when done)
- [ ] Bump PyPI package version appropriately (e.g. `0.2.0`) — independent of workflow `0.1.0`
- [ ] Dogfood: run `/dmx/init` on `deepmodel-ai/dmx` repo; validate full workflow on a test ticket

---

## Phase 10 — Verification checklist

Manual smoke tests after implementation.

- [ ] `/dmx/init` — scaffolds v2 layout; no `tickets/`; learning-inbox `activeContext`; reports installed workflow version
- [ ] `/dmx/create-ticket` — creates branch + `.dmx/spec.md` with frontmatter; no `activeContext` ticket write
- [ ] `/dmx/plan` — reads spec, writes `.dmx/tasks.md`; fails cleanly if no spec
- [ ] `/dmx/implement-next-phase` — reads flat spec/tasks; can append to `activeContext`
- [ ] `/dmx/commit` — promotes inbox learnings; ticket footer from spec frontmatter; no ticket resolution
- [ ] `/dmx/create-pr` — full memory sync from flat paths; commits `.dmx/` on branch
- [ ] `/dmx/close-ticket` — closes issue, deletes branch; zero `.dmx/` file changes
- [ ] `/dmx/hotfix` — parallel branch with independent spec; no conflict with feature branch
- [ ] `/dmx/update-memory` — deep sync without `ticket_id` arg
- [ ] Re-init on project with non-semver marker — rules refreshed; semver marker written
- [ ] Protected `main` — close-ticket leaves working tree clean (no `.dmx/` edits)
