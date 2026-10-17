# Default delivery workflow

## Implementation and acceptance

- For implementation tasks, own delivery from understanding the request through
  a review-ready pull request. Do not stop at a plan, an initial implementation,
  or local changes when further delivery work is possible.
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
- Use the repository's Windows CI for platform-specific validation and packaged
  builds. Explicitly report any interactive Windows desktop checks that could
  not be performed.
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
  required checks pass, and the review-feedback process is complete. Summarize
  the delivered outcome, validation evidence, and any remaining limitations.
- If permissions, unavailable services or reviews, runtime limits, or repeated
  attempts without progress block completion, leave the pull request in draft
  (if one exists) and report the exact blocker and remaining work. Do not claim
  the task is complete or reviewed.
- Merge only when the user explicitly requests it, all required checks pass,
  and the review-feedback process above is complete.
