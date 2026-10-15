# Deterministic tools find, the LLM only patches

Findings come from deterministic tools (linters, type checkers, security and dependency scanners, coverage, failing tests, readiness checks); Jev triages them, and the LLM is given one finding plus its code context and returns one diff. We rejected a free-roaming coding agent (Claude Code / aider / OpenHands style) because the patching models are free-tier flash models reached through OmniRoute, and the owner's hard requirement is quality, stability and predictability: the same finding must lead to the same kind of change, every run must be reproducible from logs, and a fix PR must touch one thing.

## Considered Options

- **Free agent with tools** — finds more, but output drifts between runs and on weak models wanders or rewrites whole files.
- **Base on patched-codes/patchwork** — its AutoFix/DependencyUpgrade flows are close, but it does not own scheduling, budget, the coverage gate or the issue → PR → merge loop; we borrow ideas, not the framework.

## Consequences

LLM-driven bug discovery is not excluded: it arrives later as the separate bug hunt mode (Hunter → Skeptic → Referee), and its findings must pass the same gate.
