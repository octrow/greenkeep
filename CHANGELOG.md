# Changelog

## 0.1.0 - 2026-10-15

First release: one end-to-end slice for Python repos.

- Findings from ruff (S/F/B rules first) and from modules with no tests (characterization tests).
- One LLM patch per finding through OmniRoute, as SEARCH/REPLACE edits; the gate rejects it unless the full suite is green, every changed line was covered before the change, and no new lint findings appear.
- Dry run by default; `--push` opens a plan issue and a fix PR, waits for the required CI checks and squash-merges on green, within a daily `--budget`.
- State in `~/.local/share/greenkeep/state.db`: attempts per finding, a finding that fails too often is parked.
- Install scripts for Linux, macOS and Windows; release workflow with build provenance.
