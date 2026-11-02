import Spec.Gate.Model
/-! Oracle for differential tests. Long-lived: one request per line; text travels as character codes.
In:  `S c…` (a diff)                       Out: suppressions, each as `len c…`
     `C n c… k (len c… ne e… nm m…)…`      Out: violations, each as `len c… 0 0` (not measured) or `len c… 1 line`
     (a diff of n codes, then k measured files: path, executed lines, missing lines)
     `T c…` (a path)                       Out: `1` for a test path, else `0`
Or `error: <reason>`. -/
open Gate

def chars (xs : List Nat) : Str := xs.map Char.ofNat

def codes (s : Str) : List Nat := s.map Char.toNat

def encodeStr (s : Str) : List Nat := s.length :: codes s

/-- `n` numbers off the front, and the rest. -/
def takeN (n : Nat) (xs : List Nat) : Option (List Nat × List Nat) :=
  if xs.length < n then none else some (xs.take n, xs.drop n)

def parseCov : Nat → List Nat → Option Cov
  | 0, [] => some []
  | 0, _ :: _ => none
  | k + 1, xs => do
    let (plen, xs) ← xs.head?.map (·, xs.tail)
    let (p, xs) ← takeN plen xs
    let (ne, xs) ← xs.head?.map (·, xs.tail)
    let (e, xs) ← takeN ne xs
    let (nm, xs) ← xs.head?.map (·, xs.tail)
    let (m, xs) ← takeN nm xs
    let rest ← parseCov k xs
    some ((chars p, e, m) :: rest)

def coverageAnswer (xs : List Nat) : Option (List Nat) := do
  let (n, xs) ← xs.head?.map (·, xs.tail)
  let (d, xs) ← takeN n xs
  let (k, xs) ← xs.head?.map (·, xs.tail)
  let cov ← parseCov k xs
  some <| (violations (chars d) cov).flatMap fun (p, l) =>
    encodeStr p ++ (match l with | none => [0, 0] | some n => [1, n])

def answer (line : String) : String :=
  let words := ((line.replace "\n" "").splitOn " ").filter (· ≠ "")
  let render (ns : List Nat) := " ".intercalate (ns.map toString)
  match words with
  | tag :: rest =>
    match rest.mapM String.toNat? with
    | none => "error: expected natural numbers after the tag"
    | some xs =>
      if tag == "S" then render ((suppressions (chars xs)).flatMap encodeStr)
      else if tag == "T" then (if isTestPath (chars xs) then "1" else "0")
      else if tag == "C" then
        match coverageAnswer xs with
        | some ns => render ns
        | none => "error: malformed coverage request"
      else "error: unknown tag"
  | [] => "error: empty request"

partial def loop (stdin : IO.FS.Stream) (stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then return
  stdout.putStrLn (answer line)
  stdout.flush
  loop stdin stdout

def main : IO Unit := do
  loop (← IO.getStdin) (← IO.getStdout)
