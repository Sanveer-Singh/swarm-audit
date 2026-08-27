#!/usr/bin/env python3
"""Execution waves: dependency DAG first, then path-conflict coloring, then severity.

Order of authority (D24):
1. Topological sort on depends_on. Cycles are a hard failure (block decomposition).
2. Within each ready frontier, color by allowed_paths overlap; shared-conflict
   paths never share a wave.
3. Within a wave, order by severity weight: security and journey blockers first.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import dump_json, load_config, norm_path, shared_conflict_hits

SEVERITY_WEIGHT = {
    "security": 0,
    "journey_blocker": 1,
    "blocking": 2,
    "major": 3,
    "minor": 4,
}


def overlaps(config: dict, left: list[str], right: list[str]) -> bool:
    left_n = {norm_path(p) for p in left}
    right_n = {norm_path(p) for p in right}
    if left_n & right_n:
        return True
    if shared_conflict_hits(config, list(left_n)) and shared_conflict_hits(config, list(right_n)):
        return True
    return False


def topo_frontiers(tasks: list[dict]) -> list[list[dict]]:
    """Kahn's algorithm producing ready frontiers. Raises ValueError on a cycle."""
    by_id = {t["id"]: t for t in tasks}
    indegree: dict[str, int] = {t["id"]: 0 for t in tasks}
    dependants: dict[str, list[str]] = {t["id"]: [] for t in tasks}
    for task in tasks:
        for dep in task.get("depends_on") or []:
            if dep not in by_id:
                continue  # external/completed dependency
            indegree[task["id"]] += 1
            dependants[dep].append(task["id"])
    frontier = [tid for tid, deg in indegree.items() if deg == 0]
    frontiers: list[list[dict]] = []
    seen = 0
    while frontier:
        frontiers.append([by_id[tid] for tid in frontier])
        seen += len(frontier)
        nxt: list[str] = []
        for tid in frontier:
            for child in dependants[tid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    nxt.append(child)
        frontier = nxt
    if seen != len(tasks):
        remaining = [tid for tid, deg in indegree.items() if deg > 0]
        raise ValueError(f"dependency_cycle:{sorted(remaining)}")
    return frontiers


def severity_key(task: dict) -> tuple:
    return (SEVERITY_WEIGHT.get(str(task.get("severity") or "").lower(), 9), task["id"])


def color_frontier(config: dict, frontier: list[dict]) -> list[list[dict]]:
    remaining = sorted(frontier, key=severity_key)
    waves: list[list[dict]] = []
    while remaining:
        wave: list[dict] = []
        next_round: list[dict] = []
        for task in remaining:
            if any(overlaps(config, task.get("allowed_paths") or [], taken.get("allowed_paths") or []) for taken in wave):
                next_round.append(task)
            else:
                wave.append(task)
        waves.append(wave)
        remaining = next_round
    return waves


def build_waves(config: dict, tasks: list[dict]) -> list[list[str]]:
    waves: list[list[str]] = []
    for frontier in topo_frontiers(tasks):
        for wave in color_frontier(config, frontier):
            waves.append([task["id"] for task in wave])
    return waves


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tasks_json", help="array of {id, allowed_paths, depends_on, severity}")
    parser.add_argument("--out")
    args = parser.parse_args()
    config = load_config()
    tasks = json.loads(Path(args.tasks_json).read_text(encoding="utf-8"))
    try:
        waves = build_waves(config, tasks)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    result = {"waves": [{"index": idx, "task_ids": ids} for idx, ids in enumerate(waves)]}
    if args.out:
        dump_json(Path(args.out), result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
