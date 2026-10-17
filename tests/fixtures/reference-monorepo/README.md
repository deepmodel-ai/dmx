# Reference monorepo

A small polyglot monorepo that dmx's tests copy into a temporary git repo. It grows with each 0.7 ticket (#81).

- `services/ledger`: a Go package in the root module (`go.mod`).
- `services/payments`: a Go service in its own module that depends on ledger.
- `packages/billing-ui`: a pnpm workspace package.
- `libs/pyutils`: a `uv` workspace member.
- `crates/hasher`: a Cargo workspace member.
- `tools/lua-lint`: a component in a language dmx has no default for, with its own `test` command.
