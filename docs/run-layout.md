# Run Folder Anatomy

`{paths.run_root}/{run-id}/` — run id convention: `swarm-YYYY-MM-DD[-n]`.
`{paths.run_root}/CURRENT` holds the active run id.

```
state.json              # authoritative machine state (run_state.py owns writes)
state.md                # human render of state.json
registry.json           # reduced agent snapshot from agents.jsonl
agents.jsonl            # append-only spawn/stop event log (hook-written + orchestrator-written)
req_index.json          # intake output: all requirement ids + text + source + INF-* inferred rows
findings/{mode}/*.json  # persisted auditor payloads
clusters/*.json         # reconciled, deduped, evidence-filtered clusters
decomposition/queue.json# decomposition queue, budgets, ancestor chains, attempts
packets/*.json          # every spawn input (schema-valid)
payloads/*.json         # every agent output persisted before promotion
course-reports/*.json   # owner course reports
ledger/*.json           # normalized post-implement findings per task
gate-receipts/*.json    # commit+hash-scoped pass/fail evidence
journal.jsonl           # durable activity journal (external side effects, idempotency keys)
budgets.json            # run + per-task budget counters
research/receipts.md    # context7/perplexity/web research receipts
decisions.md            # per-run decision log (see decisions-template.md)
human-queue.md          # questions requiring human answers
leftovers.md            # deferred work
checkpoints/            # per-phase state snapshots
```

Durable cross-run artifacts (outside the run folder):

```
{paths.plans_root}/{task}.md              # frozen plan (immutable once hashed)
{paths.plans_root}/{task}.addendum.json   # hashed remediation envelope
{paths.registries}/issue-registry.md      # tracker id ↔ cluster ↔ plan hash rows
{paths.registries}/decision-log.md        # cross-run decisions
```

## Artifact ownership

| Artifact | Writer | Consumer |
|---|---|---|
| packet | orchestrator (`packet_build.py`) | spawned agent |
| payload | agent returns; orchestrator persists | orchestrator, downstream packets |
| course report | owner returns; orchestrator persists | `course_verify.py` |
| gate receipt | orchestrator | phase gates, close checklist |
| ledger row | orchestrator (from collect payloads) | admission red-team |
| journal entry | orchestrator (`journal.py`) | resume reconcile |
| plan / addendum | orchestrator (from planner/admission payloads) | implementer, red-teams, close |

## Close checklist (per task)

1. Frozen plan + red-team PASS receipt (hash-matched)
2. Course report verified (`course_verify.py` receipt)
3. Gates receipt — orchestrator-run, targeted + full
4. UI disposition receipt (APPLICABLE with findings resolved, NOT_APPLICABLE, or human-accepted BLOCKED_INFRASTRUCTURE)
5. Tracker issue closed with plan hash + commit (journaled)
6. Registries updated; trace file updated when configured
7. Worktree removed (journaled)
