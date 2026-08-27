#!/usr/bin/env python3
"""Worktree lifecycle: create (with env/port/db seeding), merge, remove, quarantine.

All operations are journaled by the caller. Quarantine (F09) renames a corrupt
worktree aside and frees its port/db instead of deleting evidence.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
from pathlib import Path

from admission import file_lock
from swarm_lib import cfg, current_run_dir, dump_json, load_config, load_json, now_iso, repo_root


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)


def worktrees_dir(config: dict) -> Path:
    return Path.home() / ".swarm-audit" / "worktrees" / cfg(config, "project.name", "project")


def allocations_path(run_dir: Path) -> Path:
    return run_dir / "worktree-allocations.json"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def global_ports_path() -> Path:
    return Path.home() / ".swarm-audit" / "ports.json"


def allocate_port(config: dict, run_dir: Path, task_id: str) -> int:
    """Bind-probed, cross-run allocation under a file lock (F07)."""
    low, high = cfg(config, "worktree.port_range", [5600, 5999])
    gpath = global_ports_path()
    with file_lock(gpath.with_suffix(".lock")):
        registry = load_json(gpath) if gpath.exists() else {}
        used = {int(p) for p in registry}
        chosen = None
        for port in range(low, high + 1):
            if port in used:
                continue
            if _port_free(port):
                chosen = port
                break
        if chosen is None:
            raise RuntimeError(f"port_exhausted:{low}-{high}")
        registry[str(chosen)] = {"run": run_dir.name, "task": task_id, "at": now_iso()}
        dump_json(gpath, registry)
    allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
    allocs[task_id] = {**allocs.get(task_id, {}), "port": chosen}
    dump_json(allocations_path(run_dir), allocs)
    return chosen


def release_port(run_dir: Path, task_id: str) -> None:
    gpath = global_ports_path()
    if not gpath.exists():
        return
    with file_lock(gpath.with_suffix(".lock")):
        registry = load_json(gpath)
        registry = {
            p: meta for p, meta in registry.items()
            if not (meta.get("run") == run_dir.name and meta.get("task") == task_id)
        }
        dump_json(gpath, registry)


def create(config: dict, run_dir: Path, task_id: str, base_commit: str) -> dict:
    root = repo_root(config)
    allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
    active = [a for a in allocs.values() if a.get("path") and not a.get("quarantined")]
    max_wt = cfg(config, "caps.max_worktrees", 10)
    if len(active) >= max_wt:
        return {"ok": False, "reason": f"max_worktrees:{max_wt}"}
    branch = f"swarm/{run_dir.name}/{task_id}"
    path = worktrees_dir(config) / f"{run_dir.name}-{task_id}"
    existing = allocs.get(task_id)
    if path.exists():
        if existing and existing.get("path") == str(path):
            # crash-safe idempotent create (F06): return the existing allocation
            return {"ok": True, "path": str(path), "branch": existing.get("branch", branch),
                    "port": existing.get("port"), "existing": True}
        return {"ok": False, "reason": "worktree_exists_unallocated", "path": str(path)}
    result = git(["worktree", "add", "-b", branch, str(path), base_commit], root)
    if result.returncode != 0:
        return {"ok": False, "reason": result.stderr[-500:], "path": str(path)}
    port = allocate_port(config, run_dir, task_id)
    try:
        env_lines = [f"SWARM_TASK={task_id}", f"SWARM_PORT={port}"]
        db_template = cfg(config, "worktree.db_template")
        if db_template and (root / db_template).exists():
            db_dest = path / Path(db_template).name
            shutil.copy2(root / db_template, db_dest)
            env_lines.append(f"SWARM_DB={db_dest.name}")
        env_file = cfg(config, "worktree.env_file", ".worktree-env")
        (path / env_file).write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        setup = cfg(config, "worktree.setup_script")
        setup_result = None
        if setup:
            proc = subprocess.run(setup, shell=True, cwd=path, text=True, capture_output=True, check=False, timeout=600)
            setup_result = {"exit_code": proc.returncode, "tail": (proc.stdout + proc.stderr)[-800:]}
        allocs = load_json(allocations_path(run_dir))
        allocs[task_id] = {**allocs.get(task_id, {}), "path": str(path), "branch": branch, "created": now_iso()}
        dump_json(allocations_path(run_dir), allocs)
    except Exception:
        # Ports live in a GLOBAL registry: any failure after allocation must free
        # the port or it leaks across runs (delta F07).
        release_port(run_dir, task_id)
        raise
    return {"ok": True, "path": str(path), "branch": branch, "port": port, "setup": setup_result}


def merge(config: dict, run_dir: Path, task_id: str) -> dict:
    root = repo_root(config)
    allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
    alloc = allocs.get(task_id)
    if not alloc:
        return {"ok": False, "reason": "no_allocation"}
    wt = Path(alloc["path"])
    # New files (tests-first!) must be staged too — `commit -am` misses untracked (F05).
    # Swarm-seeded runtime files (env file, db copy) must never reach the merge.
    env_file = cfg(config, "worktree.env_file", ".worktree-env")
    seeded = {env_file}
    db_template = cfg(config, "worktree.db_template")
    if db_template:
        seeded.add(Path(db_template).name)
    excludes = [f":!{name}" for name in seeded]
    # Build artifacts are never legitimate course output (mirrors course_verify).
    excludes += [":!*.pyc", ":!*.pyo", ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)__pycache__/**"]
    git(["add", "-A", "--", ".", *excludes], wt)
    commit = git(["commit", "-m", f"swarm-audit {run_dir.name} {task_id}"], wt)

    def _benign(rel: str) -> bool:
        parts = rel.replace("\\", "/").split("/")
        return Path(rel).name in seeded or "__pycache__" in parts or rel.endswith((".pyc", ".pyo"))

    status = git(["status", "--porcelain"], wt)
    leftover = [
        line for line in status.stdout.splitlines()
        if line.strip() and not _benign(line[3:].strip().strip('"').rstrip("/"))
    ]
    if leftover:
        return {"ok": False, "reason": f"uncommitted_after_commit:{leftover[:5]}"}
    result = git(["merge", "--no-ff", alloc["branch"], "-m", f"integrate {task_id} ({run_dir.name})"], root)
    if result.returncode != 0:
        git(["merge", "--abort"], root)
        return {"ok": False, "reason": "merge_conflict", "detail": result.stdout[-500:] + result.stderr[-500:]}
    head = git(["rev-parse", "HEAD"], root).stdout.strip()
    return {"ok": True, "merge_commit": head, "commit_output": commit.stdout[-300:]}


def remove(config: dict, run_dir: Path, task_id: str) -> dict:
    root = repo_root(config)
    allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
    alloc = allocs.get(task_id)
    if not alloc:
        return {"ok": False, "reason": "no_allocation"}
    result = git(["worktree", "remove", "--force", alloc["path"]], root)
    git(["branch", "-D", alloc["branch"]], root)
    release_port(run_dir, task_id)
    allocs.pop(task_id, None)
    dump_json(allocations_path(run_dir), allocs)
    return {"ok": result.returncode == 0, "reason": result.stderr[-300:] if result.returncode else None}


def quarantine(config: dict, run_dir: Path, task_id: str) -> dict:
    """Rename a corrupt/suspect worktree aside; free port and allocation; keep evidence."""
    allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
    alloc = allocs.get(task_id)
    if not alloc:
        return {"ok": False, "reason": "no_allocation"}
    src = Path(alloc["path"])
    dest = src.with_name(src.name + ".quarantine")
    try:
        src.rename(dest)
    except OSError as exc:
        return {"ok": False, "reason": f"rename_failed:{exc}"}
    git(["worktree", "prune"], repo_root(config))
    release_port(run_dir, task_id)
    allocs[task_id] = {"quarantined": str(dest), "at": now_iso()}
    dump_json(allocations_path(run_dir), allocs)
    return {"ok": True, "quarantined": str(dest)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["create", "merge", "remove", "quarantine", "list"])
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--base-commit", default="HEAD")
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL worktree: CURRENT missing")
        return 1
    if args.cmd == "list":
        allocs = load_json(allocations_path(run_dir)) if allocations_path(run_dir).exists() else {}
        print(json.dumps(allocs, indent=2))
        return 0
    if not args.task_id:
        print("FAIL worktree: --task-id required")
        return 1
    fn = {"create": create, "merge": merge, "remove": remove, "quarantine": quarantine}[args.cmd]
    if args.cmd == "create":
        result = create(config, run_dir, args.task_id, args.base_commit)
    else:
        result = fn(config, run_dir, args.task_id)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
