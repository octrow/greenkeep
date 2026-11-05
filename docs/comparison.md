# How greenkeep differs

[README](../README.md)

- Dependabot, Renovate: they bump dependencies, with a PR per bump. greenkeep only bumps a dependency for a known vulnerability or a same-major update, and only merges it on a green suite; most of its work is code findings and tests. Both can run on the same repo: greenkeep then merges Dependabot's PRs on a green local suite (a major needs 60% coverage; a red one gets one try by the agent on its own branch).
- ruff `--fix`, pre-commit.ci autofix: they apply only the fixes ruff can make mechanically. greenkeep uses ruff's safe fix when there is one and calls the LLM only for findings ruff can't fix, like `PLW1510` above; both kinds go through the same gate and get their own PR.
- Coding agents (Copilot coding agent, OpenHands, Sweep): an agent decides what to change and how much. Here a deterministic tool picks the problem, the patch is limited to one finding, and nothing merges unless tests already covered every line it touched. The agents can do more; greenkeep aims to be boring and repeatable.
- [patchwork](https://github.com/patched-codes/patchwork): closest in spirit (AutoFix flows). It doesn't own the schedule, the daily budget, the coverage gate or the issue → PR → merge loop, and greenkeep is built around those.
