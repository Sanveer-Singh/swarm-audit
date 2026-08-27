# Evidence Ladder

Every finding, red-team verdict, and course-report claim carries an evidence level.
Deterministic consequences follow from the level — models never argue around the ladder.

| Level | Meaning | Consequence |
|---|---|---|
| `observed` | Reproducible command, failing test, trace, exploit, or browser interaction demonstrating the issue | Eligible to become a fix task; can block merge |
| `static-proof` | Precise control-flow/data-flow argument tied to cited code locations that still exist at the audited commit | Eligible to become a fix task; can block merge |
| `plausible` | Technically possible, not yet demonstrated | Becomes a validation task only — never a fix task directly |
| `speculative` | Requires assumptions not established by the repository | Dropped. Convert to a question if genuinely important |

## Rules

- Red-team agents must return zero findings when they cannot produce evidence at
  `plausible` or better. Silence is an acceptable, expected outcome.
- A finding's cited files and lines must exist at the audited commit; `course_verify.py`
  and the orchestrator check existence deterministically.
- Line numbers and prose are evidence, not identity — identity is the fingerprint
  (requirement id + gap class + path + symbol + remediation class).
- Clean test output is evidence about the tested behavior only, never proof that a
  reviewer's concern is false.

## Per-mode minimum evidence for `observed` / `static-proof`

| Mode | Minimum |
|---|---|
| faithfulness | Failing journey walk, failing test, or requirement text + code path showing divergence |
| ui-ux | Browser observation (snapshot/screenshot) or computed-style/contrast measurement |
| code-quality | Cited code + rule (linter/Sonar rule id or named principle) + concrete consequence |
| architecture | Dependency path (graphify or import chain) crossing a declared boundary, or duplicated pattern instances cited side by side |
| security | Reproducible exploit path, failing security test, or control-flow proof reaching the vulnerable behavior with realistic preconditions |
