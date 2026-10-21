# Changelog

## Unreleased

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
- Bug hunt: the Hunter no longer reports races or timing bugs, and the Skeptic rejects behaviour the code, comments or tests mark as a choice (both claims on nicegui-kit's run pool were of these kinds).
- The patcher, reviewer and bug hunt run without the owner's Claude Code settings (hooks, plugins, CLAUDE.md): a role's prompt no longer depends on the machine, and a call is a third faster and cheaper (63s → 40s on a README section). Each call's duration is in the full log.
- A failed dependency upgrade parks at once: `uv lock` fails the same way every time, so a second and third attempt only cost time.
- Readiness checks: `[project]` description, readme and urls, README install and usage sections, a CI badge. Each failed check is a `docs:` fix PR that may touch only README.md or the `[project]` table; the link fields and the badge are written by greenkeep itself, the words by the patcher and then reviewed. A missing LICENSE or README goes to the digest once, under Needs you.
- The repo's own pytest `addopts` apply again (markers, `-m`, `--strict-markers`…): only pytest-cov and xdist options are dropped, since they break greenkeep's coverage.
- A new test file also gets ruff's unsafe fixes for unused variables (F841, RUF059), so one does not cost an attempt; other unsafe fixes stay off (E721's turns `type(x) == int` into `int == int`).
- Bug hunt, opt-in per repo (`hunt = true`): once a repo has nothing else to do, Sonnet suspects up to 3 bugs in its biggest covered module and Opus keeps only those it cannot refute. Each is a test-first fix: the gate requires the new test to fail without the change and the suite to pass with it. An unchanged module is not hunted twice.
- A module that imports a database or broker client (psycopg, asyncpg, redis, …) gets no generated tests: a clone runs no such service, so they only cost attempts.
- Config file, systemd timer, Telegram digest, pause and auto-pause on a reverted merge, NiceGUI dashboard.

## 0.1.0 - 2026-10-15

First release: one end-to-end slice for Python repos.

- Findings from ruff (S/F/B rules first) and from modules with no tests (characterization tests).
- One LLM patch per finding through OmniRoute, as SEARCH/REPLACE edits; the gate rejects it unless the full suite is green, every changed line was covered before the change, and no new lint findings appear.
- Dry run by default; `--push` opens a plan issue and a fix PR, waits for the required CI checks and squash-merges on green, within a daily `--budget`.
- State in `~/.local/share/greenkeep/state.db`: attempts per finding, a finding that fails too often is parked.
- Install scripts for Linux, macOS and Windows; release workflow with build provenance.
