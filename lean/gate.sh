#!/usr/bin/env bash
# Done means this passes. Same script locally, in the Stop hook and in CI.
set -euo pipefail
cd "$(dirname "$0")"
TEST_CMD=(uv run pytest -q)  # from the repo root: the suite CI runs (.github/workflows/test.yml), oracle tests included

lake build
# Cheats the kernel cannot see: compiled code that differs from the definition, skipped checks.
# lake comparator does not see implemented_by/extern (verified), so this grep is load-bearing.
# Raw text on purpose: a word in a comment fails loudly, rewording it is cheaper than a comment stripper that could hide a cheat.
if grep -rnE '\bsorry\b|native_decide|\baxiom\b|implemented_by|\bextern\b|\bunsafe\b|skipKernelTC|debug\.' \
     --include='*.lean' --exclude-dir=.lake . | grep -vE '^(\./)?Frozen\.lean:'; then
  echo "gate: forbidden construct" >&2; exit 1
fi
# lake comparator checks only the theorems comparator.json names: an unlisted frozen theorem is not checked,
# and a listed one missing from Frozen.lean is not frozen. It also checks only the definitions those statements
# reach, so every frozen definition the oracle calls must be reachable from a listed statement.
frozen_checks() {
python3 - <<'PY'
import json, re
from pathlib import Path
DECL = re.compile(r"(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|partial)\s+)*"
                  r"(theorem|lemma|def|abbrev|structure|inductive|instance|opaque)\s+([^\s:({\[]+)")
names = set(json.load(open("comparator.json"))["theorem_names"])
scope, frozen, decls, cur = [], set(), {}, None
for line in open("Frozen.lean", encoding="utf-8"):
    if m := re.match(r"(namespace|section)\b\s*(\S*)", line):
        scope.append(m.group(2) if m.group(1) == "namespace" else "")  # a section name is not a prefix
    elif re.match(r"end\b", line) and scope:
        scope.pop()
    elif m := DECL.match(line):
        kind, name = m.groups()
        if kind in ("theorem", "lemma"):
            frozen.add(".".join([*filter(None, scope), name]))
        cur = decls.setdefault(name.split(".")[-1], [kind, ""])
    if cur is not None:
        cur[1] += line
if frozen != names:
    raise SystemExit(f"gate: comparator.json and Frozen.lean disagree; only in Frozen: {sorted(frozen - names)}, "
                     f"only in comparator.json: {sorted(names - frozen)}")
words = lambda text: set(re.findall(r"[A-Za-z_][\w']*", text))
todo = set().union(*(words(body.split(":=")[0]) for kind, body in decls.values() if kind in ("theorem", "lemma")))
reached = set()
while todo:  # definitions the listed statements reach, by name: an over-approximation, never misses a real use
    name = todo.pop()
    if name in decls and name not in reached:
        reached.add(name)
        todo |= words(decls[name][1])
oracle = Path("Oracle.lean")
called = {n for n in words(oracle.read_text(encoding="utf-8")) if n in decls} if oracle.exists() else set()
if called - reached:
    raise SystemExit(f"gate: the oracle calls frozen definitions no listed theorem reaches, so lake comparator "
                     f"does not check them: {sorted(called - reached)}")
PY
}
frozen_checks
lake comparator   # statements and model definitions match Frozen.lean, standard axioms, kernel accepts
(cd .. && "${TEST_CMD[@]}")
echo "gate: ok"
