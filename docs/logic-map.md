# Logic map

How one run of greenkeep turns the tended repos into merged fix PRs and a Telegram digest.
The map describes the code as it is today, step by step.
Each step says what it decides, where the flow branches, and which module does it.

Read `CONTEXT.md` first for the terms (tended repo, finding, gate, attempt, parked, budget).
Known gaps and fixes in progress are in `docs/plans/2026-10-29-logic-map-fixes.md`.

Rule: if you change a step or a branch, update this file in the same commit.
`tests/test_logic_map.py` checks that every `Module:` line points to a real module.
The Russian mirror is `docs/logic-map.ru.md`; this file is the source of truth.

## The flow at a glance

```
systemd timer (hourly) / CLI / dashboard button
  │
  ├─ 1  Pause .............. is ~/.config/greenkeep/PAUSE there?
  ├─ 2  Once a day ......... did a scheduled run already finish today (boost: ignored)?
  ├─ 3  Sweep .............. which leftover clones are older than a day?
  ├─ 4  Targets ............ which repos, which budget, is a boost on?
  ├─ 5  Options ............ which models write, review and plan?
  ├─ 6  Repo order ......... Focus first, then the least recently merged
  │   per repo:
  ├─ 7  Cool-down .......... resting after two parked findings? → security and Dependabot only
  ├─ 8  Clone .............. fresh clone; boost adds extra ruff rules
  ├─ 9  Revert check ....... did the owner revert a greenkeep merge? → auto-pause
  ├─ 10 Baseline tests ..... is the suite green in a clean clone, and what does it cover?
  ├─ 11 Findings ........... ruff, coverage, pip-audit, outdated, readiness
  ├─ 12 Owner notes ........ what only the owner can settle (license, README)
  ├─ 13 Work order ......... security, lint bugs, tests, upgrades, readiness, style
  ├─ 14 Candidates ......... workable, not vendored, attemptable; first 10
  ├─ 15 Bug hunt ........... only when no candidate is left
  │   per candidate, until one merges:
  ├─ 16 Budget ............. does the day's budget still allow it?
  ├─ 17 Plan issue ......... open one GitHub issue per finding (push only)
  ├─ 18 Produce ............ ruff --fix, uv lock, a template, or Claude (with an Opus plan)
  ├─ 19 Gate ............... scope, no suppressions, no new lint, suite green, kind check
  ├─ 20 Review ............. Opus reads the diff in the clone (LLM changes only)
  ├─ 21 Land ............... commit, push, PR, squash-merge
  ├─ 22 Failure ............ count the attempt, park, cool the repo down
  │   after the repo's own work:
  ├─ 23 Dependabot ......... merge its same-major PRs on a green local suite
  │
  ├─ 24 Run-level stops .... auto-pause, Claude unavailable, a broken repo
  └─ 25 Digest ............. Telegram text; mark the day done
  │
merged PRs on GitHub, issues, state.db, the digest
```

The dashboard (`greenkeep-ui`) never enters the main path: it reads state.db and the log, deletes PAUSE, and starts a dry run.

## Where the flow splits by finding kind

One registry decides most of it: `kinds.KINDS` maps `Finding.kind` to a `Kind` with priority, produce, scope and after-checks.
A few splits live outside the registry, decided by rule-code prefixes and fingerprint prefixes.

