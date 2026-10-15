# Security checks

These checks complement GitHub's existing CodeQL, Dependabot alerts, and secret
scanning. They add local pre-push checks and required CI checks without adding
application runtime dependencies or requiring another maintainer's approval.

## Setup on Windows

Use the same Windows Python version and architecture that you use to build the
application. Python 3.11 through 3.14 are covered by the CI dependency matrix.
Git for Windows is required.
Betterleaks supports Windows x64 and ARM64. Some Git for Windows builds reject
the `NUL` configuration path used by Betterleaks' isolated Git subprocesses.
Setup probes for this bug and, only when needed, installs checksum-pinned
MinGit **2.56.0.windows.2** inside `.security-tools`. The official October 5,
2026 release contains the fix. Only security-tool subprocesses use this
repository-local Git; system Git, global configuration, and your shell's PATH
are not changed. No snapshot workaround or disabling of Git isolation is used.

```powershell
python tools\security_check.py setup
python tools\security_check.py check
python tools\security_check.py install-hook
```

Setup installs Betterleaks **1.9.0** from its official release, verifies the
platform-specific SHA-256 checksum, and installs `pip-audit==2.10.1` in a
dedicated virtual environment. Everything lives under the ignored
`.security-tools` directory; no global Python packages or Git settings change.
Tool installation is explicit, never performed automatically during a push.
Rerunning setup refreshes the pinned scanner atomically after checksum and
version verification; a failed download never replaces an existing binary.
If you change Python version or architecture, move the old tool directory
aside and run setup with the new interpreter.

This replaces Gitleaks, including its configuration file, with
`.betterleaks.toml`. Existing hook installations still call the same Python
helper; rerun setup after syncing this change before pushing. An old
`.security-tools\gitleaks.exe` is no longer used and can be removed.
Betterleaks 1.9.0 is pinned deliberately: future major releases require review
of CLI, configuration, ignore-file, and report compatibility. There is no
scheduled tool-update job or automatic installation on sync.

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
The scanner runs locally with provider validation explicitly disabled; source
and detected secrets are not sent to a scanning service or credential provider.
Betterleaks and legacy Gitleaks environment overrides are removed from scanner
processes. JSON results are captured in memory, never written as raw report
files, and console findings contain only rule/file/line/commit metadata, not
matched source or secrets. Native `--redact=100` redacts matched values, but
Betterleaks 1.x may retain secrets in commit-message metadata; the wrapper
never emits or saves those fields.
There are no blanket test-directory exclusions, history baselines, or inline
`gitleaks:allow` or `betterleaks:allow` bypasses. The compatibility-named
`--gitleaks-ignore-path` and `--ignore-gitleaks-allow` flags are the supported
Betterleaks 1.x interfaces, not invocations of the old scanner. Archive
extraction and recursive decoding remain disabled to preserve the original
scan scope. Logged scanner errors fail the gate even with a zero exit code.

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
  PR. A Betterleaks 1.x location fingerprint in `.betterleaksignore` can be narrowly scoped to a single
  finding if justified. Do not add broad path/commit/rule exclusions. There are
  currently no exceptions. Legacy `.gitleaksignore` entries, if added in another
  checkout, must be explicitly reviewed and moved to `.betterleaksignore`;
  they are not silently adopted. Dependency advisory exceptions would require an
  explicit reviewed implementation; none are automatically accepted.

Only safe metadata is printed, including on scanner failures. Raw diagnostic
output and JSON finding reports are not uploaded to GitHub artifacts.

## GitHub enforcement

The `Security` workflow runs on PRs (including forks), pushes to `main`, weekly,
and manually. It uses SHA-pinned actions, read-only repository permissions, no
repository secrets, and checkout without persisted credentials.

- `security-secrets` scans full fetched history and exercises real Betterleaks
  against isolated temporary test repositories.
- `security-dependencies (3.11)`, `(3.12)`, `(3.13)`, and `(3.14)` independently
  audit the committed dependency set on Windows.

These exact checks are required; the scanner migration keeps their names intact.
Existing `test`/`build` requirements, zero required approvals, and the rules
against force pushing/deleting `main` remain intact. CodeQL remains the code
analysis service; this workflow does not duplicate it.

GitHub secret push protection complements Betterleaks with server-side detection
of supported credential patterns. Dependency review is useful for PR-specific
dependency/license policies, but is not added here: the full dependency audit
already blocks known vulnerabilities, including unchanged transitive
dependencies, and Dependabot alerts remain enabled. No additional license
policy or automatic security-update PR behavior is introduced.

## Validation and limitations

```powershell
python -m unittest discover -s tests -p test_security_check.py -v
```

Install the tools first to run real Betterleaks integration cases; those cases
are skipped in the ordinary unit suite if Betterleaks is absent, but the dedicated
CI security job installs it before running them. Fake credentials are
constructed only inside temporary fixture repositories, never committed or
pushed to GitHub. Dependency failure cases use deterministic advisory fixtures,
not intentionally vulnerable packages installed into your environment.

Local hooks can be bypassed by Git clients, so CI and protected-branch rules
are the enforcement boundary. Scanners do not guarantee that a dependency is
non-malicious or that no secret exists. Betterleaks has pattern/format limitations,
and this setup does not unpack arbitrary historical binary archives. Audits
cover Python package advisories, not Python/Windows themselves, Inno Setup,
other native build tools, or all bundled native libraries. The application
currently has no third-party Python runtime packages. Other platforms, future
Python versions, new dependency formats, and binary tool updates require
separate assessment.

Official references:

- https://github.com/betterleaks/betterleaks/tree/v1.9.0
- https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.2
- https://github.com/pypa/pip-audit
- https://git-scm.com/docs/githooks
- https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/manage-your-dependency-security/configure-dependency-review-action
