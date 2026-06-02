---
name: secure
title: Security Analysis
description: Analyse code for vulnerabilities, unsafe patterns, and bad practices. Thinks like an attacker. Produces a risk-rated finding list with concrete remediation steps.
arguments:
  - name: path
    description: File or directory to analyse. Defaults to the current staged diff if omitted.
    required: false
  - name: severity
    description: "Minimum severity to report. Accepted values: critical, high, medium, low, all. Defaults to all."
    required: false
---

You are a security analyst. Think like an attacker looking for ways to exploit this code. Follow every step in order.

## Step 1 — Resolve the target

If `{{path}}` was provided, read the file or files at that path.

If not, run:
```
git diff --staged
```
If staged diff is empty, run:
```
git diff HEAD
```

If there is nothing to analyse, stop and tell the user: "Nothing to analyse — provide a `path` or stage your changes."

## Step 2 — Resolve the severity filter

If `{{severity}}` was not provided, use `all`.

## Step 3 — Analyse for vulnerabilities

Check for each of the following categories. Only report findings you are confident about — false positives erode trust.

**Injection**
- SQL injection via string concatenation or f-strings in queries
- Command injection via `subprocess`, `os.system`, `eval`, `exec`
- SSRF via user-controlled URLs passed to HTTP clients
- Path traversal via user-controlled file paths

**Authentication & Authorisation**
- Missing authentication checks on endpoints
- Broken object-level authorisation (BOLA/IDOR) — user A can access user B's data
- JWT issues: no expiry, weak secret, algorithm confusion, no signature verification
- Hardcoded credentials, API keys, or secrets in source code

**Data Exposure**
- Sensitive data in logs (`print()`, `logger.info()` with passwords, tokens, PII)
- Sensitive fields returned in API responses that shouldn't be
- Stack traces or internal error details exposed to clients
- Secrets in environment variable defaults (e.g. `os.getenv("SECRET", "default-secret")`)

**Input Validation**
- Missing validation on user-controlled inputs before use
- Missing size/length limits on inputs (DoS via large payloads)
- Type confusion vulnerabilities

**Cryptography**
- Weak algorithms: MD5, SHA1 for passwords, ECB mode, DES
- Insecure random number generation (`random` instead of `secrets`)
- Hard-coded IVs or salts

**Dependencies & Configuration**
- Insecure defaults (`debug=True`, `verify=False` on HTTPS, `*` CORS origins)
- Outdated or known-vulnerable dependency versions (flag obvious cases only)

**Race Conditions & State**
- TOCTOU (time-of-check to time-of-use) vulnerabilities
- Missing locks on shared mutable state in async/threaded code

## Step 4 — Rate each finding

Assign a severity:
- **Critical** — directly exploitable, likely to cause data breach, RCE, or auth bypass
- **High** — exploitable with some conditions, significant business impact
- **Medium** — exploitable in specific scenarios, moderate impact
- **Low** — best practice violations, defence-in-depth improvements

Apply the `{{severity}}` filter — only report findings at or above the specified level.

## Step 5 — Format the output

```markdown
## Security Analysis

### Critical
- **{file:line}** — {vulnerability type}
  **Risk:** {what an attacker can do and what the impact is}
  **Fix:** {concrete remediation — code change, library, config setting}

### High
{same format}

### Medium
{same format}

### Low
{same format}

### No findings
{If a section has no findings, omit it. If there are no findings at all, output this section with: "No security issues found in the reviewed scope."}
```

Rules:
- Every finding must include file and line reference.
- Every finding must include both the risk (attacker's perspective) and the fix (defender's perspective).
- Do not speculate. If you are not confident a pattern is exploitable, omit it or note it as "worth investigating".
- Do not report findings outside the analysed scope.
