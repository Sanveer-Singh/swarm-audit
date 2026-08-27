#!/usr/bin/env python3
"""Preflight: verify the environment before init (and after install).

Checks: config valid, git present + clean-enough tree, python version, gate
commands exist (dry parse), tracker CLI reachable when adapter != none,
requirement roots exist in documented mode, hooks merged, worst-case admission
fits, schema registry parses.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from admission import worst_case_occupancy
from swarm_lib import cfg, dump_json, load_config, now_iso, repo_root
from validate_payload import REGISTRY, registry_kinds


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_checks(config: dict, install_check: bool) -> list[dict]:
    root = repo_root(config)
    checks: list[dict] = []

    checks.append(check("python_version", sys.version_info >= (3, 10), sys.version.split()[0]))
    checks.append(check("git_available", shutil.which("git") is not None))

    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, capture_output=True, text=True, check=False)
    checks.append(check("git_repo", proc.returncode == 0))

    mode = cfg(config, "requirements.mode", "documented")
    if mode == "documented":
        missing = [r for r in cfg(config, "requirements.roots", []) or [] if not (root / r).exists()]
        checks.append(check("requirement_roots_exist", not missing, str(missing)))

    gate_cmds = cfg(config, "gates.commands", []) or []
    checks.append(check("gates_configured", len(gate_cmds) > 0, f"{len(gate_cmds)} gates"))
    for gate in gate_cmds:
        binary = str(gate.get("cmd", "")).split(" ", 1)[0]
        checks.append(check(f"gate_binary:{gate.get('id')}", shutil.which(binary) is not None, binary))

    adapter = cfg(config, "tracker.adapter", "none")
    if adapter != "none":
        create_tpl = cfg(config, "tracker.commands.create", "")
        binary = create_tpl.split(" ", 1)[0] if create_tpl else ""
        checks.append(check("tracker_cli", bool(binary) and shutil.which(binary) is not None, binary))

    occ = worst_case_occupancy(config)
    checks.append(check("admission_worst_case_fits", occ["fits"], f"live={occ['live']}/{occ['max_live']} writers={occ['writers']}/{occ['max_writers']}"))

    checks.append(check("schema_registry", len(registry_kinds()) >= 20, f"{len(registry_kinds())} kinds"))

    hooks_json = root / ".cursor" / "hooks.json"
    hooks_ok = hooks_json.exists() and "swarm" in hooks_json.read_text(encoding="utf-8", errors="replace").lower()
    # Honest naming (F04): this proves the JSON is merged, NOT that Cursor delivers
    # stdin to the hooks. Live hook-fire is proven by the canary spawn at run start.
    checks.append(check("hooks_json_present", hooks_ok, f"{hooks_json} (live hook-fire proven only by canary spawn)"))

    agents_dir = root / ".cursor" / "agents"
    expected_agents = [
        "swarm-auditor", "swarm-architect", "swarm-owner", "swarm-planner",
        "swarm-red-team", "swarm-implementer", "swarm-ui-red-team", "swarm-solution-architect",
    ]
    missing_agents = [a for a in expected_agents if not (agents_dir / f"{a}.md").exists()]
    checks.append(check("agent_templates_installed", not missing_agents, str(missing_agents)))

    if "ui-ux" in (cfg(config, "modes.default", []) or []):
        browser = bool(cfg(config, "mcp.browser", False))
        checks.append(check("ui_mode_requires_browser", browser, "ui-ux mode needs mcp.browser=true (or drop ui-ux from modes.default)"))

    if install_check:
        manifest = root / "install-manifest.json"
        checks.append(check("install_manifest", manifest.exists(), str(manifest)))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-check", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    try:
        config = load_config()
        config_valid = True
        config_detail = ""
    except Exception as exc:  # noqa: BLE001 - preflight reports, never crashes
        print(json.dumps({"passed": False, "config_valid": False, "error": str(exc)}, indent=2))
        return 1
    checks = run_checks(config, args.install_check)
    passed = all(c["ok"] for c in checks)
    receipt = {
        "at": now_iso(),
        "config_valid": config_valid,
        "checks": checks,
        "passed": passed,
        "install_check": args.install_check,
    }
    if args.out:
        dump_json(Path(args.out), receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
