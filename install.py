#!/usr/bin/env python3
"""Transactional swarm-audit installer.

Copies agents, skills, hooks, and the toolkit into a target repo; three-way
merges the hook fragment into .cursor/hooks.json; injects pinned models
from config into agent templates; records everything in install-manifest.json;
runs contract tests + preflight; rolls back completely on any failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent

ROLE_MODEL_KEY = {
    "swarm-auditor": "auditor",
    "swarm-architect": "architect",
    "swarm-owner": "owner",
    "swarm-planner": "critic",
    "swarm-red-team": "critic",
    "swarm-ui-red-team": "critic",
    "swarm-solution-architect": "solution_architect",
    "swarm-implementer": "implementer",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Transaction:
    """Records created files and backups so any failure can be fully undone."""

    def __init__(self, target: Path):
        self.target = target
        self.created: list[Path] = []
        self.backups: list[tuple[Path, Path]] = []  # (original, backup_copy)
        self.backup_dir = target / ".swarm-audit-backup" / time.strftime("%Y%m%d-%H%M%S")

    def write(self, dest: Path, content: bytes) -> None:
        if dest.exists():
            backup = self.backup_dir / dest.relative_to(self.target)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
            self.backups.append((dest, backup))
        else:
            self.created.append(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def rollback(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)
        for original, backup in reversed(self.backups):
            shutil.copy2(backup, original)

    def cleanup(self) -> None:
        if self.backup_dir.exists():
            shutil.rmtree(self.backup_dir, ignore_errors=True)


def inject_model(text: str, agent_name: str, config: dict) -> str:
    key = ROLE_MODEL_KEY.get(agent_name)
    model = (config.get("models") or {}).get(key) if key else None
    if not model or model == "inherit":
        # Cursor treats a missing model line as inherit; drop the placeholder.
        return text.replace("model: inherit\n", "")
    return text.replace("model: inherit", f"model: {model}")


def _normalize_entries(value) -> list:
    """Cursor accepts a bare string or dict for a hook event; normalize to a list
    without corrupting it (F14: list('cmd') explodes a string into characters)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [{"command": value}]
    if isinstance(value, dict):
        return [value]
    return list(value)


def merge_hooks_json(existing: dict, fragment: dict) -> dict:
    merged = dict(existing)
    merged["swarm_audit_version"] = fragment.get("swarm_audit_version", 1)
    hooks = dict(merged.get("hooks") or {})
    for event, entries in (fragment.get("hooks") or {}).items():
        current = _normalize_entries(hooks.get(event))
        for entry in entries:
            if not any(e.get("command") == entry.get("command") for e in current if isinstance(e, dict)):
                current.append(entry)
        hooks[event] = current
    merged["hooks"] = hooks
    return merged


def run_is_live(target: Path) -> bool:
    config_path = target / "swarm.config.json"
    if not config_path.exists():
        return False
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    run_root = target / (config.get("paths") or {}).get("run_root", "Docs/swarm-audit/runs")
    pointer = run_root / "CURRENT"
    if not pointer.exists():
        return False
    state_path = run_root / pointer.read_text(encoding="utf-8").strip() / "state.json"
    if not state_path.exists():
        return False
    try:
        return json.loads(state_path.read_text(encoding="utf-8")).get("status") == "running"
    except json.JSONDecodeError:
        return False


def install(target: Path, *, skip_checks: bool) -> int:
    if not (target / ".git").exists():
        print(f"FAIL install: {target} is not a git repo root")
        return 1
    if run_is_live(target):
        print("FAIL install: a swarm run is live in the target. Close or finish it before upgrading.")
        return 1

    # Install lock (D26/F14): O_EXCL so two concurrent installs cannot interleave.
    lock_path = target / ".swarm-audit-install.lock"
    try:
        lock_fd = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        print(f"FAIL install: {lock_path} exists — another install is in progress (or crashed; delete the lock if so).")
        return 1
    try:
        lock_fd.write(f"pid={time.time()}\n")
        lock_fd.close()
        return _install_locked(target, skip_checks=skip_checks)
    finally:
        lock_path.unlink(missing_ok=True)


