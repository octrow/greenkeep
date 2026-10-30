# Agents: start here

1. `PLAN.md`: every plan and open item, in ONE file; a new plan is a new section there, never a new file.
2. `CONTEXT.md`: the domain terms.
3. `docs/logic-map.md`: how one run turns the tended repos into merged fix PRs and a digest: every step, every branch, the module behind it.
4. `docs/adr/`: decisions.

Rules:
- Change a step or a branch → update `docs/logic-map.md` and its mirror `docs/logic-map.ru.md` in the same commit.
- `tests/test_logic_map.py` fails when a `Module:` line in either map points to a module that is gone.
