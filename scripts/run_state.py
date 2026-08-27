#!/usr/bin/env python3
"""Read/write state.json, enforce legal transitions, render state.md, checkpoints."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from swarm_lib import PHASES, PHASE_SET, TASK_STATES, current_run_dir, dump_json, load_config
from validate_payload import validate

FORWARD = {phase: PHASES[idx + 1] for idx, phase in enumerate(PHASES[:-1])}

# Extra legal edges beyond strict forward.
EXTRA = {
    ("dedupe", "audit"),       # next mode wave
    ("cleanup", "wave"),       # next task wave
    ("course", "decompose"),   # replan with new plan revision
    ("verify", "course"),      # ONLY legal with a new plan hash; orchestrator must prove via replan receipt
}

TASK_FORWARD = {state: TASK_STATES[idx + 1] for idx, state in enumerate(TASK_STATES[:-3])}


def legal_transition(current: str, nxt: str) -> bool:
    if nxt == "blocked":
        return True
    if current == "blocked":
        return nxt == "close"  # explicit human-sanctioned close of a blocked run
    if nxt == current:
        return True
    if (current, nxt) in EXTRA:
        return True
    return FORWARD.get(current) == nxt


def legal_task_transition(current: str, nxt: str) -> bool:
    if nxt in {"blocked", "replan_required"}:
        return True
    if current == "replan_required" and nxt == "pending":
        return True  # hash-change requirement enforced in task_transition
    if current == "blocked":
        return False
    return TASK_FORWARD.get(current) == nxt


def load_state(run_dir: Path) -> dict:
    path = run_dir / "state.json"
    if not path.exists():
        raise FileNotFoundError(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    errors = validate("run_state", state)
    if errors:
        raise ValueError(", ".join(errors))
    return state


def render_md(state: dict) -> str:
    lines = [f"# Run {state.get('run_id')}", ""]
    for key in sorted(state):
        if key == "tasks":
            continue
        lines.append(f"- {key}: {json.dumps(state[key])}")
    tasks = state.get("tasks") or {}
    if tasks:
        lines += ["", "## Tasks", "", "| task | state | plan | worktree |", "|---|---|---|---|"]
        for tid in sorted(tasks):
            row = tasks[tid]
            lines.append(
                f"| {tid} | {row.get('state')} | {row.get('plan_sha256', '')[:18]} | {row.get('worktree_id', '')} |"
            )
    return "\n".join(lines) + "\n"


def persist(run_dir: Path, state: dict) -> None:
    errors = validate("run_state", state)
    if errors:
        raise ValueError(", ".join(errors))
    dump_json(run_dir / "state.json", state)
    (run_dir / "state.md").write_text(render_md(state), encoding="utf-8")
    dump_json(run_dir / "checkpoints" / f"{state['phase']}.json", state)


def transition(run_dir: Path, nxt: str, next_action: str) -> dict:
    state = load_state(run_dir)
    current = state["phase"]
    if nxt not in PHASE_SET:
        raise ValueError(f"illegal_phase:{nxt}")
    if not legal_transition(current, nxt):
        raise ValueError(f"illegal_transition:{current}->{nxt}")
    if (current, nxt) == ("verify", "course"):
        # forbid silent redo: require a replan receipt reference in next_action
        if "replan" not in (next_action or "").lower():
            raise ValueError("verify->course requires new plan revision (replan receipt)")
    state["phase"] = nxt
    state["next_action"] = next_action
    if nxt == "close":
        # A closed run must stop reading as live (installer/uninstaller guards,
        # hooks' run_dir_or_none) — smoke finding.
        state["status"] = "closed"
    persist(run_dir, state)
    return state


def task_transition(run_dir: Path, task_id: str, nxt: str, **stamps) -> dict:
    state = load_state(run_dir)
    tasks = state.setdefault("tasks", {})
    row = tasks.setdefault(task_id, {"state": "pending"})
    current = row.get("state", "pending")
    if not legal_task_transition(current, nxt):
        raise ValueError(f"illegal_task_transition:{task_id}:{current}->{nxt}")
    if current == "replan_required" and nxt == "pending":
        # One-way gate (D6): re-entering the course REQUIRES a new plan hash.
        new_hash = stamps.get("plan_sha256")
        if not new_hash or new_hash == row.get("plan_sha256"):
            raise ValueError(f"replan_requires_new_plan_hash:{task_id}")
        # The gate forbids re-implementing the SAME plan after delta; an accepted
        # new-hash replan opens a fresh course, so the delta lock resets with it.
        row.pop("delta_done", None)
    if row.get("delta_done") and nxt in {"coursing", "remediated"}:
        raise ValueError(f"post_delta_reimplement_forbidden:{task_id}")
    if nxt == "delta_passed":
        row["delta_done"] = True
    row["state"] = nxt
    for key, value in stamps.items():
        if value is not None:
            row[key] = value
    persist(run_dir, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--run-id", required=True)
    init.add_argument("--base-commit", default="HEAD")
    init.add_argument("--epic", default="")
    init.add_argument("--modes", default="")
    init.add_argument("--tier", default="")
    move = sub.add_parser("transition")
    move.add_argument("--to", required=True)
    move.add_argument("--next-action", required=True)
    tmove = sub.add_parser("task")
    tmove.add_argument("--task-id", required=True)
    tmove.add_argument("--to", required=True)
    tmove.add_argument("--plan-sha256", default=None)
    tmove.add_argument("--worktree-id", default=None)
    tmove.add_argument("--receipt", default=None)
    sub.add_parser("show")
    args = parser.parse_args()

    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL run_state: CURRENT missing", file=sys.stderr)
        return 1
    try:
        if args.cmd == "init":
            from swarm_lib import cfg  # local import to keep module top minimal

            state = {
                "run_id": args.run_id,
                "phase": "init",
                "next_action": "intake",
                "base_commit": args.base_commit,
                "status": "running",
                "modes": [m for m in args.modes.split(",") if m] or cfg(config, "modes.default", []),
                "tier": args.tier or cfg(config, "orchestration.tier", "owner"),
                "tasks": {},
            }
            if args.epic:
                state["epic"] = args.epic
            persist(run_dir, state)
        elif args.cmd == "transition":
            state = transition(run_dir, args.to, args.next_action)
        elif args.cmd == "task":
            state = task_transition(
                run_dir,
                args.task_id,
                args.to,
                plan_sha256=args.plan_sha256,
                worktree_id=args.worktree_id,
                receipt=args.receipt,
            )
        else:
            state = load_state(run_dir)
        print(json.dumps(state, indent=2))
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"FAIL run_state: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
