#!/usr/bin/env python3
"""subagentStart: fail-closed governance for swarm-* spawns.

Order: payload readable -> role classified -> run live -> packet present and
schema-valid -> model pinning (when enforce_model in task) -> atomic slot
reservation (registry baseline + file-locked reservations). Non-swarm spawns
always pass untouched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_common import (  # noqa: E402
    ROOT,
    HookInputError,
    allow,
    append_event,
    deny,
    expected_model,
    load_stdin,
    run_dir_or_none,
    swarm_config,
    toolkit_available,
    utc_now,
    validate_packet_file,
)


def main() -> int:
    raw_payload = None
    stdin_error = None
    try:
        raw_payload = load_stdin(fail_closed=True)
    except HookInputError as exc:
        stdin_error = str(exc)

    if not toolkit_available():
        # No swarm install context: we cannot even classify. Allow (non-governed repo).
        allow()
        return 0

    import swarm_lib  # noqa: E402 - toolkit path injected by hook_common

    run_dir = run_dir_or_none()
    config = swarm_config()

    if stdin_error is not None:
        append_event(
            run_dir,
            {
                "event": "spawn",
                "at": utc_now(),
                "status": "denied",
                "reason": stdin_error,
                "stdin_empty": True,
                "pid": os.getpid(),
            },
        )
        deny(f"swarm spawn denied: hook stdin {stdin_error}. Ungovernable payloads are refused while a swarm toolkit is installed.")
        return 0

    payload = raw_payload or {}
    sub_type = str(payload.get("subagent_type") or payload.get("type") or "")
    task = str(payload.get("task") or payload.get("prompt") or "")
    model = payload.get("subagent_model") or payload.get("model")
    packet_path = swarm_lib.extract_packet_path(task)
    pipeline = swarm_lib.is_pipeline(sub_type)

    if not pipeline:
        allow()
        return 0

    event = {
        "event": "spawn",
        "at": utc_now(),
        "subagent_id": payload.get("subagent_id"),
        "subagent_type": sub_type,
        "subagent_model_selected_at_spawn": model,
        "packet_path": packet_path,
        "payload_keys": sorted(payload.keys()),
        "stdin_empty": False,
        "pid": os.getpid(),
    }

    if run_dir is None:
        deny("swarm spawn denied: no live run (CURRENT missing). Start one with new_run.py.")
        return 0

    if not packet_path:
        append_event(run_dir, {**event, "status": "denied", "reason": "missing_packet"})
        deny("swarm spawn denied: packet_path: missing from task text. Spawner must pass a bounded packet.")
        return 0

    errors = validate_packet_file(ROOT, packet_path)
    if errors:
        append_event(run_dir, {**event, "status": "denied", "reason": "invalid_packet", "errors": errors[:8]})
        deny("swarm spawn denied: packet failed schema validation: " + ", ".join(errors[:6]))
        return 0

    expected = expected_model(ROOT, sub_type, config)
    enforce = "enforce_model: true" in task.lower() or "enforce_model:true" in task.lower()
    if enforce and model and expected and model != expected:
        append_event(run_dir, {**event, "status": "denied", "reason": "model_mismatch", "expected_model": expected})
        deny(f"swarm spawn denied: selected model {model} != pinned {expected}.")
        return 0

    from admission import try_reserve  # noqa: E402
    from registry import LIVE_STATUSES, snapshot  # noqa: E402

    snap = snapshot(run_dir)
    live, writers = snap["live"], snap["writers"]
    live_keys = {a["key"] for a in snap["agents"] if a.get("status") in LIVE_STATUSES}
    key = f"pkt:{packet_path}"
    admitted, admit_reason = try_reserve(run_dir, sub_type, key, live, writers, live_keys=live_keys)
    if not admitted:
        append_event(run_dir, {**event, "status": "denied", "reason": admit_reason, "live": live, "writers": writers})
        deny(f"swarm spawn denied: {admit_reason} (live={live} writers={writers}).")
        return 0

    append_event(
        run_dir,
        {**event, "status": "allowed", "model_match": (model == expected) if expected and model else None},
    )
    allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
