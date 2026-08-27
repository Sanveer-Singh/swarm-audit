#!/usr/bin/env python3
"""Config-driven gate runner. Orchestrator-executed; receipts are commit-scoped.

Timeout kills the whole process GROUP (F09) — a hung test host cannot leave
orphaned children. A timed-out gate is FAIL with reason=timeout, and the
receipt records it; retry consumes task budget upstream.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from swarm_lib import cfg, current_run_dir, dump_json, load_config, now_iso, repo_root

IS_WINDOWS = os.name == "nt"


def run_gate(command: dict, cwd: Path) -> dict:
    timeout = int(command.get("timeout_s", 600))
    popen_kwargs: dict = {
        "shell": True,
        "cwd": str(cwd),
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command["cmd"], **popen_kwargs)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        stdout, stderr = proc.communicate()
    return {
        "gate_id": command["id"],
        "cmd": command["cmd"],
        "cwd": str(cwd),
        "exit_code": 124 if timed_out else proc.returncode,
        "timeout": timed_out,
        "required": bool(command.get("required", True)),
        "summary": ((stdout or "")[-1500:] + "\n" + (stderr or "")[-1500:]).strip()[-2500:],
        "at": now_iso(),
    }


def gate_commands(config: dict, mode: str | None) -> list[dict]:
    commands = list(cfg(config, "gates.commands", []) or [])
    if mode:
        commands += list(cfg(config, f"gates.per_mode.{mode}", []) or [])
    return commands


def execute(config: dict, worktree: Path, commit: str, *, mode: str | None, only: list[str] | None, dry_run: bool) -> dict:
    receipts = []
    failed = False
    for command in gate_commands(config, mode):
        if only and command["id"] not in only:
            continue
        if dry_run:
            receipt = {
                "gate_id": command["id"],
                "cmd": command["cmd"],
                "commit": commit,
                "exit_code": 0,
                "summary": "dry_run",
                "dry_run": True,
                "required": bool(command.get("required", True)),
                "at": now_iso(),
            }
        else:
            gate_cwd = worktree / command.get("cwd", ".") if command.get("cwd", ".") != "." else worktree
            receipt = run_gate(command, gate_cwd)
            receipt["commit"] = commit
        receipts.append(receipt)
        if receipt["exit_code"] != 0 and receipt.get("required", True):
            failed = True
    return {"commit": commit, "worktree": str(worktree), "gates": receipts, "passed": not failed, "at": now_iso()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", default=None)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--mode", default=None)
    parser.add_argument("--only", default=None, help="comma-separated gate ids")
    parser.add_argument("--integrate", action="store_true", help="tag receipt as integration gates")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    config = load_config()
    worktree = Path(args.worktree) if args.worktree else repo_root(config)
    result = execute(
        config,
        worktree,
        args.commit,
        mode=args.mode,
        only=[x for x in (args.only or "").split(",") if x] or None,
        dry_run=args.dry_run,
    )
    if args.integrate:
        result["integrate"] = True
    out = Path(args.out) if args.out else None
    if out is None:
        run_dir = current_run_dir(config)
        if run_dir is not None:
            stem = "integrate" if args.integrate else "gates"
            out = run_dir / "gate-receipts" / f"{stem}-{args.commit[:12]}.json"
    if out is not None:
        dump_json(out, result)
    print(json.dumps({"passed": result["passed"], "gates": [(g["gate_id"], g["exit_code"]) for g in result["gates"]]}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
