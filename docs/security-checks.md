# Security checks

These checks complement GitHub's existing CodeQL, Dependabot alerts, and secret
scanning. They add local pre-push checks and required CI checks without adding
application runtime dependencies or requiring another maintainer's approval.

## Setup on Windows

Use the same Windows Python version and architecture that you use to build the
application. Python 3.11 through 3.14 are covered by the CI dependency matrix.
Git for Windows is required.

```powershell
python tools\security_check.py setup
python tools\security_check.py check
python tools\security_check.py install-hook
```

Setup installs the Gitleaks version pinned in `tools/security_check.py` from its
official release, verifies the platform-specific SHA-256 checksum, and installs
`pip-audit==2.10.1` in a dedicated virtual environment. Everything lives under the ignored
`.security-tools` directory; no global Python packages or Git settings change.
Dependency-audit tool installation is explicit. Secret scans automatically
install or refresh Gitleaks to the repository's checksum-pinned version when
needed; they never select an unreviewed upstream release.
If you change Python version or architecture, move the old tool directory
aside and run setup with the new interpreter.

The hook installer preserves an existing default `pre-push` hook as
`pre-push.before-security`, then chains it with the original arguments and
stdin after the security checks pass. Reinstallation is idempotent.
Existing `core.hooksPath` configurations or hook symlinks are not overwritten.
For an externally managed hook, integrate
`python tools\security_check.py pre-push "$@"` into its dispatcher, forwarding
the original stdin to both this command and the other hooks.

The installed hook references this checkout and interpreter. Moving the
checkout, removing its security helper, or switching to a branch without the
helper requires reinstalling or integrating the hook explicitly; it does not
silently stop checking pushes.

## Keeping detection rules current

Gitleaks bundles detection rules with each release; these are not a live
advisory database. The `Update Gitleaks` workflow checks the official latest
stable release every Monday at 08:41 UTC and can also be run manually. If a newer
release exists, it verifies both Windows archives against the official release
checksums and opens a version-specific PR changing only the version and hashes.
It never executes downloaded binaries in the updater job or auto-merges a PR.
Existing update PRs, including closed ones, are not overwritten or reopened.
An orphaned update branch causes an explicit failure for manual inspection.

The repository must allow GitHub Actions to create pull requests (Settings >
Actions > General > Workflow permissions). The job requests only contents and
pull-request write permissions, uses the built-in token, and runs from the
default branch, never on untrusted PR code. No personal token or new secret is
needed. GitHub may require **Approve workflows to run** on the generated PR;
approve those runs and wait for all existing required checks before merging.
This does not add a required human code-review approval.

After you merge an update PR and sync the repository, the next `secrets`,
`check`, or installed pre-push invocation refreshes Gitleaks before scanning.
An already-current binary needs no download. The replacement's checksum and
version must match the repository pins before it atomically replaces the old
binary. If a download, verification, or replacement fails, the scan fails; it
does not continue with old rules. Git sync itself does not execute an installer
or change Git hooks. Checking out an older repository revision uses that
revision's pinned version, not necessarily the newest upstream release.

The scheduled workflow becomes active only after its setup PR is merged.
The local `pip-audit` setup requirement is unchanged; Gitleaks refresh does not
repair dependency-audit network/TLS failures or install the pre-push hook.
To propose an update manually, run:

```powershell
python tools\update_gitleaks.py
```

Review tool updates even when checksums match: the checksum file and downloads
come from the same upstream release, not an independent trust source. New rules
can reveal older secrets and can also introduce false positives. Gitleaks
currently describes future releases as security patches only; automation
cannot guarantee new patterns will be published. Evaluating another maintained
scanner would be a separate change, not a silent migration.

## Commands and scope

```powershell
# One command: all locally available Git history plus committed HEAD dependencies.
python tools\security_check.py check

# Separate full-history secret scan.
python tools\security_check.py secrets

# Dependency audit for committed HEAD, without unrelated global packages.
python tools\security_check.py dependencies
```

Before a publication audit, fetch every branch and tag you intend to publish.
The history command scans all local refs plus detached HEAD; it cannot inspect
remote history that has not been fetched. Shallow clones fail explicitly.

The pre-push hook reads Git's ref-update records. For existing refs it scans
every commit in `remote-tip..local-tip`; for new branches or commit tags it
scans their complete reachable history. It handles multiple refs and ignores
deletion-only updates. Missing remote base objects fail with instructions to
fetch rather than silently narrowing the range. Non-commit tag targets are
not supported and fail explicitly.

Both modes inspect commit patches, including merge changes, and commit
messages. A secret committed and subsequently deleted still blocks a push.
The scanner runs locally; source and detected secrets are not sent to a
scanning service. Reports are temporary and redacted, and console findings
contain only rule/file/line/commit metadata, not matched source or secrets.
There are no blanket test-directory exclusions, history baselines, or inline
`gitleaks:allow` bypasses.

