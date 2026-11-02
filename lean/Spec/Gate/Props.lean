import Spec.Gate.Model
/-! Properties of step 19 «Gate». Statements are frozen once the user approves them; only proofs change. -/
namespace Gate

/-- The suppression markers exactly as the map writes them. -/
def mapMarkers : List Str :=
  ["noqa", "type: ignore", "pragma: no cover", "ruff:", "pylint: disable"].map String.toList


theorem occurs_append (p : List Tok) (s : Str) (h : matchPrefix p s = true) :
    ∀ pre : Str, occurs p (pre ++ s) = true
  | [] => by cases s with
    | nil => simpa [occurs] using h
    | cons x xs => simp [occurs, h]
  | _ :: pre => by simp [occurs, occurs_append p s h pre]

theorem marker_hit (m s pre : Str) (hm : pat m ∈ markers) (h : matchPrefix (pat m) s = true) :
    hasMarker (pre ++ s) = true :=
  List.any_eq_true.mpr ⟨pat m, hm, occurs_append _ _ h pre⟩

theorem max_filter_le_self {xs : List Nat} {n : Nat} (h : n ∈ xs) :
    (xs.filter (· ≤ n)).max? = some n := by
  rw [List.max?_eq_some_iff]
  simp only [List.mem_filter, decide_eq_true_eq]
  exact ⟨⟨h, Nat.le_refl n⟩, fun b hb => hb.2⟩

theorem min_filter_ge_self {xs : List Nat} {n : Nat} (h : n ∈ xs) :
    (xs.filter (· ≥ n)).min? = some n := by
  rw [List.min?_eq_some_iff]
  simp only [List.mem_filter, decide_eq_true_eq]
  exact ⟨⟨h, Nat.le_refl n⟩, fun b hb => hb.2⟩

theorem hunkViolations_eq_nil (cov : Cov) (h : Str × List Nat) :
    hunkViolations cov h = [] ↔ (isTestPath h.1 = false →
      ∃ e m, lookup cov h.1 = some (e, m) ∧ ∀ n ∈ h.2, covered e m n = true) := by
  unfold hunkViolations
  rcases hl : lookup cov h.1 with _ | ⟨e, m⟩ <;> cases ht : isTestPath h.1 <;> simp [List.filter_eq_nil_iff]
  exact ⟨fun hh => ⟨e, m, ⟨rfl, rfl⟩, hh⟩, fun ⟨_, _, ⟨h1, h2⟩, hh⟩ => h1 ▸ h2 ▸ hh⟩

-- map: "3. no added `noqa`, `type: ignore`, `pragma: no cover`, `ruff:`, `pylint: disable`;"
/-- A line is reported as a suppression exactly when the change adds it and it carries a marker. -/
theorem suppressions_iff (d t : Str) :
    t ∈ suppressions d ↔ t ∈ addedLines d ∧ hasMarker t = true := by
  simp [suppressions, List.mem_filter]

-- map: "3. no added `noqa`, `type: ignore`, `pragma: no cover`, `ruff:`, `pylint: disable`;"
/-- Each marker as the map spells it is found wherever it sits in an added line. -/
theorem map_marker_caught (m pre post : Str) (hm : m ∈ mapMarkers) :
    hasMarker (pre ++ m ++ post) = true := by
  simp only [mapMarkers, List.map_cons, List.map_nil, List.mem_cons, List.not_mem_nil, or_false] at hm
  rw [List.append_assoc]
  rcases hm with rfl | rfl | rfl | rfl | rfl
  · exact marker_hit "noqa".toList _ pre (by simp [markers]) (by simp [pat, matchPrefix]; try simp [isSpace])
  · exact marker_hit "type:~ignore".toList _ pre (by simp [markers]) (by simp [pat, matchPrefix]; try simp [isSpace])
  · exact marker_hit "pragma: no cover".toList _ pre (by simp [markers]) (by simp [pat, matchPrefix]; try simp [isSpace])
  · exact marker_hit "ruff~:".toList _ pre (by simp [markers]) (by simp [pat, matchPrefix]; try simp [isSpace])
  · exact marker_hit "pylint:~disable".toList _ pre (by simp [markers]) (by simp [pat, matchPrefix]; try simp [isSpace])

-- map: "lint → only its file, changed lines covered before" and "bug → … source lines covered"
/-- The coverage rule passes exactly when every changed old line of every non-test file was
covered by the baseline tests, and every such file was measured. -/
theorem coverage_passes_iff (d : Str) (cov : Cov) :
    violations d cov = [] ↔
      ∀ h ∈ changed d, isTestPath h.1 = false →
        ∃ e m, lookup cov h.1 = some (e, m) ∧ ∀ n ∈ h.2, covered e m n = true := by
  simp only [violations, List.flatMap_eq_nil_iff, hunkViolations_eq_nil]

-- map: "changed lines covered before"
/-- A statement the baseline tests executed counts as covered. -/
theorem executed_covered (e m : List Nat) (n : Nat) (h : n ∈ e) : covered e m n = true := by
  simp only [covered]
  rw [max_filter_le_self (List.mem_append_left m h), min_filter_ge_self (List.mem_append_left m h)]
  simp [h]

-- map: "changed lines covered before"
/-- A statement the baseline tests never executed is not covered. -/
theorem missing_not_covered (e m : List Nat) (n : Nat) (hm : n ∈ m) (he : n ∉ e) :
    covered e m n = false := by
  simp only [covered]
  rw [max_filter_le_self (List.mem_append_right e hm)]
  simp [he]

-- map: "untested → only test files"
/-- Any file under a `tests/` directory is a test file. -/
theorem under_tests_is_test (dirs : List Str) (name : Str) (h : "tests".toList ∈ dirs) :
    isTestParts (dirs ++ [name]) = true := by
  simp only [isTestParts, List.dropLast_concat, Bool.or_eq_true, List.any_eq_true]
  exact Or.inl (Or.inl ⟨_, h, by decide⟩)

end Gate
