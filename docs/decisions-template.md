# Per-Run Decision Log Template

Copy into `{run}/decisions.md`. One row per decision. Append-only — corrections are new rows
referencing the old id.

```
## DEC-{run}-{n}

- date:
- phase:
- task_ids:
- class: best_guess | architect_recommendation | human_answer | orchestrator_call
- question:
- options_considered:
- decision:
- reason:
- requirement_ids_touched:
- changes_requirement_meaning: false   # must be false for best_guess/architect classes
- evidence_refs:
- research_receipts:                   # mcp, question, source, retrieved_at
- budget_decremented: architect_consults | none
```

Rules:

- `best_guess` rows must cite the enumerated class from `unblocking.md` they fall under.
- `human_answer` rows link the human-queue row they resolve.
- Any row with `changes_requirement_meaning: true` is a contract violation — contract tests
  scan for it.
