# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report privately via **GitHub's private vulnerability reporting** — the "Report a vulnerability" button
under the repository's *Security* tab. (No email address is involved; the report goes straight to the
maintainer through GitHub.)

Include what you can: affected version/commit, a description, reproduction steps, and impact. We aim to
acknowledge reports within a few days. Please give us reasonable time to release a fix before any public
disclosure.

## Scope & posture

CodexMill is self-hosted. Its security design, threat model, and the results of an adversarial review
are documented in [`docs/SECURITY.md`](docs/SECURITY.md). In short:

- A local admin account (argon2id-hashed) is required on first run; a fresh instance is not open.
- Saved API keys / OIDC secrets are encrypted at rest.
- Setting `CODEXMILL_SECRET_KEY` is recommended; serve behind HTTPS (`CODEXMILL_HTTPS_ONLY=true`) if you
  expose an instance to the public internet.

## Supported versions

CodexMill is pre-1.0; security fixes land on the latest `main`. Run a recent version.
