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

`.github/dependabot.yml` also schedules weekly version updates for GitHub
Actions (grouped into one PR) and the pinned pip requirements, each waiting
seven days after a release. Workflow actions are pinned to full commit SHAs,
which Dependabot updates along with their version comments. pip version
updates ignore `pip-audit`, which the weekly security tool updater below owns;
that ignore rule may also suppress Dependabot security updates for
`pip-audit`, which that updater and Copilot remediation cover instead. pip
updates are not grouped because remediation identifies Dependabot PRs by their
`dependabot/pip/<package>-<version>` branch names.

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
unsupported manifests, and malformed reports fail the job. The audit never
upgrades dependencies or suppresses advisories; fixes arrive as reviewed pull
requests (see [Automatic dependency remediation](#automatic-dependency-remediation)).

Each dependency job also writes a findings report with the audited commit, the
run attempt that wrote it, a `clean`, `vulnerable`, or `error` status, and each
finding's package, installed version, advisory ID, aliases, and fixed versions.
Runs other than pull request checks upload it as the
`dependency-findings-<python>` artifact for 14 days. The report contains no
repository contents, scanner output, or secrets.

Package names and versions are sent to PyPI for resolution and advisory
queries; repository contents and secrets are not. Dependency review is not
added as a duplicate vulnerability gate or an unrequested license policy.

## Automatic dependency remediation

Once [set up](#setup), dependency findings are handed to GitHub Copilot cloud
agent automatically. Each fix still arrives as a pull request that must pass
the required checks and review before a maintainer merges it. Nothing is
merged automatically.

The `Dependency remediation` workflow runs on `main` after each `Security` run
for a push to `main`, the weekly schedule, or a manual run. It acts only on an
audit of the current `main` head and combines the findings reports from all
four Python versions. Running it manually from `main` processes the newest
finished audit of the current head. Manual runs from other branches are
skipped, so only reviewed code on `main` can use the remediation token.

- **New findings** open one issue labeled `dependency-remediation`. The issue
  lists each vulnerable package, installed version, advisory, fixed versions,
  and affected Python versions, with instructions for the fix. A hidden
  fingerprint stops later audits with the same findings from opening another
  issue, and a hidden package list identifies the Dependabot pull requests
  that belong to the remediation.
- **Copilot cloud agent** is then assigned to the issue and opens a pull
  request. It is not assigned when the `COPILOT_REMEDIATION_TOKEN` secret is
  missing, when no finding has a fixed release yet, or when open Dependabot
  pull requests already update every fixable package to a version that
  resolves all of its advisories. It is told to leave a package unchanged when
  such a Dependabot pull request exists or no fix exists. If Copilot is
  unassigned while the issue is open, the next run assigns it again.
- **Changed findings** update the open issue, add a comment, and ask Copilot to
  update any open Copilot pull request that references the issue with
  `Fixes #<issue>`. Hidden markers in these comments record each notice, and
  the issue keeps a hidden pending marker until every notice is posted, so if
  a run fails partway, the next run posts only the missing notices.
- **A clean audit** on all four Python versions comments on and closes open
  remediation issues as completed.
- **An incomplete audit**, where a report is missing, expired, or records a
  failed audit, never closes issues. A report counts only if the newest run of
  its `security-dependencies (<python>)` job wrote it, so when a re-run job
  cannot write a report, the earlier attempt's report counts as missing. Any
  findings an incomplete audit does report are still tracked, and the issue
  notes that the list may be partial. A malformed report, including a single
  malformed finding, fails the workflow run instead of replacing the tracked
  findings with a partial list.

The `Dependency remediation feedback` workflow catches bugs that an upgrade
causes elsewhere in the code. It runs when the `Security` or
`Windows installer` checks finish on a `dependabot/**` or `copilot/**` pull
request branch in this repository, and waits until both have finished for the
pull request's latest commit. If any check failed, it comments `@copilot` with
the failed jobs and steps and asks Copilot to fix the root cause. On
Dependabot pull requests, Copilot keeps the updated version and adapts the code
instead of reverting it. Dependabot pull requests qualify only when they update
a pip package listed in an open remediation issue, so GitHub Actions and other
unrelated updates are left alone. Copilot pull requests qualify only when they
reference an open remediation issue with `Fixes #<issue>`. Each commit is
reported once, and after three requests on a pull request the workflow asks a
maintainer to take over. Because a Dependabot pull request's checks can fail
before an issue lists its package, each `Dependency remediation` run also
reviews the latest checks on open Dependabot pull requests that update a listed
package, with the same limits. Both workflows share one run queue, so they
never report the same commit twice. They use only the GitHub API and never
check out or run pull request code.

### Setup

The built-in `GITHUB_TOKEN` cannot assign Copilot or start its work, so these
steps need a user token. Without it, the remediation workflow still opens and
updates issues, and both workflows explain in their run summaries what the
missing secret prevents. You can also assign Copilot to an issue manually.

1. Confirm that Copilot cloud agent is enabled for the repository, and that
   the token's user can write to the repository and has a paid Copilot plan.
2. Create a fine-grained personal access token for that user. Limit it to this
   repository, set an expiration date, and grant read access to metadata plus
   read and write access to actions, contents, issues, and pull requests.
   GitHub requires these permissions to assign Copilot through its API.
3. Store it as the repository Actions secret `COPILOT_REMEDIATION_TOKEN`. Never
   commit it, and replace it before it expires.
4. Workflows do not run on Copilot's pushes until a maintainer inspects the
   changes and selects **Approve and run workflows**, so failure feedback waits
   for that approval. Administrators can let these workflows run without
   approval under **Settings > Copilot > Cloud agent**. Review GitHub's warning
   there first, because workflows can access repository secrets.

### Controls

- To stop work on specific findings, close Copilot's pull request and close the
  issue as **not planned** or as a duplicate. While audits report those exact
  findings, the workflow leaves every remediation issue unchanged: it does not
  reopen that issue, open another one, or move another open issue to those
  findings. Different findings are tracked again, and reopening the issue
  resumes work.
- To retry, close any unwanted Copilot pull request and unassign Copilot from
  the issue. Then run the `Dependency remediation` workflow manually from
  `main`, or wait for the next audit.
- Removing the `COPILOT_REMEDIATION_TOKEN` secret stops Copilot assignments and
  fix requests while issues are still tracked. Disable both workflows in the
  **Actions** tab to stop all automation.
- Keep the `dependency-remediation` label and the hidden markers in the issue
  description and automated comments; the workflows use them to find the
  issue, its vulnerable packages, and the notices and fix requests already
  posted.
- Neither workflow suppresses advisories, approves reviews or workflow runs,
  merges, force-pushes, or changes repository settings.

### Copilot environment

[`copilot-setup-steps.yml`](../.github/workflows/copilot-setup-steps.yml)
prepares Copilot cloud agent's Ubuntu environment with Python 3.14 and the
pinned pip-audit. The app targets Windows, so only platform-independent test
modules run there (listed in
[`.github/copilot-instructions.md`](../.github/copilot-instructions.md)), and a
Linux pip-audit run can miss Windows-only transitive dependencies. The Windows
checks on the pull request decide whether a fix is complete.

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
If a branch was created but PR creation failed, a retry can reuse it even after
main advances: its original base must be an ancestor of main, and its complete
tree must exactly match the proposed pins applied to that original base.
Contributor edits are never overwritten. Recovered draft PRs may need main
incorporated before the normal strict up-to-date checks permit merging.

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
Raw scanner reports are never uploaded as artifacts; dependency jobs upload
only the findings report described above.

`tools/dependency_remediation.py` implements both remediation workflows. It
refuses to run outside GitHub Actions or from any branch except the default
branch. The workflows use SHA-pinned actions and checkout without persisted
credentials. Their built-in token is read-only, except that the remediation
job can write issues. `COPILOT_REMEDIATION_TOKEN` is used only to assign
Copilot and to comment on eligible Copilot and Dependabot pull requests. Unit
tests run the tool against a simulated GitHub API.

## Remediation and exceptions

- **Secret finding:** inspect the reported location, revoke/rotate a real
  credential, and remove it from the relevant history. Merely deleting the
  latest copy does not remove earlier exposure. Coordinate any history rewrite
  explicitly; these tools never rewrite history.
- **Dependency finding:** the `Dependency remediation` workflow tracks it in an
  issue and, when configured, assigns Copilot cloud agent to propose a fix.
  Review the advisory and the proposed pins before merging, or update the
  affected pins yourself, then rerun the PR checks.
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
require explicit review. Automatic remediation covers only findings from this
Python dependency audit. Copilot's fixes can be incomplete or wrong; required
checks and maintainer review remain the safeguard.
