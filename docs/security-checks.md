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
secret push protection are retained. Dependabot automatic security-update PRs
are enabled in repository settings; these are proposed fixes, not automatic
merges.

CI checks execute after code reaches GitHub and block merging when they fail.
They do not prevent the initial upload. GitHub secret push protection provides
separate server-side rejection for supported credential patterns.

## Secret scanning

The secret job installs checksum-pinned Betterleaks on its Windows
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
machines. Exact native-tool versions and architecture checksums are recorded
in [`tools/ci_security.py`](../tools/ci_security.py). Automated version/checksum
proposals are reviewed as ordinary repository changes.

## Dependency auditing

Each Windows/Python 3.11-3.14 job independently installs pinned
pip-audit in an isolated runner environment. Its single authoritative version
pin is [`requirements-security.txt`](../requirements-security.txt), so a
Dependabot fix to that requirement also updates the installed-version check.
It audits the committed
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

## Reviewed tool upgrades

The `Security tool updates` workflow checks weekly on Mondays at 08:23 UTC
and can be run manually from the default branch. It discovers stable releases
of Betterleaks, pip-audit, and the isolated MinGit compatibility helper.
Scanner execution still uses exact reviewed pins, never an unreviewed `latest`.
Vulnerability advisory data continues to refresh independently on every audit.

Discovery has read-only repository access. New native-tool archives for Windows
x64 and ARM64 must match SHA-256 digests published on the official GitHub
releases before any update plan is published. Prereleases, downgrades,
missing/yanked wheels, missing architecture archives, changed checksums for an
already-pinned version, and download/metadata errors fail the job. The updater
does not install or execute downloaded scanner binaries.

A separate publication job can write only repository contents and pull
requests. It creates a draft PR containing the proposed pin/checksum changes.
It does not approve reviews or workflow runs, merge, force-push, or modify
protections. Existing open scanner-upgrade PRs are left untouched, including
contributor fixes. Already-proposed versions are not proposed again; after
closing a proposal without merging, upgrade those pins manually if needed.
Open Dependabot or contributor PRs changing the same pin files also defer a
new proposal, avoiding overlapping implementation work.
If main moves between discovery and publication, rerun the workflow.

The workflow uses GitHub's built-in token, not a stored personal token or
third-party update service. The repository's combined **Allow GitHub Actions
to create and approve pull requests** setting is enabled for PR creation;
default token permissions remain read-only, and this workflow never uses the
review-approval capability. GitHub places workflows triggered by token-created
PRs in an approval-required state. A maintainer must select **Approve workflows
to run**, then inspect required checks and the latest-commit Copilot review.
See [GitHub's workflow-trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

Keep generated PRs in draft until the normal acceptance/review process is
complete. Review upstream release notes and major-version changes, resolve
valid findings, and merge only with all required gates satisfied. Urgent tool
security fixes can be proposed immediately with a manual run. Dependabot
security-fix PRs are separate and can be created as advisories appear, without
waiting for this weekly schedule.

## Workflow implementation and tests

`tools/ci_security.py` is an implementation helper for GitHub Actions. Its
entry point requires the Actions environment and offers no local setup,
combined check, outgoing-ref hook, or hook-installation command. Scanner and
auditor setup steps are independent, so the secret job does not install
pip-audit and dependency jobs do not install Betterleaks.

The `Security` workflow uses SHA-pinned actions, read-only repository permissions, no
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
