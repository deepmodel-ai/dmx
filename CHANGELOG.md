# Changelog

All notable changes to `deepmodel-dmx` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.1.0] — Unreleased

### Added

- **23 SDLC skills** as MCP prompts: `/dmx-init`, `/dmx-create-ticket`, `/dmx-derive-ticket`, `/dmx-plan`, `/dmx-implement-next-phase`, `/dmx-implement-next-task`, `/dmx-validate`, `/dmx-create-branch`, `/dmx-commit`, `/dmx-create-pr`, `/dmx-draft-pr-description`, `/dmx-close-ticket`, `/dmx-hotfix`, `/dmx-draft-release-note`, `/dmx-release-merge`, `/dmx-create-release`, `/dmx-status`, `/dmx-sync-branch`, `/dmx-update-memory`, `/dmx-review`, `/dmx-test`, `/dmx-docs`, `/dmx-secure`
- **`system-prompt` rule** — always-apply AI persona and memory bank instructions
- **`detect_invoking_ide` tool** — detects Cursor, Claude, Copilot, Antigravity from MCP `clientInfo.name`, `X-Dmx-IDE` header, or `DMX_IDE` env var
- **`setup_ide_rules` tool** — emits per-IDE rule files for Cursor (`.mdc`), Claude, Copilot, Antigravity, and generic agents
- **`dmx serve`** — stdio and HTTP/SSE transports; `PORT` env var; `REQUIRE_API_KEY` + `MCP_API_KEY` bearer auth
- **`dmx serve --watch`** — hot-reload on skill/rule file changes (`watchfiles` extra)
- **`dmx list-skills`** — print all loaded skills with name, title, and argument count
- **`--skills-dir` / `--rules-dir`** CLI overrides and `DMX_SKILLS_DIR` / `DMX_RULES_DIR` env var overrides
- **`create_app()`** internal extension API for the Deepmodel commercial layer

[Unreleased]: https://github.com/deepmodel-ai/dmx/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deepmodel-ai/dmx/releases/tag/v0.1.0
