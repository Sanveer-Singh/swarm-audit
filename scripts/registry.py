#!/usr/bin/env python3
"""Reconstruct live/terminal agent state from agents.jsonl.

Primary correlation key is packet_path echoed in task text (D20);
subagent_id is optional enrichment. Events with neither identity never
count as live.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import current_run_dir, dump_json, is_writer, load_config, read_jsonl

LIVE_STATUSES = {"spawned", "running", "allowed"}
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "timed_out",
    "aborted",
    "hung",
    "interrupted",
    "reclaimed",
    "denied",
    "unenforced_legacy",
}


def event_key(event: dict) -> str | None:
    packet = event.get("packet_path")
    if packet:
        return f"pkt:{packet}"
    sid = event.get("subagent_id")
    if sid:
        return f"id:{sid}"
    return None


def reduce_events(events: list[dict]) -> dict[str, dict]:
    by_key: dict[str, dict] = {}
    for event in events:
        key = event_key(event)
        if key is None:
            continue
        current = by_key.get(key, {"packet_path": event.get("packet_path"), "events": []})
        current["events"].append(event)
        if event.get("event") == "spawn":
            current["role"] = event.get("subagent_type")
            current["subagent_id"] = event.get("subagent_id") or current.get("subagent_id")
            current["model_selected_at_spawn"] = event.get("subagent_model_selected_at_spawn")
            status = event.get("status") or "running"
            current["status"] = "running" if status == "allowed" else status
            current["writable"] = is_writer(str(event.get("subagent_type") or ""))
            current["spawned_at"] = event.get("at")
        elif event.get("event") == "stop":
            current["status"] = event.get("status") or "completed"
            current["role"] = current.get("role") or event.get("subagent_type")
            if event.get("subagent_id"):
                current["subagent_id"] = event.get("subagent_id")
            current["stopped_at"] = event.get("at")
        by_key[key] = current
    return by_key


def count_unidentifiable(events: list[dict]) -> int:
    return sum(1 for event in events if event.get("event") == "spawn" and event_key(event) is None)


def snapshot(run_dir: Path | None) -> dict:
    if run_dir is None:
        return {"agents": [], "live": 0, "writers": 0, "unidentifiable_spawns": 0}
    events = read_jsonl(run_dir / "agents.jsonl")
    reduced = reduce_events(events)
    agents = []
    live = 0
    writers = 0
    for key, rec in reduced.items():
        status = rec.get("status") or "unknown"
        live_now = status in LIVE_STATUSES
        if live_now:
            live += 1
            if rec.get("writable"):
                writers += 1
        agents.append(
            {
                "key": key,
                "packet_path": rec.get("packet_path"),
                "subagent_id": rec.get("subagent_id"),
                "role": rec.get("role") or "",
                "status": "running" if status == "allowed" else status,
                "model_selected_at_spawn": rec.get("model_selected_at_spawn"),
                "writable": bool(rec.get("writable")),
            }
        )
    return {
        "agents": agents,
        "live": live,
        "writers": writers,
        "unidentifiable_spawns": count_unidentifiable(events),
    }


def count_live(run_dir: Path | None) -> tuple[int, int]:
    snap = snapshot(run_dir)
    return snap["live"], snap["writers"]


def reclaim_stale(run_dir: Path, older_than_min: int) -> list[str]:
    """Append reclaim stop events for live agents whose spawn is older than the
    timeout and that never produced a stop event (F15 / D19 dead-agent duty)."""
    import datetime as _dt

    events = read_jsonl(run_dir / "agents.jsonl")
    reduced = reduce_events(events)
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=older_than_min)
    reclaimed: list[str] = []
    for key, rec in reduced.items():
        if (rec.get("status") or "") not in LIVE_STATUSES:
            continue
        spawned_at = rec.get("spawned_at")
        try:
            when = _dt.datetime.fromisoformat(str(spawned_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            when = None
        if when is None or when <= cutoff:
            stop = {
                "event": "stop",
                "status": "reclaimed",
                "packet_path": rec.get("packet_path"),
                "subagent_id": rec.get("subagent_id"),
                "subagent_type": rec.get("role"),
                "reason": f"reclaim_stale>{older_than_min}min",
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            with (run_dir / "agents.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(stop) + "\n")
            reclaimed.append(key)
    return reclaimed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--reclaim-stale", type=int, default=None, metavar="MINUTES",
                        help="append reclaim stop events for live agents older than N minutes")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL registry: CURRENT missing")
        return 1
    if args.reclaim_stale is not None:
        reclaimed = reclaim_stale(run_dir, args.reclaim_stale)
        print(f"reclaimed: {json.dumps(reclaimed)}")
    snap = snapshot(run_dir)
    dump_json(Path(args.out) if args.out else run_dir / "registry.json", snap)
    print(
        f"registry: live={snap['live']} writers={snap['writers']} "
        f"unidentifiable={snap['unidentifiable_spawns']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