| Kind / variant | Switched on by | Steps it changes | What changes |
|---|---|---|---|
| `vuln` | pip-audit finds a fixed release for every advisory | 13, 16, 18, 19, 22 | first in order; budget-exempt; `uv lock --upgrade-package`; only `uv.lock` may change; parks on first failure |
| `outdated` | `uv tree --outdated`, no Dependabot for uv/pip | 11, 13, 14, 18, 22 | after tests in order; needs the budget; skipped below 60% total coverage; `uv lock`; parks on first failure |
| `ruff` S/F/B (rank ≤ 2) | rule-code letter prefix | 13, 21 | ordered as bugs; commit type `fix` |
| `ruff` other | rule-code prefix | 13 | style, last; commit type `chore` |
| `ruff` safe-fixable, any rank | ruff's `fix.applicability == "safe"` | 18, 20 | `ruff --fix`, no LLM, no review |
| `ruff` C901/PLR0912/PLR0915 | `kinds.HARD_RULES` | 18 | boost: Opus plans first |
| `untested` | module < 50% covered, ≥ 5 statements | 14, 18, 19 | new test file only, within a line budget; coverage must grow; boost: Opus plans |
| `ready` | readiness check fails | 18, 19 | template fix for readme-field/urls/ci-badge, Claude for words; one file, one `[project]` field |
| `bug` | the hunt (step 15) | 14, 18, 19 | skips step 14; source fix plus an added test that fails on the unfixed code; boost: Opus plans |
| boost | `boost_until` in the future | 2, 4, 5, 8, 15 | hourly runs, budget 15, extra ruff rules, `hunt` on for every repo, Opus planner |

Examples:
- `F401` in a covered line → step 18 `ruff --fix`, no review; commit `fix: F401: …`, PR titled `F401: …`.
- `SIM103` during a boost → Claude patch, gate, Opus review; a changed line the tests never ran fails the gate.
- `vuln:certifi:PYSEC-…` with the budget spent → still worked, merged, not counted.

Budget exemption is read twice: `Kind.exempt_from_budget` and `state.BUDGET_FREE` (`vuln:`) in `State.merged_today`; `state` cannot import `kinds`, so a test keeps them equal.

Module: `kinds`, `findings`, `state`

## Entry points

All entry points call `cli.main`. They differ in defaults.

