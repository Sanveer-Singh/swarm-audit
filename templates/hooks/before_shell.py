#!/usr/bin/env python3
"""preToolUse Shell guard.

Global rules (caller identity is not exposed to hooks, D21):
- Mutating tracker CLI commands are allowed only from the main checkout, never
  from worktrees (sole-writer invariant approximated by location).
- Destructive git (push --force to main/master, reset --hard, clean -f on the
  main checkout) is denied while a swarm run is live.
Non-configured repos: everything allowed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_common import ROOT, allow, deny, load_stdin, run_dir_or_none, swarm_config, toolkit_available  # noqa: E402

GIT_DESTRUCTIVE = re.compile(
    r"\bgit\s+(?:push\s+[^\n]*--force(?!-with-lease)|reset\s+--hard|clean\s+-[a-z]*f)",
    re.IGNORECASE,
)
# Only the orchestrator integrates (D16): worktrees never publish or rewrite history.
GIT_WORKTREE_FORBIDDEN = re.compile(
    r"\bgit\s+(?:push|merge|rebase|cherry-pick)\b",
    re.IGNORECASE,
)
HELP_OR_RO = re.compile(r"\s(?:--help|-h|--dry-run|--readonly)\b", re.IGNORECASE)


def tracker_mutation_re(config: dict | None) -> re.Pattern | None:
    if config is None:
        return None
    adapter = config.get("tracker", {}).get("adapter", "none")
    if adapter == "none":
        return None
    commands = config.get("tracker", {}).get("commands", {})
    binaries = {str(tpl).split(" ", 1)[0] for tpl in commands.values() if tpl}
    binaries.discard("")
    if not binaries:
        return None
    names = "|".join(re.escape(b) for b in sorted(binaries))
    return re.compile(
        rf"\b(?:{names})(?:\.exe)?\s+(?:create|update|close|dep|comment|comments|reopen|delete|undelete|restore|sync|claim|dolt)\b",
        re.IGNORECASE,
    )


def main() -> int:
    payload = load_stdin(fail_closed=False)
    if not toolkit_available() or ROOT is None:
        allow()
        return 0
    command = str(payload.get("command") or payload.get("tool_input", {}).get("command") or "")
    cwd = Path(payload.get("cwd") or ROOT).resolve()
    try:
        in_main = cwd == ROOT or ROOT in cwd.parents
    except OSError:
        in_main = False
    in_worktree = "worktrees" in cwd.parts or ".swarm-audit" in cwd.parts

    config = swarm_config()
    tracker_re = tracker_mutation_re(config)
    if tracker_re and tracker_re.search(command) and not HELP_OR_RO.search(command):
        if in_worktree or not in_main:
            deny("tracker writes are orchestrator-only from the main checkout. Return a payload instead.")
            return 0

    run_live = run_dir_or_none() is not None
    if run_live and in_main and GIT_DESTRUCTIVE.search(command):
        deny("destructive git on the main checkout is denied while a swarm run is live.")
        return 0

    if run_live and in_worktree and GIT_WORKTREE_FORBIDDEN.search(command):
        deny("git push/merge/rebase/cherry-pick from a swarm worktree is denied — the orchestrator is the only integrator (D16).")
        return 0

    allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
