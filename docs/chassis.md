# Chassis — Run State Machine

One state machine, mode-agnostic. The orchestrator drives it; `scripts/run_state.py`
enforces legal transitions; every phase advance requires the listed gate and writes a receipt.

## Phases

| Phase | Actor | Output | Advance gate |
|---|---|---|---|
| `init` | orchestrator | `state.json`, `CURRENT`, base commit, epic id | `preflight.py` PASS |
| `intake` | orchestrator (`intake.py`) | `req_index.json` incl. INF-* slots | every configured root indexed; empty index in `documented` mode fails loudly |
| `partition` | orchestrator (`partition.py`) | auditor packets per (mode, partition) | zero duplicate ownership |
| `audit` | swarm-auditor × N (one mode wave at a time) | finding payloads → `findings/{mode}/` | 100% assigned rows classified; `ambiguous` → human queue |
| `dedupe` | orchestrator (`fingerprint.py`) | `clusters/` | cross-mode dedupe + evidence-ladder filter |
| `decompose` | swarm-architect proposes; `atomicity.py` authorizes | manifests, ordered task list | guards pass; dependency cycles are a hard block |
| `record` | orchestrator (`tracker.py`, journaled) | tracker issues, registries, `state.json.tasks` | every non-speculative cluster has issue or no-op receipt |
| `wave` | orchestrator (`conflict_graph.py`) | wave batches | topo sort on depends_on → path-conflict coloring → severity ordering |
| `course` | swarm-owner per task (or flat stages when `orchestration.tier=flat`) | course report + worktree diff | see `owner-course.md` |
| `verify` | orchestrator (`course_verify.py` + re-run `gates.py`) | verification receipt | deterministic checks pass; report claims never trusted raw |
| `integrate` | orchestrator (journaled) | merge commit, `gates --integrate` receipt | gates PASS; conflicts → human question |
| `cleanup` | orchestrator | worktree removed, task closed | close checklist (run-layout.md) |
| `close` | orchestrator | final report, leftovers, human-queue summary | all tasks terminal |

Any phase may transition to `blocked`. Illegal: `verify → course` for the same plan hash
(no silent redo); `dedupe → record` skipping decompose; anything after `delta` back to implement.

## Per-task micro-states

`pending → coursing → collected → admitted → remediated → gated → delta_passed → integrated → closed`
with side exits `blocked` and `replan_required`. Each transition stamps receipt path, plan hash,
addendum hash, worktree id, owner agent id into `state.json.tasks[id]`.

## Receipt skip rule

Skip a stage only when an existing receipt matches current commit + plan hash
(+ addendum hash post-admission). Missing receipt is never PASS. A new integration SHA
invalidates commit-scoped receipts.

## Budgets

- Run: `budgets.run` (agents, est. tokens, cost, wall hours). Exhaustion → graceful stop, resumable.
- Per task: owner attempts, evidence refreshes, architect consults, replans — every recovery
  transition decrements; zero → `blocked` + human queue. Counters live in `budgets.json`.

## Resume

`CURRENT` + `checkpoints/` + `journal.jsonl`. On resume: reload state, run `journal.py reconcile`
(complete or roll back half-done external effects), reconcile `agents.jsonl` (uncorrelated spawns
reclaimed), continue from the last checkpoint.
