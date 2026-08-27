#!/usr/bin/env python3
"""Durable activity journal for external side effects (F08, VA8).

Every tracker mutation, worktree create/merge/remove, and integration commit
writes an intent entry BEFORE execution and a completion entry AFTER. On
resume, `reconcile` replays incomplete entries: a checker function decides
whether the effect actually landed, then completes or rolls back the entry.

Usage from the orchestrator (python API):

    from journal import journaled
    with journaled(run_dir, key="tracker-create:task-3", intent={...}) as entry:
        result = do_side_effect()
        entry.complete(external_id=result_id, result=summary)

If the process dies inside the block, the entry stays at status=intent and
reconcile surfaces it with its idempotency key so the orchestrator can check
the external system before retrying — never blind-retry a create.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import append_jsonl, current_run_dir, load_config, now_iso, read_jsonl


def journal_path(run_dir: Path) -> Path:
    return run_dir / "journal.jsonl"


class Entry:
    def __init__(self, run_dir: Path, key: str, intent: dict, task_id: str | None = None):
        self.run_dir = run_dir
        self.key = key
        self.intent = intent
        self.task_id = task_id
        self.completed = False

    def open(self) -> None:
        append_jsonl(
            journal_path(self.run_dir),
            {
                "idempotency_key": self.key,
                "intent": self.intent,
                "status": "intent",
                "task_id": self.task_id,
                "at": now_iso(),
            },
        )

    def complete(self, external_id: str | None = None, result: dict | str | None = None) -> None:
        append_jsonl(
            journal_path(self.run_dir),
            {
                "idempotency_key": self.key,
                "intent": self.intent,
                "status": "completed",
                "external_id": external_id,
                "result": result,
                "task_id": self.task_id,
                "at": now_iso(),
            },
        )
        self.completed = True

    def rollback(self, note: str) -> None:
        append_jsonl(
            journal_path(self.run_dir),
            {
                "idempotency_key": self.key,
                "intent": self.intent,
                "status": "rolled_back",
                "reconcile_note": note,
                "task_id": self.task_id,
                "at": now_iso(),
            },
        )
        self.completed = True


class journaled:
    """Context manager. Does NOT auto-complete: the caller must call
    entry.complete()/entry.rollback() so completion is an explicit fact."""

    def __init__(self, run_dir: Path, key: str, intent: dict, task_id: str | None = None):
        self.entry = Entry(run_dir, key, intent, task_id)

    def __enter__(self) -> Entry:
        self.entry.open()
        return self.entry

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False  # incomplete entries stay visible to reconcile


def state_of(run_dir: Path) -> dict[str, dict]:
    """Latest status per idempotency key."""
    latest: dict[str, dict] = {}
    for row in read_jsonl(journal_path(run_dir)):
        latest[row["idempotency_key"]] = row
    return latest


def already_completed(run_dir: Path, key: str) -> dict | None:
    row = state_of(run_dir).get(key)
    if row and row.get("status") == "completed":
        return row
    return None


def incomplete(run_dir: Path) -> list[dict]:
    return [row for row in state_of(run_dir).values() if row.get("status") == "intent"]


def reconcile(run_dir: Path) -> dict:
    """Auto-resolve incomplete entries whose intent declares a deterministic check:

    - intent.check == "path_exists": effect landed iff intent.path exists
    - intent.check == "path_absent": effect landed iff intent.path is gone

    Entries without a declared check (e.g. tracker creates without recorded ids)
    are returned as `needs_review` — the orchestrator must verify the external
    system before retrying. Never blind-retry a create.
    """
    resolved, needs_review = [], []
    for row in incomplete(run_dir):
        intent = row.get("intent") or {}
        check = intent.get("check")
        target = intent.get("path")
        if check in {"path_exists", "path_absent"} and target:
            landed = Path(target).exists() if check == "path_exists" else not Path(target).exists()
            mark_reconciled(run_dir, row["idempotency_key"], f"auto:{check}={landed}", landed)
            resolved.append({"key": row["idempotency_key"], "landed": landed})
        else:
            needs_review.append(row)
    return {"resolved": resolved, "needs_review": needs_review}


def mark_reconciled(run_dir: Path, key: str, note: str, completed: bool, external_id: str | None = None) -> None:
    append_jsonl(
        journal_path(run_dir),
        {
            "idempotency_key": key,
            "intent": state_of(run_dir).get(key, {}).get("intent", {}),
            "status": "completed" if completed else "rolled_back",
            "external_id": external_id,
            "reconcile_note": note,
            "at": now_iso(),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reconcile", help="auto-resolve declared path checks; print needs_review rows")
    sub.add_parser("show")
    mark = sub.add_parser("mark")
    mark.add_argument("--key", required=True)
    mark.add_argument("--note", required=True)
    mark.add_argument("--completed", action="store_true")
    mark.add_argument("--external-id", default=None)
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL journal: CURRENT missing")
        return 1
    if args.cmd == "reconcile":
        result = reconcile(run_dir)
        print(json.dumps(result, indent=2))
        return 1 if result["needs_review"] else 0
    if args.cmd == "mark":
        mark_reconciled(run_dir, args.key, args.note, args.completed, args.external_id)
        print("marked")
        return 0
    print(json.dumps(list(state_of(run_dir).values()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
