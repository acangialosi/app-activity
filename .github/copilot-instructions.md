# Pull request workflow

- Create new work as a draft pull request. Keep it in draft until review
  feedback has been evaluated and addressed.
- After each push, wait for Copilot's code review of the latest commit. Passing
  CI checks alone does not mean the review is complete.
- Read the review summaries, inline comments, and unresolved review threads.
  Evaluate each finding against the code; do not apply suggestions blindly.
- Fix valid findings and run the relevant tests. Explain rejected findings in
  the pull request with a concrete reason.
- Push fixes and repeat the review process until the latest commit has been
  reviewed and no valid findings remain outstanding.
- Resolve a review thread only after its finding has been addressed or an
  explanation for rejecting it has been posted.
- If a review is unavailable or cannot complete, report the blocker rather
  than claiming the pull request has been reviewed.
- Merge only when the user explicitly requests it, all required checks pass,
  and the review-feedback process above is complete.
