/-! Executable model of the pure rules of logic-map step 19 «Gate» (`greenkeep/gate.py`):
which old lines a `git diff -U0` changes, whether the baseline tests ran them, what a test
file is, and which added lines silence a tool. Written from the map and git's diff format. -/
namespace Gate

/-- Text as a list of characters. -/
abbrev Str := List Char

/-- Split on one separator; the empty text is one empty piece. -/
def splitOn (sep : Char) : Str → List Str
  | [] => [[]]
  | c :: cs =>
    match splitOn sep cs with
    | [] => [[]]
    | l :: ls => if c = sep then [] :: l :: ls else (c :: l) :: ls

/-- git ends every diff line with `\n` and nothing else: a form feed or another control
character inside a source line stays inside that line. -/
def diffLines (d : Str) : List Str := splitOn '\n' d

/-- `s` with `pre` cut off its front, if it starts with `pre`. -/
def dropPrefix? : Str → Str → Option Str
  | [], s => some s
  | _ :: _, [] => none
  | p :: ps, c :: cs => if p = c then dropPrefix? ps cs else none

/-! ## Test files -/

-- silent (see the docstring below)
/-- Silent in the map: the map never says what a test file is; this is `_test_dir` in the code. -/
def isTestDir (n : Str) : Bool :=
  n == "tests".toList || n == "test".toList || "tests_".toList.isPrefixOf n
    || "tests-".toList.isPrefixOf n || "_tests".toList.isSuffixOf n || "-tests".toList.isSuffixOf n

/-- A path as directory parts plus a file name: some directory is a test directory, or the
name is `test_*` or `conftest.py`. -/
def isTestParts (parts : List Str) : Bool :=
  parts.dropLast.any isTestDir || "test_".toList.isPrefixOf (parts.getLastD [])
    || parts.getLastD [] == "conftest.py".toList

def isTestPath (p : Str) : Bool := isTestParts ((splitOn '/' p).filter (· ≠ []))

/-! ## Changed old lines -/

/-- Digits at the front of `s` as a number, and the rest. -/
def digits : Str → Nat → Option Nat → Option Nat × Str
  | [], _, r => (r, [])
  | c :: cs, acc, r =>
    if c.isDigit then
      let v := acc * 10 + (c.toNat - 48)
      digits cs v (some v)
    else (r, c :: cs)

/-- The old side of a hunk header `@@ -start[,count] +…`: start and count (1 when absent). -/
def hunkOld (l : Str) : Option (Nat × Nat) := do
  let rest ← dropPrefix? "@@ -".toList l
  let (start?, rest) := digits rest 0 none
  let start ← start?
  match rest with
  | ',' :: more => let (count?, _) := digits more 0 none; let count ← count?; some (start, count)
  | _ => some (start, 1)

-- silent (see the docstring below)
/-- Silent in the map: the map says "changed lines"; for a pure insertion (`-N,0`) that is line N, the
line the new code lands after (line 1 at the top of the file), as the code's docstring says. -/
def hunkLines (start count : Nat) : List Nat :=
  if count = 0 then [max start 1] else List.range' start count

/-- A header path: `a/P` or `b/P` gives P without the tab git appends when P has a space;
`/dev/null` gives none. -/
def headerPath (side : Char) (rest : Str) : Option Str :=
  match dropPrefix? [side, '/'] rest with
  | some p => some (if p.getLast? = some '\t' then p.dropLast else p)
  | none => none

/-- Parser state: inside a file header or a hunk, the old and new paths of the current file,
the changed old lines per hunk (path, lines) and the added lines, both newest first. -/
structure St where
  inHunk : Bool := false
  old : Option Str := none
  new : Option Str := none
  hunks : List (Str × List Nat) := []
  added : List Str := []

