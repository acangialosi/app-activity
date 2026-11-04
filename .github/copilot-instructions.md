# Default delivery workflow

## Implementation and acceptance

- For implementation tasks, own delivery from understanding the request through
  a merged pull request. An implementation request authorizes this full delivery
  workflow without a separate user review, approval, or merge prompt, subject to
  the required checks, reviews, approvals, and repository protections below.
  Privileged changes are the exception: they need explicit maintainer
  confirmation before merging (see "Pull requests and review feedback").
  Do not stop at a plan, an initial implementation, local changes, or a
  review-ready pull request when further delivery work is possible.
- Honor explicit user stopping points, such as a planning checkpoint,
  draft-only delivery, or a request not to merge.
- Requests explicitly limited to research, explanation, planning, or review do
  not authorize implementation or a pull request.
- Establish verifiable acceptance criteria from the requested behavior and
  existing repository conventions. Verify those outcomes directly; do not
  invent unrelated requirements or expand the task's scope.
- Before implementing a feature, fixing a bug, or making other requested
  implementation changes, create a feature branch and open a draft pull request.
  Do not wait until the implementation is written to open the draft.
- Outline the task, scope, acceptance criteria, intended approach, and validation
  plan in the draft pull request description. If an initial commit is needed to
  open the pull request, limit it to planning rather than feature or bug-fix
  implementation.
- Once the draft is open, proceed with implementation without waiting for
  approval unless the user requested a planning checkpoint. Keep the pull
  request description current as the work progresses.
- Make routine implementation decisions independently. Ask only when a material
  product decision, genuinely missing requirement, destructive action, or
  required permission prevents safe progress.
- Follow existing patterns, preserve unrelated work, and fix failures caused by
  the change. Continue iterating while there is a concrete next step toward the
  requested outcome.

## Validation

- Add or update regression tests when behavior changes. Run the smallest
  relevant existing tests first, then broaden validation as needed.
- The full test suite is `python -m unittest discover -s tests -v`. When Node.js
  is available, it also runs the dashboard's JavaScript behavior tests.
- Don't run `install`, `start`, `run`, or `dashboard` (also the default with no
  command) to validate a change: they start tracking real activity, and
  `install` also registers sign-in startup. See the README's "Development"
  section for setup and checks that have no side effects.
- Use the repository's Windows CI for platform-specific validation and packaged
  builds. Explicitly report any interactive Windows desktop checks that could
  not be performed.
- Copilot cloud agent works on Ubuntu, prepared by
  `.github/workflows/copilot-setup-steps.yml`. Most test modules need Windows,
  so run the platform-independent modules there instead of the full suite:

  ```bash
  python -m unittest tests.test_dashboard tests.test_dashboard_export tests.test_dashboard_feedback tests.test_dashboard_filter_status tests.test_database tests.test_dependency_remediation tests.test_mcp_server tests.test_security_tool_updates tests.test_updates -v
  ```

  The pull request's Windows checks are authoritative for everything else.
- Documentation-only changes do not require a local test-suite run, but check
  changed links or assets where applicable and wait for required CI checks.
- Never claim validation that was not performed, weaken tests to obtain a
  passing result, or bypass repository protections.

## Pull requests and review feedback

- Commit and push implementation changes to the draft pull request opened
  before implementation. Keep it in draft until the acceptance criteria,
  required checks, and review feedback have been addressed.
- After each push, wait for Copilot's code review of the latest commit. Passing
  CI checks alone does not mean the review is complete.
- Read the review summaries, inline comments, and unresolved review threads.
  Evaluate each finding against the code; do not apply suggestions blindly.
- Inspect CI results, fix task-related failures and valid review findings, and
  run the relevant tests. Explain rejected findings in the pull request with a
  concrete reason.
- Push fixes and repeat the review process until the latest commit has been
  reviewed and no valid findings remain outstanding.
- Resolve a review thread only after its finding has been addressed or an
  explanation for rejecting it has been posted.
- Mark the pull request ready only when the acceptance criteria are verified,
  required checks pass, and the review-feedback process is complete. Then merge
  without waiting for a separate user prompt, unless the user requested an
  earlier stopping point or the change is privileged (see below). Required
  human approvals must still be obtained; agent review does not replace them.
- Privileged changes need explicit maintainer confirmation before merging,
  even when every check passes. A pull request is privileged if it changes
  `.github/workflows/`, `.github/dependabot.yml`, these instructions or
  `AGENTS.md`, security tooling (`tools/ci_security.py`,
  `tools/update_security_tools.py`, `tools/dependency_remediation.py`,
  `requirements-security.txt`), `build.ps1` or other signing, packaging, or
  release logic, or code that uses repository tokens or secrets. Finish the
  rest of the workflow, leave the pull request ready but unmerged, and ask the
  maintainer to confirm the merge. An implementation request alone is not
  confirmation.
- Verify the pull request was merged before reporting delivery as complete.
  Summarize the delivered outcome, validation evidence, and any remaining
  limitations. For a user-requested earlier stopping point, verify and report
  that outcome instead.
- If permissions, unavailable services or reviews, runtime limits, or repeated
  attempts without progress block completion, leave the pull request in draft
  (if one exists) and report the exact blocker and remaining work. Do not claim
  the task is complete or reviewed.
- Merge only when the acceptance criteria are verified, all required checks and
  approvals are satisfied, and the latest-commit review-feedback process above
  is complete. Never bypass repository protections or agent platform permission
  limits to merge; report a blocker if those limits prevent completion.