def _install_locked(target: Path, *, skip_checks: bool) -> int:
    tx = Transaction(target)
    manifest: dict = {
        "version": 1,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(SOURCE),
        "files": {},
        "hooks_json_managed_commands": [],
    }
    try:
        # 1. Config (from example if absent)
        config_path = target / "swarm.config.json"
        if not config_path.exists():
            tx.write(config_path, (SOURCE / "swarm.config.example.json").read_bytes())
            print("wrote swarm.config.json from example — REVIEW IT before running an audit")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        toolkit_rel = (config.get("paths") or {}).get("toolkit", "Docs/swarm-audit/toolkit")

        def record(dest: Path) -> None:
            manifest["files"][str(dest.relative_to(target)).replace("\\", "/")] = sha256_file(dest)

        # 2. Toolkit: scripts, schemas, docs, modes
        for sub in ("scripts", "schemas", "docs", "modes"):
            src_dir = SOURCE / sub
            if not src_dir.exists():
                continue
            for src in sorted(src_dir.rglob("*")):
                if src.is_file() and "__pycache__" not in src.parts:
                    dest = target / toolkit_rel / sub / src.relative_to(src_dir)
                    tx.write(dest, src.read_bytes())
                    record(dest)

        # 3. Agents (model injection)
        for src in sorted((SOURCE / "templates" / "agents").glob("*.md")):
            text = inject_model(src.read_text(encoding="utf-8"), src.stem, config)
            dest = target / ".cursor" / "agents" / src.name
            tx.write(dest, text.encode("utf-8"))
            record(dest)

        # 4. Skills
        for src in sorted((SOURCE / "templates" / "skills").rglob("SKILL.md")):
            dest = target / ".agents" / "skills" / src.parent.name / "SKILL.md"
            tx.write(dest, src.read_bytes())
            record(dest)

        # 5. Hooks
        for src in sorted((SOURCE / "templates" / "hooks").glob("*.py")):
            dest = target / ".cursor" / "hooks" / "swarm" / src.name
            tx.write(dest, src.read_bytes())
            record(dest)

        # 6. hooks.json three-way merge — Cursor reads .cursor/hooks.json, NOT
        # .cursor/hooks/hooks.json (smoke finding: wrong path = hooks never fire).
        fragment = json.loads((SOURCE / "templates" / "hooks" / "hooks.fragment.json").read_text(encoding="utf-8"))
        hooks_json_path = target / ".cursor" / "hooks.json"
        existing = {}
        if hooks_json_path.exists():
            existing = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        merged = merge_hooks_json(existing, fragment)
        tx.write(hooks_json_path, (json.dumps(merged, indent=2) + "\n").encode("utf-8"))
        manifest["hooks_json_managed_commands"] = [
            entry["command"]
            for entries in (fragment.get("hooks") or {}).values()
            for entry in entries
        ]

        # 7. Manifest (written inside the transaction so rollback removes it)
        manifest_path = target / "install-manifest.json"
        tx.write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))

        # 8. Post-install checks
        if not skip_checks:
            scripts_dir = target / toolkit_rel / "scripts"
            contract = scripts_dir / "contract_tests.py"
            if contract.exists():
                proc = subprocess.run([sys.executable, str(contract)], cwd=target, capture_output=True, text=True, timeout=600)
                if proc.returncode != 0:
                    raise RuntimeError(f"contract_tests failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}")
            proc = subprocess.run(
                [sys.executable, str(scripts_dir / "preflight.py"), "--install-check"],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"preflight failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}")

    except Exception as exc:  # noqa: BLE001 - any failure rolls back
        tx.rollback()
        print(f"FAIL install (rolled back): {exc}")
        return 1

    tx.cleanup()
    print(f"installed swarm-audit into {target} ({len(manifest['files'])} files + hooks.json). Next: review swarm.config.json, then load @swarm-orchestrator.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--skip-checks", action="store_true", help="skip post-install contract tests and preflight")
    args = parser.parse_args()
    return install(Path(args.target).resolve(), skip_checks=args.skip_checks)


if __name__ == "__main__":
    raise SystemExit(main())
