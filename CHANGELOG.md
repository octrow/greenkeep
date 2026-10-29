# Changelog

## Unreleased

- Dashboard: stopping it kills a dry run still going instead of leaving it orphaned, and "Dry run now" says so when the run queue is full instead of doing nothing.
- Dashboard: the budget shown is the one in force now (a boost's included), the status line says the timer fires hourly with one run a day, and Resume no longer promises a run when today's is already done.
- A scheduled run that cloned no repo (no network after boot or sleep) leaves the day open, so the next hourly firing tries again; before, it marked the day done and the next try was tomorrow. The day mark is the date the run started, so a run past midnight no longer takes the next day's run.
- A broken config sends a one-line digest once a day instead of failing every hourly run with only a journal entry.
- A dependency upgrade syncs the venv after `uv lock`: `uv run` alone kept a package that only an extra needs at its old version, so the gate tested the old one.
- A merge `gh` reports as failed but GitHub made (`gh pr view` says MERGED) counts as merged.
- The issue of a finding greenkeep no longer reports is closed with a comment: the owner fixed the code or the line changed. Bug issues are never closed this way; findings of boost-only rules stay open outside a boost; a vulnerability or outdated issue closes only once a newer finding for the same package replaces it (a failed pip-audit or PyPI call must not close anything).
- An outdated dependency is bumped only within the specifiers in `pyproject.toml` and its uv workspace members: an `==` pin gets no finding (all 5 bumps in jobs parked on "no solution").
- One advisory without a fixed release no longer hides the fixable ones of the same package: the finding fixes those and names the rest as "no fix yet".
- A lint finding is attempted only when every line it spans was covered, not just its first: SIM103 in tiktok-lazy-follower spans two lines, and all three attempts failed on the second. A complexity finding (C901, PLR0912, PLR0915) needs its whole function covered: ruff reports it on the name, and a refactor rewrites the body.
- A bug the hunt finds on a line no test ran is dropped: the gate would reject every fix there.
- During a boost a new test file is held to the repo's own ruff rules only; the boost rules are for existing code.
- Identical lines in one file are separate findings (`#1`, `#2`… after the first): fixing one of eight `except Exception:` in tg-bot-template failed the gate as "ruff still reports the finding", and parking one hid all eight.
- Claude not answering stops the run without counting an attempt, like the usage limit: logged out, an API 5xx or overload, or a connection error. Before, each such call burned an attempt with no pause, so a logout parked findings (with a public issue comment) and cooled repos down in seconds. A timeout or an API 4xx ("prompt is too long") still counts: it belongs to one finding, and stopping on it would stop every run there.
- A repo in cool-down still gets its security fixes and its Dependabot merges; only the rest waits. Before, the whole repo was skipped for up to 7 days.
- A merge GitHub refuses right after the PR opens is retried once it has worked out mergeability (`mergeStateStatus` no longer UNKNOWN). Required checks are watched only when the PR is BLOCKED: in a repo without them, `gh pr checks --required` failed and the attempt counted (repo-scout, 2026-10-29), which parks a dependency fix at once.
- During a boost the reviewer's model (Opus) first plans each hard change: tests for untested code, bug fixes and complexity refactors (C901, PLR0912, PLR0915). It reads the repo and names the conventions to follow, what to change or test, and what to avoid; Sonnet writes the change from that plan, and Opus still reviews it. The PR names both (`claude-sonnet-5-5 + plan by claude-opus-5-5`), so the merge rate with and without a plan can be compared. Lint and other changes are not planned.
- During a boost ruff also lints for improvements beyond the repo's own config: complexity (C901, PLR0912, PLR0915), SIM, PERF, UP and RUF (without the ambiguous-unicode rules, which flag Cyrillic text, and RUF100). Complexity goes first. The gate is unchanged, so a refactor lands only where tests covered every line it touches.
- A ruff rule ranks by its whole letter prefix: `SIM` findings ranked as `S` (security) before, so they went first and got `fix:` subjects.
- Claude Code is called directly (`claude -p`), not through OmniRoute: the account the local Claude Code is logged in to pays. Through OmniRoute every call went to the one Claude account OmniRoute held (the owner's Pro), not the Team seat the owner chose. Models are plain ids again (`claude-sonnet-5-5`, `claude-opus-5-5`).
- Outdated dependencies are left to Dependabot in a repo whose `.github/dependabot.yml` updates `uv` or `pip`: both opened the same bumps (4 Dependabot PRs closed as duplicates), and only Dependabot edits an `==` pin, which `uv lock --upgrade-package` cannot move past (all 5 bumps in jobs were parked). Vulnerabilities stay with greenkeep: it merges them the same day.
- During a boost every tended repo bug-hunts once nothing else is left, not only those with `hunt = true`.
- Boost: `boost_until` (a time with its offset) and `boost_budget` (default 15) in the config. Until then every hourly firing is a run and the budget is `boost_budget`, for nights when the owner wants to spend more of the subscription.
- Claude's usage limit stops the run at the repo that hit it (before, each later repo cloned, synced and ran its suite only to hit it too: 8 repos on 2026-10-26) and leaves the day open, so the next hourly firing tries again.
- Dependabot: after its own fixes, greenkeep merges the repo's open Dependabot PRs whose every `from X to Y` is a same-major update (same minor for 0.x), once its local suite passes on the PR's head (up to 3 per repo per run; a red head is not retried until Dependabot pushes again). Actions-only PRs merge only on green checks. CI in the private repos no longer starts (no Actions minutes), and 34 such PRs had piled up. Majors stay with the owner; these merges spend no budget.
- A characterization test retry corrects the previous attempt's file (shown to the patcher with the failure) instead of writing a new one: re-rolls failed a different way each time (a wrong assert, then over the line budget, then new lint).
- `greenkeep <repo>` on a repo from the config keeps its entry, so a dry run uses the repo's test root and bug hunt setting.
- From a review of the first 32 merged PRs (4 judged bad):
  - A lint fix may change only the file of its finding.
  - An unused `noqa` with an explanation after its codes (`RUF100`) goes to the patcher: ruff's own fix deleted the explanation too (whisper-gpu#2). The patcher keeps such text as a plain comment, uses the rule's documented fix (`contextlib.suppress` for S110) and never adds a logger only to please the linter (linux-coding#23); the reviewer rejects such workarounds.
  - Vendored code (a `vendor`/`_vendor`/`third_party` folder, or a `VENDORED.md` beside or above the module) gets no findings or hunts: repo-scout#10 pinned a byte-identical copy of nicegui-kit's run pool in 559 lines.
  - Characterization tests leave output that looks like a bug unasserted, and import the module the way the repo's tests do. The suggested import is relative to the test root: in jobs, tests run from `pipeline/` and the prompt said `pipeline.gmail_api_client`, which failed three attempts with `ModuleNotFoundError`.
- A new test file gets the long name (`test_<package>_<module>.py`) when a test file with the short name exists anywhere in the repo, not only in its folder: a root `test_extract.py` and a new `tests/test_extract.py` made pytest stop on "import file mismatch", which parked two cc-bench findings.
- A repo without a `tests/` folder that keeps its test files at the test root gets the new one there too: the reviewer rejected every cc-bench test file put in a new `tests/` (a dry run then merged the one for `arms.py`).
- Each run removes the clones older than a day that an interrupted run left in `~/.cache/greenkeep` (a shutdown ends a run before its cleanup); 1.7 GB had piled up.
- Patcher and reviewer run on Claude Code (`claude -p`): Sonnet writes, Opus reviews inside the clone with read-only tools, so it judges a change against the repo, not the diff alone.
- Generated tests: the patcher sees the module's existing tests, conftests and pytest/ruff settings, gets a line budget, and writes the file where the repo keeps its tests (`tests/<subpackage>/test_<module>.py`); the gate rejects oversized files.
- Findings: dependency vulnerabilities (pip-audit, bypass the daily budget) and outdated direct dependencies (same major, needs 60% coverage), fixed with `uv lock --upgrade-package`.
- Coverage is measured even when a repo's `addopts` turns on pytest-cov; a green suite with 0% total coverage skips test findings. `uv sync --all-extras` installs test plugins declared as a `dev` extra.
- Cost guards: a repo is dropped for the run after 2 parked findings in a row; rate limits (429/503) and quota errors no longer count as attempts.
- Cool-down: a run that stops a repo for parked findings in a row also leaves it alone for up to 7 days; a new commit in the repo or in greenkeep ends that early. A patcher timeout counts as an attempt and defers the finding to the next run.
- Security fixes no longer spend the daily budget, and a spent budget still visits every repo for them.
- The timer fires hourly; one scheduled run per day, done once its digest is sent, so an interrupted run starts over. The digest ends with `Quiet: N clean, M waiting for budget (…), K in cool-down`.
- The patcher runs inside the clone with read-only tools too: without them it asked to read the modules under test as plain text and returned no code.
- Merges are dated by their own `merged_at`: a later write to an old merge (the revert check backfilling its commit) no longer counts it against today's budget.
- Test root: a config entry `{ path = ..., tests = "pipeline" }` runs that repo's suite from a subfolder; coverage paths stay repo-relative and new tests go under it.
- `uv sync --all-packages`: workspace members' dependencies are installed for the tests. A patcher reply without a code block is logged in full.
- Bug hunt: an unchanged module is left alone after two empty hunts in a row, not one (the Hunter saw the same real bug in about half its looks).
- A bug fix may append its test to the module's existing test file (added lines only, no conftest.py): the reviewer rejected new files that re-created the repo's fixtures.
- Models are pinned by provider (`claude/claude-sonnet-5-5`, `claude/claude-opus-5-5`): through OmniRoute a bare `claude-sonnet-5-5` went to a free combo and was answered by another model under the same label, which caused the 600s timeouts. Hunter and Skeptic run at medium effort with the module in the prompt and a 300s limit; one hunt call went from a timeout to 15s.
- Bug hunt: the Hunter no longer reports races or timing bugs, and the Skeptic rejects behaviour the code, comments or tests mark as a choice (both claims on nicegui-kit's run pool were of these kinds).
- The patcher, reviewer and bug hunt run without the owner's Claude Code settings (hooks, plugins, CLAUDE.md): a role's prompt no longer depends on the machine, and a call is a third faster and cheaper (63s → 40s on a README section). Each call's duration is in the full log.
- A failed dependency upgrade parks at once: `uv lock` fails the same way every time, so a second and third attempt only cost time.
- Readiness checks: `[project]` description, readme and urls, README install and usage sections, a CI badge. Each failed check is a `docs:` fix PR that may touch only README.md or the `[project]` table; the link fields and the badge are written by greenkeep itself, the words by the patcher and then reviewed. A missing LICENSE or README goes to the digest once, under Needs you.
- The repo's own pytest `addopts` apply again (markers, `-m`, `--strict-markers`…): only pytest-cov and xdist options are dropped, since they break greenkeep's coverage.
- A new test file also gets ruff's unsafe fixes for unused variables (F841, RUF059), so one does not cost an attempt; other unsafe fixes stay off (E721's turns `type(x) == int` into `int == int`).
- Bug hunt, opt-in per repo (`hunt = true`): once a repo has nothing else to do, Sonnet suspects up to 3 bugs in its biggest covered module and Opus keeps only those it cannot refute. Each is a test-first fix: the gate requires the new test to fail without the change and the suite to pass with it. An unchanged module is left alone after two empty hunts.
- A module that imports a database or broker client (psycopg, asyncpg, redis, …) gets no generated tests: a clone runs no such service, so they only cost attempts.
- Config file, systemd timer, Telegram digest, pause and auto-pause on a reverted merge, NiceGUI dashboard.

## 0.1.0 - 2026-10-15

First release: one end-to-end slice for Python repos.

- Findings from ruff (S/F/B rules first) and from modules with no tests (characterization tests).
- One LLM patch per finding through OmniRoute, as SEARCH/REPLACE edits; the gate rejects it unless the full suite is green, every changed line was covered before the change, and no new lint findings appear.
- Dry run by default; `--push` opens a plan issue and a fix PR, waits for the required CI checks and squash-merges on green, within a daily `--budget`.
- State in `~/.local/share/greenkeep/state.db`: attempts per finding, a finding that fails too often is parked.
- Install scripts for Linux, macOS and Windows; release workflow with build provenance.
