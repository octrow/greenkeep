# The Claude Code login pays only in its hours

This narrows the routing part of ADR 0003. Claude calls stay `claude -p` with pinned models and read-only tools; what changes is which account pays.

The owner's Team seat may be used only right before its weekly limits reset: Thursday 21:00 to Friday 03:00, Almaty time (owner, 2026-11-05). The rest of the week greenkeep runs on the owner's personal Pro, which OmniRoute holds as its `claude` connection. So the config takes `login_hours = "Thu 21:00 - Fri 03:00"`: inside those hours a call is direct and the Claude Code login pays; outside them it goes through `omniroute run claude` with the model as `claude/<model>`. The `claude/` prefix pins OmniRoute's `claude` connection; a bare `claude-sonnet-*` there went to a free combo (ADR 0003). Without `login_hours` every call is direct, as before, so other users need no OmniRoute.

## Considered Options

- **Two Claude Code logins switched by clauth on the server**: the token lives in the service environment, and switching it twice a week by hand is the kind of step nobody does.
- **Hard-coding the owner's window**: greenkeep is public; everyone else would be routed through an OmniRoute they do not have.

## Consequences

- Pro limits are small: a run that hits one stops uncounted and the next hourly run tries again (as with any usage limit).
- `login_hours` are local time of the service (the unit sets `TZ`), whole hours within one week; a window across Sunday midnight is not supported.
- Escalation (PLAN.md) runs Opus 5.5 at medium effort on whichever account the hour gives; there is no second review by another vendor (owner, 2026-11-05).
