#!/usr/bin/env python3
"""Role-reserved admission with atomic, file-locked reservation records.

Two simultaneous hook processes must never both take the last slot (F10).
Reservations expire so crashed spawns cannot leak slots forever.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from swarm_lib import caps as load_caps
from swarm_lib import is_writer, load_config, now_iso

RESERVATION_TTL_S = 15 * 60

ROLE_RESERVE = {
    "swarm-auditor": {"live": 1, "writers": 0},
    "swarm-architect": {"live": 1, "writers": 0},
    "swarm-owner": {"live": 1, "writers": 0},
    "swarm-planner": {"live": 1, "writers": 0},
    "swarm-red-team": {"live": 1, "writers": 0},
    "swarm-ui-red-team": {"live": 1, "writers": 0},
    "swarm-solution-architect": {"live": 1, "writers": 0},
    "swarm-implementer": {"live": 1, "writers": 1},
}

# Worst case concurrent set for one owner course wave at max_writers=2:
# 2 owners + 2 implementers + red-team + ui-red-team + planner + architect.
WORST_CASE_ROLES = (
    "swarm-owner",
    "swarm-owner",
    "swarm-implementer",
    "swarm-implementer",
    "swarm-red-team",
    "swarm-ui-red-team",
    "swarm-planner",
    "swarm-solution-architect",
)


@contextmanager
def file_lock(lock_path: Path, timeout_s: float = 10.0):
    """Cross-platform exclusive lock via os.O_CREAT|os.O_EXCL sentinel with retry.

    Portable (no msvcrt/fcntl divergence) and adequate for hook-frequency access.
    Stale sentinels older than 60s are removed (crashed locker).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 60:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"lock timeout: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def reserve_for(role: str) -> dict[str, int]:
    for name, reserve in ROLE_RESERVE.items():
        if name in (role or ""):
            return reserve
    if is_writer(role):
        return {"live": 1, "writers": 1}
    return {"live": 1, "writers": 0}


def _load_reservations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    fresh = [r for r in rows if time.time() - r.get("epoch", 0) < RESERVATION_TTL_S]
    return fresh


def try_reserve(
    run_dir: Path,
    role: str,
    key: str,
    live_baseline: int,
    writer_baseline: int,
    live_keys: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Atomically reserve a slot. Baselines come from registry counts; reservations
    cover only the window between hook decision and registry visibility, so any
    reservation whose key is already live in the registry is dropped (no double
    counting once the spawn event materializes)."""
    config = load_config()
    limits = load_caps(config)
    res_path = run_dir / "reservations.json"
    lock = run_dir / "reservations.lock"
    reserve = reserve_for(role)
    live_keys = live_keys or set()
    with file_lock(lock):
        rows = _load_reservations(res_path)
        rows = [r for r in rows if r.get("key") != key and r.get("key") not in live_keys]
        reserved_live = sum(r.get("live", 0) for r in rows)
        reserved_writers = sum(r.get("writers", 0) for r in rows)
        if live_baseline + reserved_live + reserve["live"] > limits["max_live_agents"]:
            return False, "live_cap"
        if reserve["writers"] and writer_baseline + reserved_writers + reserve["writers"] > limits["max_writers"]:
            return False, "writer_cap"
        rows.append({"key": key, "role": role, "epoch": time.time(), "at": now_iso(), **reserve})
        res_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return True, None


def release(run_dir: Path, key: str) -> None:
    res_path = run_dir / "reservations.json"
    lock = run_dir / "reservations.lock"
    if not res_path.exists():
        return
    with file_lock(lock):
        rows = _load_reservations(res_path)
        rows = [r for r in rows if r.get("key") != key]
        res_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def can_admit(role: str, live: int, writers: int, *, pipeline: bool, writable: bool, config: dict | None = None) -> tuple[bool, str | None]:
    if not pipeline:
        return True, None
    limits = load_caps(config or load_config())
    reserve = reserve_for(role)
    if live + reserve["live"] > limits["max_live_agents"]:
        return False, "live_cap"
    if writable and writers + reserve["writers"] > limits["max_writers"]:
        return False, "writer_cap"
    return True, None


def worst_case_occupancy(config: dict | None = None) -> dict:
    limits = load_caps(config or load_config())
    live = 0
    writers = 0
    for role in WORST_CASE_ROLES:
        reserve = ROLE_RESERVE[role]
        live += reserve["live"]
        writers += reserve["writers"]
    return {
        "roles": list(WORST_CASE_ROLES),
        "live": live,
        "writers": writers,
        "max_live": limits["max_live_agents"],
        "max_writers": limits["max_writers"],
        "fits": live <= limits["max_live_agents"] and writers <= limits["max_writers"],
    }


def main() -> int:
    occ = worst_case_occupancy()
    print(json.dumps(occ, indent=2))
    return 0 if occ["fits"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
