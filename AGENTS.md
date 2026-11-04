# Agent instructions

The authoritative instructions for coding agents in this repository are in
[`.github/copilot-instructions.md`](.github/copilot-instructions.md). Read them
before making changes; this file only summarizes the essentials.

- **Test:** `python -m unittest discover -s tests -v` (Windows, Python 3.11+,
  standard library only). Node.js and a Chromium browser enable optional
  dashboard tests.
- **Non-Windows agents:** run the platform-independent modules listed in
  `.github/copilot-instructions.md`; the pull request's Windows checks are
  authoritative for everything else.
- **No side effects:** don't run `install`, `start`, `run`, or `dashboard` (the
  default with no command). They track real activity, and `install` registers
  sign-in startup. `status` and `mcp` are safe. See the README's "Development"
  section.
- **Privacy:** never commit activity databases, exports, or other personal
  activity data.
- **Pull requests:** fill in the
  [pull request template](.github/PULL_REQUEST_TEMPLATE.md). Changes to
  workflows, Dependabot, security tooling, signing, packaging, release logic,
  agent instructions, or token-handling code need explicit maintainer
  confirmation before merging.
