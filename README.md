# Swarm Audit — Generic Audit and Course-Correct Toolset

Portable multi-agent audit pipeline for Cursor projects. Five audit modes (faithfulness,
ui-ux, code-quality, architecture, security) run on one chassis: orchestrator fans out
fresh-context auditors, an architect decomposes findings into ordered minimal-overlap tasks,
readonly task owners run a bounded validate → diagnose → plan → red-team → implement →
red-team course in isolated git worktrees, and the orchestrator verifies, integrates,
and records everything.

Generalized from the PGMS KB-audit pipeline. Design spec and decision log:
`.cursor/plans/generic_audit_swarm_toolset_2e562b38.plan.md` (r2, adversarially reviewed).

## Core invariants

- Orchestrator (main agent) is the sole durable writer and sole tracker mutator.
- Subagents are readonly payload producers, except the implementer (writable, worktree-confined).
- One-way remediation gate: collect → admit → at most one remediation batch → gates → one delta review. Never delta → implement.
- Deterministic code authorizes (atomicity, loop trips, admission, gates); models only propose.
- Evidence ladder: only `observed` / `static-proof` findings become fix tasks.
- Every recovery path decrements a budget. Budget exhausted → blocked + human queue.
- Every external side effect is journaled with an idempotency key (crash-safe resume).

## Install

```bash
python swarm-audit/install.py --target /path/to/repo
```

The installer copies agent templates into `.cursor/agents/`, skills into `.agents/skills/`,
hook scripts into `.cursor/hooks/swarm/` (wiring three-way merged into `.cursor/hooks.json`), and the toolkit
(scripts, schemas, modes, docs) into the target. It writes `swarm.config.json` from the
example if absent, records everything in a versioned `install-manifest.json`, then runs
`contract_tests.py` and `preflight.py --install-check`. Any failure rolls back.

Uninstall: `python swarm-audit/uninstall.py --target /path/to/repo` (removes only files
whose hash still matches the manifest; refuses while a run is live).

## Run

1. Fill `swarm.config.json` (see `swarm.config.example.json` and the schema).
2. In Cursor, load the orchestrator skill: `@swarm-orchestrator`.
3. Say: `run full audit` (or `run audit modes=security,faithfulness`).
4. Resume an interrupted run: `resume audit run` — state is checkpointed and journaled.

Run artifacts land in `{paths.run_root}/{run-id}/`; frozen plans in `{paths.plans_root}`.
See `docs/run-layout.md`.

## Layout

| Path | Contents |
|---|---|
| `scripts/` | Deterministic machinery (python, stdlib only) |
| `schemas/required-fields.json` | Artifact registry: kind, version, producer, consumer, schema |
| `modes/` | Five audit mode packs (rubric, taxonomy, auditor prompt) |
| `templates/agents/` | Subagent definitions → `.cursor/agents/` |
| `templates/skills/` | Skills → `.agents/skills/` |
| `templates/hooks/` | Governance hooks → `.cursor/hooks/` |
| `docs/` | Chassis spec, run layout, evidence ladder, owner course, unblocking policy |

## Requirements

- Cursor with subagent support (nesting: main → owner → stage agents).
- Python 3.10+ on PATH.
- Git. A tracker CLI (beads by default) is optional (`tracker.adapter: none` writes markdown registries).
- Optional MCPs (auto-detected, declared in config): graphify, sonarqube, context7, perplexity, browser, figma. The ui-ux mode requires the browser MCP.
