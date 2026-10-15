# Changelog

## Unreleased

- Patcher and reviewer run on Claude Code (`claude -p`): Sonnet writes, Opus reviews inside the clone with read-only tools, so it judges a change against the repo, not the diff alone.
- Generated tests: the patcher sees the module's existing tests, conftests and pytest/ruff settings, gets a line budget, and writes the file where the repo keeps its tests (`tests/<subpackage>/test_<module>.py`); the gate rejects oversized files.
- Findings: dependency vulnerabilities (pip-audit, bypass the daily budget) and outdated direct dependencies (same major, needs 60% coverage), fixed with `uv lock --upgrade-package`.
- Coverage is measured even when a repo's `addopts` turns on pytest-cov; a green suite with 0% total coverage skips test findings. `uv sync --all-extras` installs test plugins declared as a `dev` extra.
- Cost guards: a repo is dropped for the run after 2 parked findings in a row; rate limits (429/503) and quota errors no longer count as attempts.
- Config file, systemd timer, Telegram digest, pause and auto-pause on a reverted merge, NiceGUI dashboard.

## 0.1.0 - 2026-10-15

First release: one end-to-end slice for Python repos.

- Findings from ruff (S/F/B rules first) and from modules with no tests (characterization tests).
- One LLM patch per finding through OmniRoute, as SEARCH/REPLACE edits; the gate rejects it unless the full suite is green, every changed line was covered before the change, and no new lint findings appear.
- Dry run by default; `--push` opens a plan issue and a fix PR, waits for the required CI checks and squash-merges on green, within a daily `--budget`.
- State in `~/.local/share/greenkeep/state.db`: attempts per finding, a finding that fails too often is parked.
- Install scripts for Linux, macOS and Windows; release workflow with build provenance.
