# Repository security protections

Security checks run in GitHub, not as local pre-push hooks. New clones require
no Betterleaks, pip-audit, or security-specific Git installation. Ordinary
development and pushes do not invoke local security checks.

## GitHub enforcement

The `Security` workflow runs on pull requests (including forks), pushes to
`main`, weekly, and manually. Its required check names remain:

- `security-secrets`
- `security-dependencies (3.11)`
- `security-dependencies (3.12)`
- `security-dependencies (3.13)`
- `security-dependencies (3.14)`

Existing strict `test`/`build` requirements, zero required human approvals,
resolved review conversations, and protections against force pushing/deleting
`main` remain intact. CodeQL, Dependabot alerts, GitHub secret scanning, and
secret push protection are retained. Automatic Dependabot update PRs are not
enabled by this change.

CI checks execute after code reaches GitHub and block merging when they fail.
They do not prevent the initial upload. GitHub secret push protection provides
separate server-side rejection for supported credential patterns.

## Secret scanning

The secret job installs checksum-pinned Betterleaks **1.9.0** on its Windows
runner and scans all fetched refs plus HEAD, using complete Git history.
Commit patches, merge changes, and commit messages are included; adding a
secret and later deleting it does not remove the finding. Shallow history
fails instead of silently narrowing coverage.

Provider validation is explicitly disabled. The scanner does not send detected
credentials to their providers or upload code to another scanning service.
Betterleaks/legacy Gitleaks environment overrides are removed. Inline
`betterleaks:allow` and `gitleaks:allow` comments cannot bypass the gate.

Raw JSON results remain in process memory. Only rule/file/line/commit metadata
is printed; matched content and commit-message metadata are not saved or
echoed. This matters because Betterleaks 1.x can retain sensitive commit
metadata even with native redaction enabled. Scanner errors, malformed
reports, and incomplete scans fail the job.

The scanner and any needed checksum-pinned Git compatibility fix are installed
under the runner's temporary directory, not in the checkout or on developer
machines. There is no automatic scanner-update job; version/checksum changes
are reviewed as ordinary repository changes.

## Dependency auditing

Each Windows/Python 3.11-3.14 job independently installs pinned
`pip-audit==2.10.1` in an isolated runner environment. It audits the committed
HEAD's root `requirements*.txt` files, including build and security-tool
dependencies and their resolved transitive packages, not unrelated global
packages.

Resolution uses public PyPI and binary wheels only. A fresh advisory cache is
used every run. Network/TLS failures, skipped packages, missing wheels,
unsupported manifests, and malformed reports fail the job. No automatic
dependency upgrades or advisory suppressions are performed.

Package names and versions are sent to PyPI for resolution and advisory
queries; repository contents and secrets are not. Dependency review is not
added as a duplicate vulnerability gate or an unrequested license policy.

## Workflow implementation and tests

`tools/ci_security.py` is an implementation helper for GitHub Actions. Its
entry point requires the Actions environment and offers no local setup,
combined check, outgoing-ref hook, or hook-installation command. Scanner and
auditor setup steps are independent, so the secret job does not install
pip-audit and dependency jobs do not install Betterleaks.

The workflow uses SHA-pinned actions, read-only repository permissions, no
repository secrets, and checkout without persisted credentials. Existing
security settings and branch rules are not modified by the workflow.

The ordinary unit suite exercises report parsing and failure handling without
installing tools. The dedicated secret job installs Betterleaks and runs real
history/redaction/provider-isolation fixtures in temporary repositories. Fake
credentials are generated only inside those fixtures, never pushed to GitHub.
No raw finding reports are uploaded as artifacts.

## Remediation and exceptions

- **Secret finding:** inspect the reported location, revoke/rotate a real
  credential, and remove it from the relevant history. Merely deleting the
  latest copy does not remove earlier exposure. Coordinate any history rewrite
  explicitly; these tools never rewrite history.
- **Dependency finding:** review the advisory and intentionally update the
  affected pins, then rerun the PR checks.
- **Tool/network/TLS failure:** inspect the failed Actions job and repair the
  runner/setup or connection. Do not disable certificate verification or accept
  an incomplete scan as clean.
- **False positive:** document a narrowly justified Betterleaks 1.x location
  fingerprint in `.betterleaksignore` through a reviewed PR. There are currently
  no exceptions, blanket test exclusions, or history baselines. Dependency
  advisory exceptions require an explicit reviewed implementation.

## Cleanup for older clones

The previous local pre-push feature and `tools/security_check.py` were removed.
Git does not remove an already installed hook when pulling these changes.
If you previously installed it, inspect the active hook directory (including
any custom `core.hooksPath`) before editing:

1. Remove only the wrapper containing `# App Activity security pre-push hook`.
2. If `pre-push.before-security` exists, restore it as `pre-push` so your
   original hook continues to run. Do not delete unrelated hooks or change
   global hook configuration.
3. Remove the old checkout's `.security-tools` directory after confirming it
   contains only the security executables, isolated Git, and auditor environment
   installed by the retired setup. The ignore entry remains to prevent legacy
   tool files from being accidentally committed.

No security hook was installed in the maintainer checkout when this feature
was retired. Local PyPI TLS issues no longer block development or pushes;
dependency audits run on GitHub runners instead.

## Coverage limits

A clean scan does not prove that dependencies are non-malicious or that no
secret exists. Betterleaks uses pattern/filter rules; archive extraction and
recursive decoding remain disabled. Only fetched history is available to CI.
Audits cover known Python package advisories, not Windows/Python themselves,
Inno Setup, other native tools, or every bundled native library. Additional
platforms, Python versions, dependency formats, and major scanner upgrades
require explicit review.