| Entry | Repos | Push | Budget | Boost | Notes |
|---|---|---|---|---|---|
| systemd `greenkeep.service`, hourly timer | config | yes | config (defaults 3, weekend 1, boost 15) | from config | runs `greenkeep --push`: a scheduled run |
| CLI `greenkeep --push` | config | yes | config | from config | also a scheduled run: once-a-day guard, `record_run` |
| CLI `greenkeep` | config | no (dry run) | config | from config | see "Dry run" below |
| CLI `greenkeep owner/name …` | named; config entry kept if listed | flag | `--budget` or **3**, never the config's | **planner only**, no extra rules, no forced hunt | no once-a-day guard; push mode counts today's merges of every repo against the 3 (#7) |
| dashboard "Dry run now" | config | no | config | from config | `uv run --project <checkout> greenkeep`; does nothing while PAUSE exists |

Dry run: an in-memory state, so it starts with no merges, attempts, cool-downs, hunt marks or reverts, and may "merge" a full budget whatever the real day did.
It still spends the subscription (patcher, planner, review, hunt), sweeps old clones, clones and syncs, calls PyPI, pip-audit and `gh pr list`, and writes the shared log.

`--model` (default `claude-sonnet-5-5`) writes patches, README and description text, and hunts; `--review-model` (default `claude-opus-5-5`) reviews, doubts hunt claims and, in a boost, plans.
Every Claude call is `claude -p` direct (no OmniRoute), read-only tools `Read,Grep,Glob`, no user settings: the account Claude Code is logged in to pays.

Module: `cli`, `ui`, `config`

## Steps

### 1. Pause

Decides: whether this run does anything at all.
Branches:
- `~/.config/greenkeep/PAUSE` exists → log "paused", exit 0, no digest;
- absent → go on.
The file is written by step 24 (auto-pause) and deleted by the owner or the dashboard.
Module: `cli`, `config`

### 2. Once a day

Decides: whether a scheduled run already did today's work.
Branches:
- scheduled (`--push`, no repos named) and mark `run` = today and no boost → exit 0;
- a boost is on → run every hour regardless;
- named repos or a dry run → never skipped.
The mark holds the date the run started, the same date the guard checks.
Fails: a broken config during the boost check → treated as no boost.
Module: `cli`, `state`

### 3. Sweep

Decides: which clones in `~/.cache/greenkeep/run-*` are left over from a killed run.
Branches: mtime older than 24 h → removed; others kept (another run may own them).
Side effect: runs in dry runs too.
Module: `workspace`

### 4. Targets

Decides: the repo list and the day's merge budget.
In: argv, `config.toml`. Out: `list[Target]`, budget.
Branches:
- repos named → those, with their config entry (test root, hunt) if listed; budget `--budget` or 3;
- none named → config `repos`; budget `--budget` or `budget_at(now)`;
- boost (`now < boost_until`) → every target gets `hunt=True, boost=True`; budget `boost_budget` (15);
- weekday ≥ Saturday → `weekend_budget` (1); else `budget` (3).
Fails: missing or bad config with no repos named → `ConfigError`, exit 1; push → a one-line digest once a day (mark `config-error`); `boost_until` without an offset is a config error.
Module: `cli`, `config`

### 5. Options

Decides: which model writes, reviews and plans.
Branches: boost → `planner = review_model` (Opus plans hard changes, step 18); else no planner.
The boost is read from the config again here, also for named-repo runs.
Module: `cli`, `core`

### 6. Repo order

Decides: which repos get the budget first.
Order: Focus repos first, then by `last_merge` ascending (never merged first).
A spent budget still visits every repo: security findings and Dependabot merges do not need it.
A cooling repo still gets security fixes and Dependabot merges (step 7).
Module: `cli`, `state`

### 7. Cool-down

Decides: whether the repo gets its full run or only what cannot wait.
Branches:
- no cool-down mark → work it;
- `today < until` and origin HEAD unchanged (or unreachable) and greenkeep's own HEAD unchanged → count "in cool-down", clone it anyway: only budget-exempt findings (`vuln`) are worked, no hunt, then Dependabot (step 23);
- otherwise → lift the mark, work it.
Cost: one `git ls-remote` per cooling repo, plus the clone, sync and baseline suite.
Module: `run`, `state`, `workspace`

### 8. Clone

Decides: the throwaway workspace the run may change.
In: `Target`. Out: `Workspace`.
Branches:
- local checkout → its `origin` URL, `--reference-if-able` the checkout, copy its `.env`;
- `owner/name` → clone `https://github.com/owner/name.git`;
- boost → `extra_rules` = C901, PLR0912, PLR0915, SIM, PERF, UP, RUF (minus RUF001-003, RUF100) for every ruff call in this repo, the no-new-lint check included, except on files the change adds (step 19).
Fails: no origin or clone error → `WorkspaceError`, the repo is a problem line (step 24).
The owner's checkout is never written.
Module: `workspace`, `config`

### 9. Revert check

Decides: whether the gate let something wrong through.
Branches:
- a commit on the base branch says "This reverts commit <sha>" for a recorded greenkeep merge → mark it `reverted`, raise `AutoPause`;
- a merge without a stored sha (push) → fetch it from GitHub first (backfill).
Dry run: the in-memory state has no merges, so the check never fires.
Module: `reverts`, `state`, `github`

### 10. Baseline tests

Decides: whether the repo can be worked on, and the coverage every gate compares against.
In: clone. Out: `TestRun` (ok, coverage JSON, output, no_tests).
Branches:
- no `pyproject.toml` → `WorkspaceError` (revival is not built);
- `uv sync --all-extras --all-packages`; a test root with its own pyproject syncs there too;
- suite red or hangs 30 min → problem line "baseline tests are red", the repo stops here (no Dependabot either);
- exit 5 (no tests) → green; coverage still reports never-imported modules at 0% (`--source=.`), which yields `untested` findings.
Coverage: `coverage run --source=. --omit=tests/*,.venv/*`, pytest-cov and xdist options dropped from the repo's addopts.
Module: `run`, `workspace`

### 11. Findings

Decides: the full list of problems in the repo.
In: clone, baseline. Out: `Baseline` (tests, lint fingerprints, ordered findings).
Push mode first: `gh label create greenkeep / needs-human --force` (fails on a non-GitHub origin).
Sources:
- ruff JSON with the repo's own config (+ boost rules); fingerprint `ruff:<code>:<path>:<digest of the line text>`, no line number; identical lines in a file get `#1`, `#2`… in line order, the first keeps the plain one; the last line of ruff's range is kept (`end_line`);
- `untested`: modules < 50% covered with ≥ 5 statements, if coverage measured anything or there are no tests;
- `vuln`: `uv export` → `pip-audit`, only with a tracked `uv.lock`; target = lowest version fixing every advisory that has a fixed release, same line preferred; advisories with none go to the message ("no fix yet"); fingerprint holds the fixable advisory ids;
- `outdated`: `uv tree --outdated --depth 1` + PyPI, newest stable on the same major (minor for 0.x) that the specifiers in `pyproject.toml` and its uv workspace members allow (an `==` pin → no finding); skipped when `.github/dependabot.yml` (not `.yaml`) has a `uv`/`pip` ecosystem;
- `ready`: pyproject `description`/`readme`/`urls`, README install/usage heading, CI badge.
Fails: PyPI errors → no target, no finding; `uv export`/`uv tree` failure or non-JSON pip-audit output → the repo is a problem line (no fixes, no Dependabot).
Module: `run`, `core`, `findings`, `deps`, `ready`, `workspace`

### 12. Owner notes

Decides: what goes to the owner once instead of becoming a finding.
Notes: no LICENSE-like file; no `README.md`.
Branches: push → once per note (mark `told:<repo>:<note>`, set before the digest is sent); dry run → every time.
Module: `run`, `ready`

### 13. Work order

Decides: which finding is tried first.
Order by kind priority: security (vuln) → lint bugs (ruff S/F/B) and hunted bugs → untested → outdated → readiness → lint style. Stable inside a kind; ruff is pre-sorted S, F, B, then C/PLR, other, E, W.
Rank uses the whole letter prefix: `SIM` is not `S`.
Module: `kinds`, `findings`

### 14. Candidates

Decides: what this repo may work on now.
Kept: state `open` or unseen; not vendored (vendor dir or a `VENDORED.md` above it); `attemptable`:
- ruff: every line from the finding's first to its last could pass the coverage rule; for C901/PLR0912/PLR0915 the last line is the function's end (`ast`); always in test files;
- untested: no test file at the target path yet, and no database/broker client import;
- outdated: suite exists and total coverage ≥ 60%;
- vuln, ready: always.
Cut: the first 10. Hunted bugs (step 15) skip this step and the cut.
Module: `run`, `kinds`, `gate`, `findings`

### 15. Bug hunt

Decides: whether to look for bugs, and in which module.
Branches:
- candidates exist, or `hunt` off, or budget spent → no hunt;
- else pick the biggest module ≥ 50% covered, ≥ 5 statements, not hunted empty twice as it is now (mark `hunt:<repo>:<path>` = sha + count).
Hunter (`--model`, 5 min) claims ≤ 3 bugs as JSON; Skeptic (`--review-model`) keeps what it cannot refute; each kept claim on a real line that could pass the coverage rule is a `bug` finding.
Fails: timeout, CLI error or unreadable JSON → warning, no findings, module not marked; Claude unavailable (`llm.unavailable`) → raised (step 24).
Module: `hunt`, `llm`, `run`

### 16. Budget

Decides: whether this candidate may be worked today.
Branches: `vuln` → always; else `merged_today` (all repos, local day) `< budget`; over → skip, the repo may end in "waiting for budget".
Module: `run`, `kinds`, `state`

### 17. Plan issue

Decides: the public record for the finding.
Branches: push and no issue in state → `gh issue create` with label `greenkeep`; else reuse; dry run → none.
Closing (push, right after the baseline, before the cool-down filter): an open or parked finding with an issue that the baseline no longer reports, nor a lint with the boost rules → `gh issue close` with a comment, the issue is cleared in state. Kept: `bug:` (never in a baseline); `untested:` when coverage was not measured; `vuln:`/`outdated:` unless the same package has a newer finding (a failed pip-audit or PyPI call reports nothing). Otherwise the merged PR's "Closes #N" closes it.
Module: `run`, `github`, `state`

### 18. Produce

Decides: the change for one attempt, made after `git reset --hard` + `clean -fdx` (keeps `.env`, `.venv`).
Branches by kind:
- ruff, safe-fixable, first attempt of this run → `ruff --fix --select <rule>` on the file (every occurrence of the rule), then the new fixable lint it caused; no LLM;
- ruff otherwise → Claude (`FIX_SYSTEM`, file or ±40 lines around it) → SEARCH/REPLACE on tracked files;
- untested → Claude (`TEST_SYSTEM`, module, related tests, conftests, settings, line budget × 0.85, this run's previous file on retry) → one python block → test file, then the repo's safe fixes and unused-name fixes;
- bug → Claude (`BUG_SYSTEM`) → source edit + new or appended test;
- vuln/outdated → `uv lock --upgrade-package name==version`;
- ready → template fix (readme-field, urls, ci-badge) or Claude (`READY_SYSTEM`).
Boost and a hard change (untested, bug, C901/PLR0912/PLR0915) → the planner (Opus, effort high) writes a ≤ 25-line plan first; the patcher gets it appended; the PR says `<model> + plan by <planner>`.
Retry inside a run: the last failure, redacted (`.env` values), first line plus its last 1500 chars, is appended to the prompt. A new run starts without it.
Fails: no blocks, SEARCH not unique, path not tracked → `EditError` (an attempt).
Module: `kinds`, `llm`, `edits`, `deps`, `ready`, `workspace`

### 19. Gate

Decides: whether the change is safe to merge without a human.
Checks in order:
1. something changed;
2. kind scope: lint → only its file, changed lines covered before, ruff reports fewer copies of its line than the baseline did; untested → only test files, within the line budget; bug → its file + added test lines only, source lines covered, each new test fails on the unfixed code (exit 1); dependency → only `uv.lock`; ready → only its file and `[project]` field, the check passes;
3. no added `noqa`, `type: ignore`, `pragma: no cover`, `ruff:`, `pylint: disable`;
4. no ruff finding whose fingerprint was not in the baseline (boost rules included; a file the change adds gets the repo's own rules only);
5. full suite green (`uv run`; a dependency kind runs `uv sync --all-extras --all-packages` after `uv lock`, or `uv run` would keep a package only an extra needs at its old version), not "no tests" where the baseline had tests;
6. untested → module coverage grew.
Fails: `GateFailure` with the reason (an attempt).
Module: `run`, `gate`, `kinds`, `workspace`

### 20. Review

Decides: the last word on an LLM change.
Branches: deterministic change (ruff --fix, uv lock, template) → no review; else `--review-model`, effort high, 15 min, inside the clone with the change applied.
Fails closed: anything but `{"approve": true}` → `GateFailure("reviewer rejected …")`.
Module: `run`, `llm`

### 21. Land

Decides: how the change reaches the base branch.
Commit on `greenkeep/<sha1(fingerprint)[:10]>`: `<type>: <title>` ≤ 72 chars, body with finding, model, reviewer, gate note, `Closes #N`. PR title: the finding's title.
Branches:
- push → force-push, reuse an open PR on that branch or create one, `gh pr merge --squash --delete-branch`; refused → poll `mergeStateStatus` while UNKNOWN (10 × 3 s); BLOCKED → `gh pr checks --watch --required --fail-fast` (30 min); then merge once more; still refused but `gh pr view` says MERGED → merged;
- dry run → local commit only, diff to the log.
State: `merged`, PR, merge sha. One merge per repo per run: the baseline is stale after it.
Fails: a merge still refused (conflict, behind, a required review, red CI) or a gh/git error → an attempt, the change is made again next attempt; a `ValueError` from gh output → run-level (step 24), nothing counted.
Module: `run`, `github`, `state`

### 22. Failure

Decides: what a failed attempt costs.
Branches:
- Claude unavailable (`llm.unavailable`: `UsageLimitError`, `NotLoggedInError`, or a Claude error with an API 5xx, overload or connection failure) → not counted, raised (step 24);
- any other Claude error (timeout, API 4xx such as "prompt is too long", unreadable reply) → counted like a bad change;
- counted: attempts + 1, next attempt at once; dependency kinds park at once (`retries = False`); others park at 3;
- parked → digest "Needs you", issue comment with the one-line reason, label `needs-human`;
- timeout → counted, then stop this finding for the run, try the next candidate;
- 2 parked in this repo in this run → cool-down 7 days (step 7), no more findings; Dependabot still runs.
Module: `run`, `state`, `github`

### 23. Dependabot

Decides: which open Dependabot PRs greenkeep merges for the owner.
Runs after the repo's own work, only on a green baseline, only for GitHub origins.
Branches per PR:
- conflicting, or red on this head before (mark `dependabot:<repo>:<n>`) → skip;
- any `from X to Y` outside `<details>` not same-major (same-minor for 0.x), or none → left to the owner;
- only `.github/` files → merge on CI's own green checks; no checks → wait;
- else, up to 3 per run → check out the PR head, sync, full suite; green → merge with `--match-head-commit`; red → mark the head.
A refused merge waits up to 30 min for required checks; a failure leaves no mark, so the PR is tested again next run.
Dry run: logs "would merge", no suite run. No budget, no finding, no issue.
Module: `dependabot`, `deps`, `github`

### 24. Run-level stops

Decides: what one repo's exception does to the rest of the run.
Branches:
- `AutoPause` → write PAUSE (push), digest line, stop the run; the day is still marked done;
- Claude unavailable (step 22) → digest line "Claude unavailable at <repo>, run stopped: <reason>", `limited`, stop; the digest is sent, the day is not marked, the next hourly run tries again (and sends its digest too);
- anything else → log the traceback, first line to "Problems", next repo.
The clone is deleted after each repo unless `--keep`.
Module: `cli`, `run`, `reverts`

### 25. Digest

Decides: what the owner hears and whether the day counts as done.
Text: counts, Merged (own and Dependabot), Needs you (parked + owner notes), Problems, Quiet (clean, waiting, cooling), budget left.
Branches:
- push → Telegram if `GREENKEEP_TG_BOT_TOKEN`/`CHAT_ID` are set, else a desktop notification with the first line only; scheduled, not `limited`, and at least one repo cloned (or no repos) → mark `run` = the day the run started;
- dry run → the log only.
A run that cloned no repo (network down after boot or sleep) leaves the day open: the next hourly run tries again.
Module: `digest`, `cli`, `state`

### Output

GitHub: plan issues, squash-merged fix PRs, merged Dependabot PRs, `needs-human` labels.
`~/.local/share/greenkeep/state.db`: `finding` rows (status open/merged/parked/reverted, attempts, issue, PR, sha) and `mark` rows (run, cool-down, hunt, told, dependabot).
`~/.local/state/greenkeep/greenkeep.log`: INFO story plus DEBUG full failures and plans, 5 MB × 4 files.
Module: `state`, `digest`, `cli`

## Side flows

### Dashboard

`greenkeep-ui` serves on 127.0.0.1:8095 (next free port; restarts or reuses a running dashboard).
Opens state.db read-write (runs the schema and migrations); reads it and the log tail every 30 s; "Resume" deletes PAUSE; "Dry run now" starts `greenkeep` with a live log. A full run queue (20 runs) → a notice instead; stopping the dashboard kills a run still going.
It shows the budget of the moment (a boost's included) and says the timer fires hourly with one run a day.
Feeds: step 1 (resume), the dry-run entry point.
Module: `ui`, `port`

## Known gaps

Details and fixes are in the plan (number in brackets).
- No open HIGH or MEDIUM findings; the LOW ones and the owner's questions are in the plan.
