# Reference monorepo

A small polyglot monorepo that dmx's tests copy into a temporary git repo. It grows with each 0.7 ticket (#81).

- `services/ledger`: a Go package in the root module (`go.mod`).
- `services/payments`: a Go service in its own module that depends on ledger.
- `packages/billing-ui`: a pnpm workspace package.
- `libs/pyutils`: a `uv` workspace member.
- `crates/hasher`: a Cargo workspace member.
- `tools/lua-lint`: a component in a language dmx has no default for, with its own `test` command.

Component config:

- `ledger` has its own validator (`.dmx/components/ledger/validators/no_float_money.py`) and a Jira default (`LEDG`).
- `payments` depends on ledger, takes a validator, a skill and a loop from its own shared source (`../payments-team-source`, made a git repo and locked by `tests/conftest.py`), and defaults to Jira project `PAY`.
