# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, report it privately via GitHub's
[private vulnerability reporting](https://github.com/Manseez31/VerdictAI/security/advisories/new)
on this repository. We will acknowledge your report and keep you updated on the fix.

Please include: what the issue is, how to reproduce it, and its potential impact.

## Scope

Things we consider security issues:

- **Secret exposure** — leaked API keys, or code paths that could log/expose them.
- **Prompt injection** that defeats the safety preamble (e.g. content in an
  uploaded document that makes an agent output instructions for unlawful conduct).
- Unauthenticated abuse of the LLM-backed endpoints (cost/DoS), or bypasses of the
  per-IP rate limiter.
- Injection, SSRF, path traversal, or XSS in the API or frontend.
- Unsafe handling of uploaded PDF/DOCX files.

## Handling secrets

- `GROQ_API_KEY` must **only** live in `.env`, which is git-ignored. Never commit it.
- If a key is ever exposed, **rotate it immediately** at
  [console.groq.com/keys](https://console.groq.com/keys).
- `.env.example` documents the variables and must never contain real values.

## Safety expectations

VerdictAI is an educational legal tool. It is designed to refuse assistance with
crimes, evasion of law enforcement, and destruction of evidence. If you find a
prompt or document that reliably defeats these protections, please report it as a
security issue.

## Not legal advice

VerdictAI's outputs are AI-generated, may be wrong, and are not legal advice.
Incorrect legal output is a **quality bug** (please open a normal issue), not a
security vulnerability.
