# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/deepmodel/dmx/security/advisories/new).

Include:
- Description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions
- Any suggested mitigation

You will receive an acknowledgement within 48 hours. We aim to release a fix within 14 days for critical issues.

## Scope

In scope:
- Code execution via MCP tool calls
- Authentication bypass in HTTP transport
- Path traversal in skill/rule file loading
- Arbitrary file write via `setup_ide_rules`

Out of scope:
- Issues in third-party dependencies (report upstream)
- Issues requiring physical access to the machine running the server
