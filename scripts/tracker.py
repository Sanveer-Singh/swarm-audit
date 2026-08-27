#!/usr/bin/env python3
"""Tracker adapter. Orchestrator-only. All mutations journaled with idempotency keys.

Adapters:
- beads/custom: renders templated commands from tracker.commands and executes them.
- none: writes markdown registry rows to {paths.registries}/issue-registry.md.

A create with an already-completed journal key returns the recorded external id
instead of re-executing (crash-safe retry, F08).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from journal import already_completed, journaled
from swarm_lib import cfg, current_run_dir, load_config, now_iso, registries_root, repo_root

ID_HINT = re.compile(r"\b([a-z][a-z0-9]*-[a-z0-9.]+)\b")


def render(template: str, params: dict) -> str:
    out = template
    for key, value in params.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def execute(config: dict, command: str) -> dict:
    proc = subprocess.run(
        command, shell=True, cwd=repo_root(config), text=True, capture_output=True, check=False, timeout=120
    )
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1500:],
    }


def parse_id(stdout: str) -> str | None:
    match = ID_HINT.search(stdout or "")
    return match.group(1) if match else None


def registry_row(config: dict, action: str, params: dict) -> dict:
    path = registries_root(config) / "issue-registry.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Issue Registry (tracker.adapter=none)\n\n", encoding="utf-8")
    row = f"- [{now_iso()}] {action}: {json.dumps(params, sort_keys=True)}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)
    return {"action": action, "ok": True, "registry_row": row.strip()}


def act(config: dict, run_dir: Path, action: str, params: dict, idempotency_key: str) -> dict:
    done = already_completed(run_dir, idempotency_key)
    if done:
        return {"action": action, "ok": True, "id": done.get("external_id"), "stdout_tail": "journal-replay"}

    adapter = cfg(config, "tracker.adapter", "none")
    with journaled(run_dir, idempotency_key, {"action": action, "params": params}) as entry:
        if adapter == "none":
            result = registry_row(config, action, params)
            entry.complete(external_id=params.get("id"), result="registry_row")
            return result
        template = cfg(config, f"tracker.commands.{action}")
        if not template:
            entry.rollback(f"no template for action {action}")
            return {"action": action, "ok": False, "stderr_tail": f"no tracker.commands.{action}"}
        command = render(template, params)
        result = execute(config, command)
        result["action"] = action
        if result["ok"]:
            external = params.get("id") or parse_id(result["stdout_tail"])
            result["id"] = external
            entry.complete(external_id=external, result={"exit_code": 0})
        else:
            entry.rollback(f"exit {result['exit_code']}: {result['stderr_tail'][:200]}")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "update", "close", "dep", "comment", "show"])
    parser.add_argument("--params", required=True, help="JSON object of template params")
    parser.add_argument("--key", required=True, help="idempotency key")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL tracker: CURRENT missing")
        return 1
    result = act(config, run_dir, args.action, json.loads(args.params), args.key)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
