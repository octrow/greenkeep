# Pinned Claude models on Claude Code, and no triage step

This replaces the model and triage parts of ADR 0001; its core stands: deterministic tools find, a model writes one change for one finding, and the gate decides.

ADR 0001 planned free-tier flash models through OmniRoute as patchers and a Jev (or LLM) triage of every finding. Neither survived. The 2026-10-15 benchmark put Opus = Sonnet > grok-4.7 as patcher, and the flash models were behind all three. So every role now runs on Claude Code (`claude -p`) with a pinned model: Sonnet (`claude-sonnet-5-5`) patches and hunts; Opus (`claude-opus-5-5`) reviews, doubts hunted bugs and, during a boost, plans hard changes. Each role reads the clone with read-only tools (`Read,Grep,Glob`) and none of the owner's Claude Code settings. greenkeep applies the reply itself.

Calls go to `claude -p` directly, not through OmniRoute. Through OmniRoute a bare Claude id went to a free combo that answered under the same label, and a pinned `claude/...` id was paid by whichever account OmniRoute held. Direct, the account Claude Code is logged in to pays.

There is no triage step. What triage was meant to judge is decided by code that can be read and tested. Whether a finding is real comes from the tool that reported it. Urgency is the kind's work order (security first). Whether it is safe to fix automatically comes from the candidate filter (`attemptable`, vendored code, coverage), the gate and the reviewer.

## Considered Options

- **Free-tier flash models through OmniRoute** (ADR 0001): free, but weaker on the benchmark, a combo answers with a different model from run to run, and the model behind a label could be swapped silently.
- **Jev triage before work**: one more model call per finding, deciding what the filter and gate already decide deterministically, and a result that cannot be reproduced from the logs.

## Consequences

- Runs cost subscription usage, not money; a usage limit or a logout stops the run without counting an attempt.
- The reviewer is the one model judgment in the loop. It catches what the gate cannot see (tautological asserts, broken behaviour that is still covered), so a change no model wrote is not reviewed.
- Changing a model is a benchmark and a change to `core.py`, not a routing setting.
