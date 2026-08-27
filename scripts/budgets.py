#!/usr/bin/env python3
"""Run and per-task budget counters. Every recovery transition decrements here.

Zero remaining → the caller must block the task (or gracefully stop the run)
and write a human-queue row. Models never touch this file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import cfg, current_run_dir, dump_json, load_config, load_json, now_iso, per_task_budgets


def budgets_path(run_dir: Path) -> Path:
    return run_dir / "budgets.json"


def load(run_dir: Path, config: dict) -> dict:
    path = budgets_path(run_dir)
    if path.exists():
        return load_json(path)
    return {
        "run": {
            "agents_spawned": 0,
            "max_agents": cfg(config, "budgets.run.max_agents", 100),
            "started_at": now_iso(),
            "max_hours": cfg(config, "budgets.run.max_hours", 12),
        },
        "per_task": {},
        "updated_at": now_iso(),
    }


def save(run_dir: Path, state: dict) -> None:
    state["updated_at"] = now_iso()
    dump_json(budgets_path(run_dir), state)


def task_row(run_dir: Path, config: dict, task_id: str) -> dict:
    state = load(run_dir, config)
    row = state["per_task"].get(task_id)
    if row is None:
        row = dict(per_task_budgets(config))
        state["per_task"][task_id] = row
        save(run_dir, state)
    return row


def consume_task(run_dir: Path, config: dict, task_id: str, kind: str) -> tuple[bool, int]:
    state = load(run_dir, config)
    row = state["per_task"].setdefault(task_id, dict(per_task_budgets(config)))
    remaining = int(row.get(kind, 0))
    if remaining <= 0:
        return False, 0
    row[kind] = remaining - 1
    save(run_dir, state)
    return True, remaining - 1


def count_agent(run_dir: Path, config: dict) -> tuple[bool, int]:
    state = load(run_dir, config)
    run = state["run"]
    if run["agents_spawned"] >= run["max_agents"]:
        return False, run["agents_spawned"]
    run["agents_spawned"] += 1
    save(run_dir, state)
    return True, run["agents_spawned"]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    consume = sub.add_parser("consume")
    consume.add_argument("--task-id", required=True)
    consume.add_argument("--kind", required=True, choices=["owner_attempts", "evidence_refreshes", "architect_consults", "replans"])
    sub.add_parser("agent")
    sub.add_parser("show")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL budgets: CURRENT missing")
        return 1
    if args.cmd == "consume":
        ok, remaining = consume_task(run_dir, config, args.task_id, args.kind)
        print(json.dumps({"ok": ok, "remaining": remaining}))
        return 0 if ok else 1
    if args.cmd == "agent":
        ok, count = count_agent(run_dir, config)
        print(json.dumps({"ok": ok, "agents_spawned": count}))
        return 0 if ok else 1
    print(json.dumps(load(run_dir, config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