/-- One diff line. A hunk belongs to the file being changed: its new path, or for a deleted
file its old path. `+++`/`---` are headers only before a file's first hunk. -/
def step (s : St) (l : Str) : St :=
  if (dropPrefix? "diff --git ".toList l).isSome then { s with inHunk := false, old := none, new := none }
  else match hunkOld l with
  | some (start, count) =>
    match s.new.or s.old with
    | some p => { s with inHunk := true, hunks := (p, hunkLines start count) :: s.hunks }
    | none => { s with inHunk := true }
  | none =>
    if s.inHunk then
      match l with
      | '+' :: t => { s with added := t :: s.added }
      | _ => s
    else match dropPrefix? "--- ".toList l, dropPrefix? "+++ ".toList l with
      | some r, _ => { s with old := headerPath 'a' r }
      | _, some r => { s with new := headerPath 'b' r }
      | _, _ => s

def parse (d : Str) : St := (diffLines d).foldl step {}

/-- Changed old lines, one entry per hunk: (path, lines). -/
def changed (d : Str) : List (Str × List Nat) := (parse d).hunks.reverse

/-- Added lines (without their `+`), in diff order. -/
def addedLines (d : Str) : List Str := (parse d).added.reverse

/-! ## Coverage rule -/

/-- Baseline coverage: (path, executed lines, missing lines) per measured file. -/
abbrev Cov := List (Str × List Nat × List Nat)

def lookup (cov : Cov) (p : Str) : Option (List Nat × List Nat) :=
  (cov.find? (·.1 == p)).map (·.2)

-- silent (see the docstring below)
/-- Silent in the map: the map says "covered before"; coverage lists only a statement's first line, so a
line counts when the statements at or before it and at or after it (the ones that exist, at least
one) were executed. -/
def covered (exec missing : List Nat) (n : Nat) : Bool :=
  let stmts := exec ++ missing
  let before := (stmts.filter (· ≤ n)).max?
  let after := (stmts.filter (· ≥ n)).min?
  (before.isSome || after.isSome) && before.all (· ∈ exec) && after.all (· ∈ exec)

/-- A violation: (path, some line) for an uncovered line, (path, none) for an unmeasured file. -/
def hunkViolations (cov : Cov) (h : Str × List Nat) : List (Str × Option Nat) :=
  -- silent: the map says nothing of test files here; the code exempts them ("checked by the suite")
  if isTestPath h.1 then []
  else match lookup cov h.1 with
    -- silent: an unmeasured file fails as a whole
    | none => [(h.1, none)]
    | some (e, m) => (h.2.filter (fun n => !covered e m n)).map (fun n => (h.1, some n))

def violations (d : Str) (cov : Cov) : List (Str × Option Nat) :=
  (changed d).flatMap (hunkViolations cov)

/-! ## Suppressions -/

/-- A marker pattern: a literal character, or any run of whitespace (`\s*`). -/
inductive Tok
  | lit (c : Char)
  | spaces
deriving DecidableEq

/-- `~` in a marker spelling stands for `\s*`. -/
def pat : Str → List Tok
  | [] => []
  | '~' :: r => .spaces :: pat r
  | c :: r => .lit c :: pat r

/-- Python's `\s` on ASCII and the C0 separators: tab to carriage return, 0x1C to space. -/
def isSpace (c : Char) : Bool := (9 ≤ c.toNat && c.toNat ≤ 13) || (28 ≤ c.toNat && c.toNat ≤ 32)

/-- Does `p` match at the front of `s`? Letters compare case-insensitively (ASCII). -/
def matchPrefix : List Tok → Str → Bool
  | [], _ => true
  | .lit c :: ps, x :: xs => x.toLower == c && matchPrefix ps xs
  | .lit _ :: _, [] => false
  | .spaces :: ps, [] => matchPrefix ps []
  | .spaces :: ps, x :: xs => matchPrefix ps (x :: xs) || (isSpace x && matchPrefix (.spaces :: ps) xs)

def occurs (p : List Tok) : Str → Bool
  | [] => matchPrefix p []
  | x :: xs => matchPrefix p (x :: xs) || occurs p xs

-- silent (see the docstring below)
/-- Silent in the map: the map lists the markers; case and the spaces around `:` come from the code. -/
def markers : List (List Tok) :=
  ["noqa", "ruff~:", "type:~ignore", "pylint:~disable", "pragma: no cover"].map (pat ·.toList)

def hasMarker (t : Str) : Bool := markers.any (occurs · t)

/-- Added lines that silence a tool. -/
def suppressions (d : Str) : List Str := (addedLines d).filter hasMarker

end Gate
