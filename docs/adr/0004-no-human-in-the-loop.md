# No human in the loop

The owner will not come back to review code or pick up what greenkeep gave up on (2026-11-04). A `needs-human` issue therefore waited forever: 20 of them were open on 2026-11-04, the oldest from 2026-10-19.

So a parked finding no longer asks for anyone. Its issue is closed with the one-line reason of each attempt and labelled `greenkeep-gave-up`; the finding keeps the HEAD it was parked at and gets three new attempts once a commit changes its file. The code it failed on is then gone, and a new attempt is not paying for the same failure twice. Issues left open from before are closed the same way on the next run.

Safety stays where it was: the gate and the reviewer decide, and an owner's revert pauses greenkeep (Auto-pause). What changes is only who is expected to act on a failure: nobody.

## Next (PLAN.md, "Эскалация вместо needs-human")

Before giving up, a finding will get a stronger path: a triage of its failure history, an agent (Opus with Edit and Bash in the clone), and a second review by a panel prompt on another vendor's model through OmniRoute with a pinned provider id. Two points of ADR 0003 change with that step, and its own ADR records them:
- ADR 0003 has no triage step because one before work would decide what the filter and gate already decide. This triage runs only after three failed attempts, on their reasons.
- ADR 0003 calls Claude directly, not through OmniRoute, because a combo swaps models and its account is unknown. The panel runs through OmniRoute only with a pinned provider id, never a combo; the Claude roles stay direct.

## Considered Options

- **Keep `needs-human`, close after 14 days** (until now, tests only): the backlog still waited two weeks for nobody, and every other kind waited forever.
- **Never open issues for findings**: loses the public record of what was tried; the closed issue is that record.
- **Retry parked findings on a timer**: pays again for the same failure on unchanged code.

## Consequences

- `needs-human` is no longer created; old issues keep the label after they are closed.
- The digest lists parked findings under "Gave up"; "Needs you" keeps only what greenkeep cannot do (owner notes).
- `PARKED_TEST_DAYS` and its 14-day close are gone: every parked issue closes at once.
