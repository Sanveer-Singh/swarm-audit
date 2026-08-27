#!/usr/bin/env python3
"""Assemble schema-valid spawn packets with embedded requirement text.

Agents never read the requirement index themselves; the packet embeds the text
so a spawn is self-contained and auditable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from swarm_lib import (
    cfg,
    current_run_dir,
    dump_json,
    load_config,
    load_json,
    per_task_budgets,
    repo_root,
)
from validate_payload import validate


def git_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False
    )
    return proc.stdout.strip() or "HEAD"


def capability_manifest(config: dict) -> dict:
    return {
        "graphify": bool(cfg(config, "mcp.graphify", False)),
        "sonar": bool(cfg(config, "mcp.sonar.enabled", False)),
        "context7": bool(cfg(config, "mcp.context7", False)),
        "perplexity": bool(cfg(config, "mcp.perplexity", False)),
        "browser": bool(cfg(config, "mcp.browser", False)),
        "figma": bool(cfg(config, "mcp.figma", False)),
        "stack": cfg(config, "project.stack", ""),
    }


def requirement_text_for(index: dict, requirement_ids: list[str]) -> dict[str, str]:
    rows = {row["requirement_id"]: row for row in index.get("rows") or []}
    embedded: dict[str, str] = {}
    for rid in requirement_ids:
        row = rows.get(rid)
        embedded[rid] = f"[{row['source']}] {row['text']}" if row else "(no documented text: inferred or code-contract requirement)"
    return embedded


def build_packet(
    config: dict,
    run_dir: Path,
    *,
    agent_role: str,
    goal: str,
    non_goals: list[str],
    requirement_ids: list[str],
    allowed_paths: list[str],
    forbidden_paths: list[str],
    commands: list[str],
    output_kind: str,
    name: str,
    mode: str | None = None,
    task_id: str | None = None,
    attempt: int = 1,
    extra: dict | None = None,
) -> Path:
    index_path = run_dir / "req_index.json"
    index = load_json(index_path) if index_path.exists() else {"rows": []}
    packet_rel = f"{cfg(config, 'paths.run_root')}/{run_dir.name}/packets/{name}.json"
    packet = {
        "run_id": run_dir.name,
        "agent_role": agent_role,
        "goal": goal,
        "non_goals": non_goals or ["no scope expansion", "no tracker writes", "no requirement invention"],
        "requirement_ids": requirement_ids,
        "requirement_text": requirement_text_for(index, requirement_ids),
        "base_commit": git_head(repo_root(config)),
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "commands": commands,
        "budgets": per_task_budgets(config),
        "output_kind": output_kind,
        "attempt": attempt,
        "packet_path": packet_rel,
        "capability_manifest": capability_manifest(config),
    }
    if mode:
        packet["mode"] = mode
        # Auditors and red-teams need the rubric; never leave them to invent one (F15).
        pack = Path(cfg(config, "paths.toolkit", "Docs/swarm-audit/toolkit")) / "modes" / mode / "pack.md"
        packet.setdefault("mode_pack", str(pack))
    if task_id:
        packet["task_id"] = task_id
    for key, value in (extra or {}).items():
        packet[key] = value
    errors = validate("packet", packet)
    if errors:
        raise ValueError(f"packet invalid: {', '.join(errors)}")
    out = run_dir / "packets" / f"{name}.json"
    dump_json(out, packet)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--requirement-ids", default="")
    parser.add_argument("--allowed-paths", default="")
    parser.add_argument("--forbidden-paths", default="")
    parser.add_argument("--commands", default="")
    parser.add_argument("--output-kind", required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--extra-json", default=None, help="path to JSON with extra packet fields")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL packet_build: CURRENT missing")
        return 1
    extra = json.loads(Path(args.extra_json).read_text(encoding="utf-8")) if args.extra_json else None
    try:
        out = build_packet(
            config,
            run_dir,
            agent_role=args.role,
            goal=args.goal,
            non_goals=[],
            requirement_ids=[x for x in args.requirement_ids.split(",") if x],
            allowed_paths=[x for x in args.allowed_paths.split(",") if x],
            forbidden_paths=[x for x in args.forbidden_paths.split(",") if x],
            commands=[x for x in args.commands.split(";") if x],
            output_kind=args.output_kind,
            name=args.name,
            mode=args.mode,
            task_id=args.task_id,
            attempt=args.attempt,
            extra=extra,
        )
    except ValueError as exc:
        print(f"FAIL packet_build: {exc}")
        return 1
    print(f"packet: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
