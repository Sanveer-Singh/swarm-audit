# Unblocking Policy

When a task blocks, the orchestrator attempts unblocking in this strict order. Every step
is journaled and decrements the task's budget counters.

## 1. Solution-architect consult (max `architect_consults` per task)

Spawn `swarm-solution-architect` (readonly, fresh) with the blocker, the requirement text,
and the evidence. It returns 2–3 options scored on SOLID, DRY, security, minimality, blast
radius, and fidelity to requirement intent, plus a recommendation and research receipts
(context7 for framework syntax, perplexity for time-sensitive tooling only).

## 2. Best-guess — enumerated classes ONLY

The orchestrator may auto-resolve without a human ONLY when the question falls entirely
inside this list:

- Which existing service/helper/module to extend (among functionally equivalent homes)
- Test level placement (unit vs integration) and test file location
- Naming of new symbols/files

Anything that alters an acceptance criterion's meaning, scope, security posture, auth,
data model, or privacy/compliance handling is excluded by construction. The resolution is
recorded in `decisions.md` with the option set considered and the reason.

## 3. Human queue

Everything else goes to `human-queue.md` as a structured row: question, blocking task ids,
options considered, evidence refs, and what changes depending on the answer. The task moves
to `blocked`; the run continues with other tasks.

## Always-human classes (never best-guessed, never architect-resolved)

- Requirement meaning, contradiction between requirements, invented-requirement suspicion
- Security posture, authentication, authorization semantics
- Data model and migration semantics
- Privacy/compliance (retention, consent, erasure)
- Anything where two requirements genuinely conflict — the architect must return
  NEEDS_EVIDENCE with needs_human, not paper over it
