#!/usr/bin/env python3
"""Create a run folder skeleton and point CURRENT at it. Refuses if a run is live."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone

from swarm_lib import current_pointer, current_run_dir, dump_json, load_config, repo_root, run_root

SUBDIRS = [
    "findings",
    "clusters",
    "decomposition",
    "packets",
    "payloads",
    "course-reports",
    "ledger",
    "gate-receipts",
    "checkpoints",
    "partitions",
    "research",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--force", action="store_true", help="replace CURRENT even if a run is live")
    args = parser.parse_args()
    config = load_config()
    live = current_run_dir(config)
    if live is not None and not args.force:
        state_path = live / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") == "running":
                print(f"FAIL new_run: run {live.name} is live (use resume, or --force)")
                return 1
    run_id = args.run_id or f"swarm-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    base = run_root(config)
    run_dir = base / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = base / f"{run_id}-{suffix}"
    for sub in SUBDIRS:
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root(config), text=True, capture_output=True, check=False
    ).stdout.strip()
    dump_json(run_dir / "meta.json", {"run_id": run_dir.name, "base_commit": head})
    for name in ("human-queue.md", "leftovers.md", "decisions.md"):
        (run_dir / name).write_text(f"# {name.split('.')[0]} — {run_dir.name}\n\n", encoding="utf-8")
    pointer = current_pointer(config)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(run_dir.name + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_dir.name, "run_dir": str(run_dir), "base_commit": head}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
