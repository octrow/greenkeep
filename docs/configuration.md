# Configuration, schedule, digest

[README](../README.md)

## Config

`~/.config/greenkeep/config.toml`:

```toml
budget = 3            # merged PRs per weekday
weekend_budget = 1
boost_until = 2026-10-30T03:00:00+05:00  # optional: until then, a run every hour
boost_budget = 15                        # and this budget; every repo bug-hunts and lints
                                         # for complexity, SIM, PERF, UP and RUF too
                                         # and Opus plans each hard change before Sonnet writes it
login_hours = "Thu 21:00 - Fri 03:00"  # optional: the Claude Code login pays only then;
                                       # outside them calls go through OmniRoute's `claude/`
                                       # connection (needs omniroute and OMNIROUTE_API_KEY)
backup = "~/greenkeep-state"  # optional: a checkout of a private repo; after each scheduled
                              # run state.db's SQL dump is committed and pushed there.
                              # Restore: sqlite3 ~/.local/share/greenkeep/state.db < state.sql
repos = [
  "~/dev/my-repo",
  "owner/name-without-local-checkout",
  { path = "~/dev/monorepo", tests = "app" },  # the suite runs from app/, not the root
  { path = "~/dev/lib", hunt = true },  # bug hunt once nothing else is left to do
]
focus = ["~/dev/my-repo"]   # optional: these run first every run
```

Fixes for vulnerable dependencies neither need nor spend the budget.

## Schedule

```bash
cp contrib/systemd/greenkeep.* ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now greenkeep.timer
```

The timer fires hourly and greenkeep exits at once if today's digest already went out, so a run cut short by a shutdown starts over after the next boot. During a boost every hourly firing is a run. A run where Claude does not answer (usage limit, logged out, API down) stops there without counting an attempt and leaves the day open, so the next firing tries again.

The unit runs `uv run` from a checkout in `~/dev/greenkeep`; for the installed tool, set `ExecStart=%h/.local/bin/greenkeep --push` and drop `WorkingDirectory`. Any other scheduler works the same way: run `greenkeep --push` hourly with the variables from `~/.config/greenkeep/env` set and `gh`, `claude` and `uv` on `PATH`.

## Digest, pause, dashboard

Telegram digest: `GREENKEEP_TG_BOT_TOKEN` and `GREENKEEP_TG_CHAT_ID` in `~/.config/greenkeep/env` (without them: a desktop notification). Pause: `touch ~/.config/greenkeep/PAUSE`, delete the file to resume. A reverted greenkeep merge, or one after which CI on main went red (greenkeep reverts that one itself), puts its repo into a week's cool-down instead; the digest says which.

Dashboard (findings, pause/resume, dry run with a live log, recent events):

```bash
greenkeep-ui            # http://127.0.0.1:8095
```

If the port belongs to another app it moves to the next free one; if a greenkeep dashboard already runs there it asks whether to restart it (default No) and prints its link.

## Logs

Everything is in `~/.local/state/greenkeep/`, 200 MB in total:

| File | What | Cap |
|---|---|---|
| `greenkeep.log` | the scheduled run (`greenkeep --push`): INFO and up, one line per record | 20 MB × 5 files = 100 MB |
| `events.jsonl` | one JSON line per repo run (outcome, merged, failures with the reason, Claude and `gh` calls with ms, `git_sha`, `run`) | 5 MB × 6 files = 30 MB |
| `<time>-manual.log` | any other run (dry run, named repos); its event line is in the same file | 10 MB × 2 each, the rest of the 200 MB; older than 14 days or over the budget are deleted at start |

Tokens (`GREENKEEP_TG_BOT_TOKEN`, the `.env` values of a tended repo) are masked in every file. Uncaught errors go through the same logger.

One repo run: `jq -c 'select(.repo=="owner-name")' ~/.local/state/greenkeep/events.jsonl` (or `select(.run=="20261105-231207")` for a whole run, `select(.outcome=="error")` for failures). In a `*-manual.log` the event is a line with `"kind": "repo_run"`: `grep '"kind": "repo_run"' FILE | sed 's/^[^{]*//' | jq .`

Manual step, needs sudo: the service's console goes to journald, which has its own limit. Set `SystemMaxUse=200M` in `/etc/systemd/journald.conf` (and `journalctl --user` shares it), then `sudo systemctl restart systemd-journald`.
