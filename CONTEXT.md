# greenkeep

An autonomous, scheduled maintainer for the owner's own repositories: it finds real problems, fixes them through gated pull requests, and keeps the repositories visibly alive and improving.

## Language

### Scope

**Tended repo**:
A repository explicitly listed in the config that greenkeep is allowed to change. Work repos, forks and archived repos are never tended repos.
_Avoid_: target, managed repo, project

**Focus repo**:
A tended repo the owner marked in the config as a current priority; every run reaches it before the other tended repos.
_Avoid_: pinned repo, favourite, priority repo

**Test root**:
The folder of a tended repo its test suite runs from, as the owner runs it; the repo root unless the config names another.
_Avoid_: test dir, project dir, workspace member

**Bootstrap stage**:
The state of a tended repo whose tests do not yet cover the code; only test-only, safe-category and test-first fix changes are allowed.
_Avoid_: onboarding, warm-up

**Revival**:
The first fix PR in a tended repo whose existing tests cannot run: it makes them run and pass on the oldest working toolchain, without touching application code. Moving to current versions follows later as ordinary small findings.
_Avoid_: migration, modernization, upgrade

**Safe category**:
A kind of change that cannot alter runtime behaviour: docs, README, `.gitignore`, CI workflows, linter configs.
_Avoid_: trivial change, cosmetic fix

### Work

**Finding**:
One concrete problem reported by a deterministic tool (linter, type checker, security scanner, dependency audit, coverage, failing test), or later by the bug-hunting mode.
_Avoid_: issue (reserved for GitHub Issues), bug, suggestion

**Triage**:
The judgment (Jev or LLM) of whether a finding is real, how urgent it is, and whether it is safe to fix automatically.
_Avoid_: classification, filtering

**Plan issue**:
A GitHub Issue opened for a triaged finding; the public record of planned work, closed by its fix PR.
_Avoid_: ticket, task, TODO

**Fix PR**:
A pull request that resolves exactly one finding. Lint that its own change introduces (a rewritten import leaving the block unsorted) is fixed in the same PR; lint that was there before is not.
_Avoid_: patch PR, bot PR, change

**Characterization test**:
A test that pins down the current behaviour of untested code without changing that code.
_Avoid_: snapshot test, coverage test

**Test-first fix**:
A fix PR for a bug in uncovered code that first adds a test reproducing the bug (red before, green after).
_Avoid_: hotfix

**Readiness check**:
A deterministic check that a tended repo looks trustworthy to a stranger (license, description, topics, README install/usage, CI badge, working links); a failed check is a finding.
_Avoid_: polish, SEO, publish check

### Quality

**Gate**:
The automatic verdict that a fix PR may be merged: the full test suite passes and every changed line was covered by tests before the change (or by the test-first fix's own test).
_Avoid_: check, CI, validation

**Attempt**:
One try of the patcher (a fixed model, prompt and temperature 0) to produce a fix PR that passes the gate; a finding gets at most three. An attempt that times out still counts, and the finding waits for the next run.
_Avoid_: retry, run

**Parked finding**:
A finding whose attempts are exhausted; it is labelled `needs-human` and greenkeep never retries it on its own.
_Avoid_: failed finding, skipped, blocked

**Daily budget**:
The maximum number of fix PRs merged per day across all tended repos; security findings neither need nor spend it, plan issues do not count toward it.
_Avoid_: quota, rate limit

### Operation

**Run**:
One scheduled pass of greenkeep over the tended repos, from finding collection to merged fix PRs; every run is recorded. A run counts as done for its local day only once its digest is sent; an interrupted run is simply started again.
_Avoid_: job, sweep, cycle

**Digest**:
The Telegram summary sent after each run: what merged, what was parked, what broke, budget left.
_Avoid_: report, notification

**Pause**:
The state in which runs exit immediately; set by the owner or by auto-pause.
_Avoid_: stop, disable, kill switch

**Cool-down**:
Up to seven days during which greenkeep leaves one tended repo alone, set when a run stopped working that repo because its findings kept getting parked; it ends early once the repo or greenkeep itself changes. The digest mentions it once, when it starts.
_Avoid_: shelved, snooze, backoff

**Auto-pause**:
Pause set by greenkeep itself when its own merged fix PRs get reverted.
_Avoid_: circuit breaker

**Bug hunt**:
The optional mode where an LLM searches one module for bugs itself (Hunter → Skeptic) once a tended repo has no other workable finding; each bug that survives is fixed as a test-first fix, and the test failing before the fix is the referee.
_Avoid_: free agent, exploration mode

### Roles

**Patcher**:
The model that writes the change for one finding.
_Avoid_: fixer, coder, agent

**Reviewer**:
The model that reads a change after it passed the gate and approves or rejects it before it lands; an unclear answer is a rejection. Changes no model wrote are not reviewed.
_Avoid_: judge, critic, second opinion
