#!/usr/bin/env python3
"""Swarm-audit uninstaller.

Removes only files whose current hash matches install-manifest.json (user-modified
files are kept and reported). Removes swarm-managed hook commands from hooks.json.
Refuses while a run is live unless --force. Run folders and plans are never removed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_is_live(target: Path, config: dict) -> bool:
    run_root = target / (config.get("paths") or {}).get("run_root", "Docs/swarm-audit/runs")
    pointer = run_root / "CURRENT"
    if not pointer.exists():
        return False
    name = pointer.read_text(encoding="utf-8").strip()
    state_path = run_root / name / "state.json"
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return state.get("status") == "running"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    target = Path(args.target).resolve()
    manifest_path = target / "install-manifest.json"
    if not manifest_path.exists():
        print("FAIL uninstall: install-manifest.json missing")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    config = {}
    config_path = target / "swarm.config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    if run_is_live(target, config) and not args.force:
        print("FAIL uninstall: a swarm run is live. Close it or pass --force.")
        return 1

    removed, kept = [], []
    for rel, expected in sorted(manifest.get("files", {}).items()):
        path = target / rel
        if not path.exists():
            continue
        if sha256_file(path) == expected:
            path.unlink()
            removed.append(rel)
        else:
            kept.append(rel)

    hooks_json_path = target / ".cursor" / "hooks.json"
    managed = set(manifest.get("hooks_json_managed_commands") or [])
    if hooks_json_path.exists() and managed:
        hooks_json = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        hooks = hooks_json.get("hooks") or {}
        for event in list(hooks):
            value = hooks[event]
            entries = [{"command": value}] if isinstance(value, str) else (
                [value] if isinstance(value, dict) else list(value or [])
            )
            hooks[event] = [
                e for e in entries if not (isinstance(e, dict) and e.get("command") in managed)
            ]
            if not hooks[event]:
                del hooks[event]
        hooks_json.pop("swarm_audit_version", None)
        if hooks or {k: v for k, v in hooks_json.items() if k != "hooks"}:
            hooks_json["hooks"] = hooks
            hooks_json_path.write_text(json.dumps(hooks_json, indent=2) + "\n", encoding="utf-8")
        else:
            hooks_json_path.unlink()

    # prune empty dirs we own
    for owned in (target / ".cursor" / "hooks" / "swarm", target / ".agents" / "skills"):
        if owned.exists() and not any(owned.rglob("*")):
            owned.rmdir()

    manifest_path.unlink()
    print(f"uninstalled: removed {len(removed)} files; kept {len(kept)} user-modified files: {kept[:10]}")
    print("note: swarm.config.json, run folders, and plans were intentionally left in place")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
