# No human in the loop

The owner will not come back to review code or pick up what greenkeep gave up on (2026-11-04). A `needs-human` issue therefore waited forever: 20 of them were open on 2026-11-04, the oldest from 2026-10-19.

So a parked finding no longer asks for anyone. Its issue is closed with the one-line reason of each attempt and labelled `greenkeep-gave-up`; the finding keeps the HEAD it was parked at and gets three new attempts once a commit changes its file. The code it failed on is then gone, and a new attempt is not paying for the same failure twice. Issues left open from before are closed the same way on the next run.

Safety stays where it was: the gate and the reviewer decide. What changes is who is expected to act on a failure: nobody.

## Later steps (PLAN.md, "Эскалация вместо needs-human")

- Escalation (2026-11-05): before giving up, a parked finding gets one try by an agent, Opus 5.5 at medium effort with Edit and a short Bash allowlist in the clone, under the same gate and review. The triage, the panel of three lenses and the review by another vendor were dropped by the owner; ADR 0003 stands as it is.
- Revert instead of auto-pause (2026-11-05): an auto-pause waited for the owner to look and delete PAUSE, which nobody does. Now a reverted greenkeep merge, or one after which CI on the base branch went red (greenkeep then opens and merges a plain `git revert` PR itself), marks the finding `reverted`, never tried again, and puts that repo into cool-down. Other repos go on. PAUSE stays for the owner's own use.

## Considered Options

- **Keep `needs-human`, close after 14 days** (until now, tests only): the backlog still waited two weeks for nobody, and every other kind waited forever.
- **Never open issues for findings**: loses the public record of what was tried; the closed issue is that record.
- **Retry parked findings on a timer**: pays again for the same failure on unchanged code.

## Consequences

- `needs-human` is no longer created; old issues keep the label after they are closed.
- The digest lists parked findings under "Gave up"; "Needs you" keeps only what greenkeep cannot do (owner notes).
- `PARKED_TEST_DAYS` and its 14-day close are gone: every parked issue closes at once.
