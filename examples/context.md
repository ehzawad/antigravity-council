# Intent Capsule

## Verbatim request
Example only: Diagnose and fix the reported concurrency defect with two independent workers, one on the model I pinned and one on agy's default. Don't commit or push.

## Authorized outcome
A diagnosis grounded in the actual source and observed evidence, plus a local fix and a regression test left uncommitted in the working tree.

## Hard constraints and non-goals
Preserve unrelated files and uncommitted changes. No refactoring beyond the defect. No commits, pushes, publishing, deployment, or changes to remote services.

## Explicit lenses or panel requests
One invariant check and source fix on the user-pinned model; one independent state-transition trace and regression test on agy's default model.

## Authority boundaries
Both workers run with full access, the default: they may read any file, edit files, run local commands and tests, and use the network and web search. Both may edit this worktree: the invariant role owns the source fix and the transition role owns the test file. No commits, pushes, deployment, publishing, account changes, or other external mutations.

## Acceptance evidence
Identify the relevant paths and transitions; distinguish a demonstrated failure from a hypothesis; report the exact commands run and their output. The host re-runs the tests on the settled worktree before claiming the fix.

# Current Phase Brief
This is an illustrative template, not a ready-made project brief. Replace it with the actual error, relevant source paths, current diff, evidence, counterevidence, unresolved questions, and which files each editing role owns. Update the capsule to the real request before the first launch; preserve it across subsequent rounds.
