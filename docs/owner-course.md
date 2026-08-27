# Owner Course Contract

The owner is a readonly depth-1 subagent. It gathers task context once and runs the whole
course in one bounded invocation (redo-on-crash). It never edits files, never touches the
tracker, never merges. It spawns stage agents at depth 2; stage agents must not use the
Task tool (platform nesting cap).

Stage-spawn identity: each stage spawn's task text carries a unique
`packet_path: <owner-packet-path>#<stage>-<attempt>`. Hooks validate the file part before
`#` and treat the full fragmented string as the admission/registry key, so every stage
spawn occupies (and releases) its own slot.

When `orchestration.tier = flat`, the orchestrator runs these same stages itself at depth 1;
the contract below still applies per stage.

## Inputs (owner packet)

`run_id, task_id, goal, non_goals, finding_cluster (full evidence), requirement_ids + embedded
text, base_commit, worktree {path, env_file, port, db}, allowed_paths, forbidden_paths,
budgets, mode_pack refs, capability_manifest, output_kind: course_report, packet_path`.

## Stages

| # | Stage | Actor | Budget |
|---|---|---|---|
| 1 | Validate finding | owner (readonly reads; graphify first when enabled) | 1 pass; `not_reproducible` aborts course |
| 2 | Diagnose | owner | 1 pass; root cause with file+symbol citations |
| 3 | Plan minimal fix | swarm-planner (fresh) | 1 + revisions from stage 4 |
| 4 | Red-team plan | swarm-red-team (fresh each round, mode=plan; never given planner narrative) | max `plan_revise_rounds` REVISEs; NEEDS_EVIDENCE consumes no round but decrements task evidence budget |
| 5 | Fix plan | swarm-planner | within stage-4 budget |
| 6 | Implement | swarm-implementer (writable, worktree only, plan-hash locked) | 1 invocation |
| 7 | Red-team implementation | swarm-red-team (fresh, mode=implementation) then mode=admission | 1 collect + 1 admission; at most `remediation_batches` implementer batch against admitted rows |
| 8 | Red-team UI | swarm-ui-red-team (fresh, browser) | 1 pass; findings join the single remediation batch; mandatory disposition even for backend tasks |
| 9 | Assemble course report | owner | — |

## Loop rules (owner enforces, orchestrator re-verifies)

- Same fingerprint in 2 consecutive red-team rounds → abort `loop_tripped`.
- Red-team FAIL twice on same plan → abort `blocked`.
- After the remediation batch: one delta red-team pass only. Delta FAIL → abort `blocked`.
  Never re-implement after delta.

## Owner duties beyond sequencing

- **Hallucination validation**: before advancing a stage, check the payload's cited files
  exist and its verdict is schema-legal. Reject and respawn fresh once; second failure aborts.
- **Dead stage agents**: one resume by id, one fresh respawn, then abort with partial report.
- **Capability checks**: confirm required tools/skills/MCPs from `capability_manifest`
  before each stage; missing → abort `missing_capability`.
- **Context packs**: each stage agent receives only durable, inspectable inputs (plan, diff,
  evidence, requirement text) — never another agent's persuasive narrative or approval labels.
- Owners do not implement, ever.

## Course report (returned payload, kind `course_report`)

`task_id, plan_sha256, addendum_sha256?, stage_receipts[] {stage, agent_id, verdict,
payload_ref, started, ended}, red_team_rounds {plan, impl, delta}, fingerprints_raised[],
fingerprints_resolved[], diff_summary {files[], insertions, deletions}, tests {added[],
commands_run[], results}, ui_disposition, blockers[], residual_risks[],
status: complete | blocked | not_reproducible | loop_tripped | missing_capability`.

## Orchestrator verification (deterministic, `course_verify.py`)

Schema-valid; rounds within budgets; cited files exist at worktree HEAD; diff paths within
allowed_paths envelope; every raised fingerprint resolved, admitted-deferred, or residual;
claimed tests present in diff. Then the orchestrator re-runs `gates.py` against the worktree
itself — report claims about test results are never trusted raw. Verification failure →
task `blocked`, never silent accept.
