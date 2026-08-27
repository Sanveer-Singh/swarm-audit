#!/usr/bin/env python3
"""subagentStop: log terminal event, release slot reservation.

Best-effort: the platform does not guarantee identity fields here, so the stop
event correlates on packet_path when present, subagent_id otherwise. The
orchestrator reconciles uncorrelated agents via registry.py + timeouts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_common import (  # noqa: E402
    allow,
    append_event,
    load_stdin,
    run_dir_or_none,
    toolkit_available,
    utc_now,
)


def main() -> int:
    payload = load_stdin(fail_closed=False)
    if not toolkit_available():
        allow()
        return 0

    import swarm_lib  # noqa: E402

    run_dir = run_dir_or_none()
    sub_type = str(payload.get("subagent_type") or payload.get("type") or "")
    task = str(payload.get("task") or payload.get("prompt") or "")
    packet_path = swarm_lib.extract_packet_path(task) or payload.get("packet_path")

    if run_dir is not None and (packet_path or payload.get("subagent_id") or sub_type):
        append_event(
            run_dir,
            {
                "event": "stop",
                "at": utc_now(),
                "subagent_id": payload.get("subagent_id"),
                "subagent_type": sub_type,
                "packet_path": packet_path,
                "status": payload.get("status") or "completed",
                "pid": os.getpid(),
            },
        )
        if packet_path:
            try:
                from admission import release  # noqa: E402

                release(run_dir, f"pkt:{packet_path}")
            except Exception:  # noqa: BLE001 - stop hook must never fail the platform
                pass

    allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
