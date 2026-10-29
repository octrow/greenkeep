# Agents: start here

1. `CONTEXT.md`: the domain terms.
2. `docs/logic-map.md`: how one run turns the tended repos into merged fix PRs and a digest: every step, every branch, the module behind it.
3. `docs/adr/`: decisions; `docs/plans/`: work in progress, `2026-10-29-logic-map-fixes.md` lists the known logic gaps.

Rules:
- Change a step or a branch → update `docs/logic-map.md` and its mirror `docs/logic-map.ru.md` in the same commit.
- `tests/test_logic_map.py` fails when a `Module:` line in either map points to a module that is gone.