Dependency audits read **committed manifests at every outgoing tip**, even
when a different branch is checked out or the working tree has modifications.
Identical manifest snapshots are audited once within that invocation.
Supported manifests are root-level `requirements*.txt` files containing exact
package pins, with optional extras and environment markers. That currently
includes build and security-tool requirements. Additional manifest formats,
URLs, nested includes, editable packages, and custom indexes require explicit
audit support instead of silently running arbitrary build code.

`pip-audit` resolves transitive dependencies for the executing Windows/Python
environment using public PyPI and binary wheels only. Audits are strict,
including packages that cannot be resolved or assessed. A fresh advisory cache
is used on every run; a previous clean result never skips a new audit.
Package names/versions are sent to PyPI for resolution and advisory queries,
but repository contents and secrets are not. No automatic dependency upgrades
or advisory suppressions are performed.

For this small repository, expect secret checks to take seconds and dependency
checks to take tens of seconds to a few minutes, depending on package metadata
downloads and network availability. First-time setup is additional. Network
timeouts are bounded and fail the check.

## Failures and exceptions

- **Secret finding:** inspect the reported location locally. Rotate/revoke a
  real credential, remove it from every outgoing commit, and rerun the check.
  Deleting it only in the latest commit does not remove the exposure. Coordinate
  any necessary history rewrite explicitly; these tools never rewrite history.
- **Vulnerable dependency:** review the advisory and suggested fixed versions,
  update the relevant pins intentionally, and rerun tests and audits.
- **Missing tool/version mismatch:** rerun setup with the appropriate Python.
- **Network, TLS, missing wheel, skipped package, malformed report, or scanner
  error:** the push is blocked because the scan is incomplete. Repair the
  connection/environment or extend supported coverage; do not disable TLS
  validation, use an insecure index, or treat scanner errors as clean results.
- **False positive:** review and document the exact finding and rationale in a
  PR. A Gitleaks fingerprint exception can be narrowly scoped to a single
  finding if justified. Do not add broad path/commit/rule exclusions. There are
  currently no exceptions. Dependency advisory exceptions would require an
  explicit reviewed implementation; none are automatically accepted.

Only safe metadata is printed, including on scanner failures. Raw diagnostic
output and JSON finding reports are not uploaded to GitHub artifacts.

## GitHub enforcement

The `Security` workflow runs on PRs (including forks), pushes to `main`, weekly,
and manually. It uses SHA-pinned actions, read-only repository permissions, no
repository secrets, and checkout without persisted credentials.

- `security-secrets` scans full fetched history and exercises real Gitleaks
  against isolated temporary test repositories.
- `security-dependencies (3.11)`, `(3.12)`, `(3.13)`, and `(3.14)` independently
  audit the committed dependency set on Windows.

These exact checks can be required after they have passed on the setup PR.
Existing `test`/`build` requirements, zero required approvals, and the rules
against force pushing/deleting `main` remain intact. CodeQL remains the code
analysis service; this workflow does not duplicate it.

GitHub secret push protection complements Gitleaks with server-side detection
of supported credential patterns. Dependency review is useful for PR-specific
dependency/license policies, but is not added here: the full dependency audit
already blocks known vulnerabilities, including unchanged transitive
dependencies, and Dependabot alerts remain enabled. No additional license
policy or automatic security-update PR behavior is introduced.

## Validation and limitations

```powershell
python -m unittest discover -s tests -p "test_security*.py" -v
```

Install the tools first to run real Gitleaks integration cases; those cases
are skipped in the ordinary unit suite if Gitleaks is absent, but the dedicated
CI security job installs it before running them. Fake credentials are
constructed only inside temporary fixture repositories, never committed or
pushed to GitHub. Dependency failure cases use deterministic advisory fixtures,
not intentionally vulnerable packages installed into your environment.

Local hooks can be bypassed by Git clients, so CI and protected-branch rules
are the enforcement boundary. Scanners do not guarantee that a dependency is
non-malicious or that no secret exists. Gitleaks has pattern/format limitations,
and this setup does not unpack arbitrary historical binary archives. Audits
cover Python package advisories, not Python/Windows themselves, Inno Setup,
other native build tools, or all bundled native libraries. The application
currently has no third-party Python runtime packages. Other platforms, future
Python versions, new dependency formats, and binary tool updates require
separate assessment.

Official references:

- https://github.com/gitleaks/gitleaks
- https://github.com/pypa/pip-audit
- https://git-scm.com/docs/githooks
- https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/manage-your-dependency-security/configure-dependency-review-action
