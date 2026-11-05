# Results of the first week

[README](../README.md)

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
