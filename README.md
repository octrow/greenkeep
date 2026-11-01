<p align="center"><img src="docs/assets/hero.svg" width="100%" alt="greenkeep: deterministic tools find problems, an LLM writes one patch per finding, merged only when the tests already covered every changed line"></p>

[![test](https://github.com/octrow/greenkeep/actions/workflows/test.yml/badge.svg)](https://github.com/octrow/greenkeep/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/octrow/greenkeep)](https://github.com/octrow/greenkeep/releases/latest)
[![python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<p><b>English</b> | <a href="README.ru.md">Русский</a></p>

greenkeep is a scheduled, gated maintainer for your own Python repositories. Deterministic tools find the problems, an LLM writes one patch per finding, and the fix PR auto-merges only when the full test suite passes and every changed line was already covered by tests. It's for a developer with a pile of pet projects who wants them to keep getting small, safe fixes without reviewing each one by hand.

Every fix PR resolves one finding, and its commit message names the model and the prompt hash that produced the patch.

## What a fix PR looks like

A real one: [whisper-gpu#4](https://github.com/octrow/whisper-gpu/pull/4), ruff `PLW1510`, merged 2026-10-21. Sonnet wrote the patch, Opus reviewed it in the clone, and the PR body is what greenkeep wrote:

```diff
    PLW1510: `subprocess.run` without explicit `check` argument (src/whisper_gpu/transcribe.py:94)

    Finding: `PLW1510` at `src/whisper_gpu/transcribe.py:94`: `subprocess.run` without explicit `check` argument
    Patched by: `claude-sonnet-5-5` (prompt `9f811a4332db`), reviewed by: `claude-opus-5-5`
    Gate: full test suite green; every changed line was covered before the change; no new lint findings.

    Closes #3

     try:
         out = subprocess.run(
             ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
-            capture_output=True, text=True, timeout=10,
+            capture_output=True, text=True, timeout=10, check=False,
         )
```

greenkeep opened plan issue #3 first, and the PR closed it on merge. A dry run (no `--push`) makes the same change in a throwaway clone and only reports it.

## What it found and fixed

The first week on the author's 19 tended repos (2026-10-15 to 2026-10-22), from greenkeep's state database and the PRs themselves:

| | Merged | How |
|---|---|---|
| Vulnerable dependencies | 17 | `uv lock --upgrade-package`, lockfile only: certifi [PYSEC-2026-4320](https://github.com/octrow/buy-smarter/pull/18) in 9 repos on one day, [Pillow](https://github.com/octrow/finance-insights/pull/10), [werkzeug](https://github.com/octrow/finance-insights/pull/12), [aiohttp](https://github.com/octrow/tiktok-lazy-follower/pull/3)… |
| Outdated dependencies | 2 | same, same major only ([alembic](https://github.com/octrow/buy-smarter/pull/15)) |
| Lint findings | 12 | 6 by `ruff --fix`, 6 patched by the LLM, e.g. a file handle that was never closed ([finance-insights#6](https://github.com/octrow/finance-insights/pull/6)) |
| Characterization tests | 1 | [repo-scout#10](https://github.com/octrow/repo-scout/pull/10) |

32 PRs in 16 repos. A separate review of all 32 (Opus, reading every diff) judged 23 good, 5 acceptable and 4 bad:

- [repo-scout#10](https://github.com/octrow/repo-scout/pull/10): 559 lines of tests for a vendored copy of another repo's module, from the first version that had no reviewer and no line budget. Vendored code now gets no findings.
- [whisper-gpu#2](https://github.com/octrow/whisper-gpu/pull/2): `ruff --fix` for unused `noqa` comments also deleted the explanations written after them. That rule now goes to the patcher, which keeps them.
- [linux-coding#23](https://github.com/octrow/linux-coding/pull/23) and [claude-cli#4](https://github.com/octrow/claude-cli/pull/4): correct but worse code — a logger added to a test only to please `S110`, a string reflowed to column 0. The patcher and reviewer prompts now ask for the rule's idiomatic fix.

What it did not manage: 20 findings were parked after three attempts, 16 of them characterization tests (wrong guesses about behaviour, new lint, a reviewer rejection, tests put where the repo does not keep them). Writing good tests for untested code is the hardest job it has, and the one it fails most.

## How it works

1. Clone: each repo you pass is cloned into `~/.cache/greenkeep`; your checkout is never touched. If its tests are red before greenkeep changes anything, the repo is skipped.
2. Find: findings come from deterministic tools only: pip-audit (vulnerable dependencies), ruff (security, bug and pyflakes rules first), coverage (poorly tested modules), outdated dependencies and readiness checks. An opt-in bug hunt adds model-suspected bugs, each fixed test first.
3. Patch: for each finding the LLM gets the finding and its code and returns SEARCH/REPLACE edits. For an untested module it writes characterization tests that pin down what the code does today.
4. Gate: the change is kept only if the full suite passes, every changed line was covered before the change (for a new test file: module coverage grew), and ruff reports nothing new. Then Opus reviews it inside the clone, reading the repo and not just the diff.
5. Merge or park: a passing change is committed (and with `--push`, opened as a PR and merged on green). A finding gets three attempts; after that it is parked and labelled `needs-human`.

Why deterministic findings and not a free-roaming coding agent: [ADR 0001](docs/adr/0001-deterministic-findings-llm-patches.md). Terms used here: [CONTEXT.md](CONTEXT.md).

## Install

Linux and macOS:

```bash
curl -LsSf https://raw.githubusercontent.com/octrow/greenkeep/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/octrow/greenkeep/main/install.ps1 | iex"
```

The script installs [uv](https://docs.astral.sh/uv/) if it's missing, then the wheel from the latest release. Run it again to update; `... | sh -s -- --uninstall` removes greenkeep. To read the script before running it, download `install.sh` and run `sh install.sh`. Or install with uv directly:

```bash
uv tool install git+https://github.com/octrow/greenkeep
```

You also need:

- git, and the [GitHub CLI](https://cli.github.com/) logged in with `gh auth login` (only for `--push`);
- [Claude Code](https://docs.claude.com/en/docs/claude-code) logged in, called through [claude-subprocess](https://github.com/octrow/claude-cli) as plain `claude -p`, so the account Claude Code is logged in to pays: both run inside the clone with read-only tools: Sonnet writes the patches, Opus reviews them.

## Usage

```bash
greenkeep ~/dev/repo-a ~/dev/repo-b          # dry run: nothing leaves the machine except LLM calls
greenkeep ~/dev/repo-a --push --budget 3     # open issues and PRs, merge on green, at most 3 merges today
greenkeep --push                             # the scheduled run, over the repos in the config
```

| Option | Default | What it does |
|---|---|---|
| `--push` | off | Open a plan issue and a fix PR, wait for the required checks, squash-merge |
| `--budget N` | config, 3 | Merged fix PRs today, across all repos |
| `--model NAME` | `claude-sonnet-5-5` | Claude Code model that writes the patches |
| `--review-model NAME` | `claude-opus-5-5` | Claude Code model that reviews each patch inside the clone |
| `--keep` | off | Keep the throwaway clones in `~/.cache/greenkeep` for inspection |

A run makes at most one fix per repo, so it's meant to run on a schedule. State (attempts per finding, merges per day) lives in `~/.local/share/greenkeep/state.db`; a dry run never writes it. Full log: `~/.local/state/greenkeep/greenkeep.log`.

### Config

`~/.config/greenkeep/config.toml`:

```toml
budget = 3            # merged PRs per weekday
weekend_budget = 1
boost_until = 2026-10-30T03:00:00+05:00  # optional: until then, a run every hour
boost_budget = 15                        # and this budget; every repo bug-hunts and lints
                                         # for complexity, SIM, PERF, UP and RUF too
                                         # and Opus plans each hard change before Sonnet writes it
repos = [
  "~/dev/my-repo",
  "owner/name-without-local-checkout",
  { path = "~/dev/monorepo", tests = "app" },  # the suite runs from app/, not the root
  { path = "~/dev/lib", hunt = true },  # bug hunt once nothing else is left to do
]
focus = ["~/dev/my-repo"]   # optional: these run first every run
```

Fixes for vulnerable dependencies neither need nor spend the budget.

### Schedule

```bash
cp contrib/systemd/greenkeep.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now greenkeep.timer
```

The timer fires hourly and greenkeep exits at once if today's digest already went out, so a run cut short by a shutdown starts over after the next boot. During a boost every hourly firing is a run. A run where Claude does not answer (usage limit, logged out, API down) stops there without counting an attempt and leaves the day open, so the next firing tries again.

The unit runs `uv run` from a checkout in `~/dev/greenkeep`; for the installed tool, set `ExecStart=%h/.local/bin/greenkeep --push` and drop `WorkingDirectory`. Any other scheduler works the same way: run `greenkeep --push` hourly with the variables from `~/.config/greenkeep/env` set and `gh`, `claude` and `uv` on `PATH`.

### Digest, pause, dashboard

Telegram digest: `GREENKEEP_TG_BOT_TOKEN` and `GREENKEEP_TG_CHAT_ID` in `~/.config/greenkeep/env` (without them: a desktop notification). Pause: `touch ~/.config/greenkeep/PAUSE`, delete the file to resume. A reverted greenkeep merge pauses every run the same way, and the file says which merge it was.

Dashboard (findings, pause/resume, dry run with a live log, recent events):

```bash
greenkeep-ui            # http://127.0.0.1:8095
```

If the port belongs to another app it moves to the next free one; if a greenkeep dashboard already runs there it asks whether to restart it (default No) and prints its link.

## What a repo needs

- Python, managed with uv: a `pyproject.toml` at the root (`uv sync` must work).
- Tests that pytest can run and that pass today. A repo with no tests at all is fine: greenkeep starts by writing characterization tests.
- For `--push`: a GitHub remote you can push to and merge into.

## How it differs

- Dependabot, Renovate: they bump dependencies, with a PR per bump. greenkeep only bumps a dependency for a known vulnerability or a same-major update, and only merges it on a green suite; most of its work is code findings and tests. Both can run on the same repo.
- ruff `--fix`, pre-commit.ci autofix: they apply only the fixes ruff can make mechanically. greenkeep uses ruff's safe fix when there is one and calls the LLM only for findings ruff can't fix, like `PLW1510` above; both kinds go through the same gate and get their own PR.
- Coding agents (Copilot coding agent, OpenHands, Sweep): an agent decides what to change and how much. Here a deterministic tool picks the problem, the patch is limited to one finding, and nothing merges unless tests already covered every line it touched. The agents can do more; greenkeep aims to be boring and repeatable.
- [patchwork](https://github.com/patched-codes/patchwork): closest in spirit (AutoFix flows). It doesn't own the schedule, the daily budget, the coverage gate or the issue → PR → merge loop, and greenkeep is built around those.

## Status

In daily use on the author's repos since 2026-10-16.

Done: ruff, coverage and dependency findings (upgrades need 60% coverage), the gate, Sonnet patcher + Opus reviewer, issue → PR → merge, config, the hourly timer with one run a day, the digest (with a one-line `Quiet:` summary), pause, cool-down for repos that keep failing, the dashboard, auto-pause after a reverted merge, the opt-in bug hunt (Hunter → Skeptic, then a test-first fix whose test must fail without it), readiness checks (pyproject description/readme/urls, README install and usage sections, CI badge; a missing LICENSE or README is left to you, once, in the digest).

Next, roughly in this order:

1. **Bug hunt in uncovered code**: today it only hunts modules at 50%+ coverage.
2. JS/TS as a second language plugin.

Design: [CONTEXT.md](CONTEXT.md) (glossary), [docs/adr](docs/adr) (decisions).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
