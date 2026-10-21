# The bug hunt's referee is a failing test, not a third model

The bug hunt (ADR 0001) was planned as Hunter → Skeptic → Referee. The Hunter (Sonnet) suspects up to three bugs in one module and the Skeptic (Opus) keeps only the claims it cannot refute. There is no Referee model: every surviving bug is fixed as a test-first fix, and the gate rejects the change unless its new test fails on the unchanged code and the full suite passes with the fix. A third opinion from a model would only be more drift. A red-then-green test is deterministic, can be reproduced from the logs, and stays in the repo as the proof.

## Consequences

- A real bug that no short test can show gets parked after three attempts instead of merged.
- Only modules at 50%+ coverage are hunted, so the rule that every changed line was covered before the change holds for the fix as well. Hunting uncovered code needs the test-first fix's own test to stand in for that coverage, and that comes later.
- The reviewer still reads the change before it lands.
