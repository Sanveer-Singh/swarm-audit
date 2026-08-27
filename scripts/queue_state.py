#!/usr/bin/env python3
"""Per-run decomposition queue: nodes, ancestor chains, attempts, budgets.

The orchestrator decrements budgets before spawning, never the model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import caps as load_caps
from swarm_lib import current_run_dir, dump_json, load_config, load_json


def queue_path(run_dir: Path) -> Path:
    return run_dir / "decomposition" / "queue.json"


def load_queue(run_dir: Path, config: dict) -> dict:
    path = queue_path(run_dir)
    if path.exists():
        return load_json(path)
    decomp = load_caps(config)["decomposition"]
    return {
        "pending": [],
        "budget_global_remaining": decomp["global"],
        "nodes": {},
    }


def save_queue(run_dir: Path, queue: dict) -> None:
    dump_json(queue_path(run_dir), queue)


def enqueue(run_dir: Path, config: dict, cluster_id: str, parent_id: str | None = None) -> dict:
    queue = load_queue(run_dir, config)
    decomp = load_caps(config)["decomposition"]
    nodes = queue["nodes"]
    parent_node = nodes.get(parent_id or "", {})
    node = nodes.get(cluster_id) or {
        "cluster_id": cluster_id,
        "parent": parent_id,
        "depth": (parent_node.get("depth", -1) + 1) if parent_id else 0,
        "ancestors": (parent_node.get("ancestors", []) + [parent_id]) if parent_id else [],
        "attempts": 0,
        "budget_branch_remaining": load_caps(config)["decomposition"]["branch"],
    }
    nodes[cluster_id] = node
    if cluster_id not in queue["pending"]:
        queue["pending"].append(cluster_id)
    if node["depth"] > decomp["depth"]:
        node["forced_atomic"] = True
    save_queue(run_dir, queue)
    return node


def consume_budget(run_dir: Path, config: dict, cluster_id: str) -> tuple[bool, str | None]:
    queue = load_queue(run_dir, config)
    node = queue["nodes"].get(cluster_id)
    if node is None:
        return False, "unknown_node"
    if queue["budget_global_remaining"] <= 0:
        return False, "global_budget_exhausted"
    if node.get("budget_branch_remaining", 0) <= 0:
        return False, "branch_budget_exhausted"
    queue["budget_global_remaining"] -= 1
    node["budget_branch_remaining"] -= 1
    node["attempts"] = node.get("attempts", 0) + 1
    save_queue(run_dir, queue)
    return True, None


def pop_pending(run_dir: Path, config: dict) -> str | None:
    queue = load_queue(run_dir, config)
    if not queue["pending"]:
        return None
    cluster_id = queue["pending"].pop(0)
    save_queue(run_dir, queue)
    return cluster_id


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    enq = sub.add_parser("enqueue")
    enq.add_argument("--cluster-id", required=True)
    enq.add_argument("--parent", default=None)
    con = sub.add_parser("consume")
    con.add_argument("--cluster-id", required=True)
    sub.add_parser("pop")
    sub.add_parser("show")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL queue_state: CURRENT missing")
        return 1
    if args.cmd == "enqueue":
        print(json.dumps(enqueue(run_dir, config, args.cluster_id, args.parent), indent=2))
    elif args.cmd == "consume":
        ok, reason = consume_budget(run_dir, config, args.cluster_id)
        print(json.dumps({"ok": ok, "reason": reason}))
        return 0 if ok else 1
    elif args.cmd == "pop":
        print(json.dumps({"cluster_id": pop_pending(run_dir, config)}))
    else:
        print(json.dumps(load_queue(run_dir, config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
