#!/usr/bin/env python3
"""beforeMCPExecution guard. Global rules because caller identity is hidden (D21).

While a swarm run is live:
- Design-tool writes (Figma) and code-quality-platform mutations (Sonar status
  changes) are denied outright — the swarm reads them, never mutates them.
Browser tools are NOT gated here: guessing the caller's role from the registry
violates D21 (rules must be caller-independent). Browser confinement lives in
agent frontmatter/skills instead (F10).
No live run: everything allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_common import allow, deny, load_stdin, run_dir_or_none, toolkit_available  # noqa: E402

FIGMA_WRITE = {
    "use_figma",
    "create_new_file",
    "generate_figma_design",
    "generate_diagram",
    "add_code_connect_map",
    "send_code_connect_mappings",
    "upload_assets",
    "weave_run_tool",
    "weave_upload_asset",
}
SONAR_MUTATE = {"change_security_hotspot_status", "change_sonar_issue_status", "toggle_automatic_analysis"}


def _tool_name(payload: dict) -> str:
    for key in ("tool_name", "toolName", "tool"):
        if payload.get(key):
            return str(payload[key]).split("/")[-1]
    return ""


def main() -> int:
    payload = load_stdin(fail_closed=False)
    if not toolkit_available():
        allow()
        return 0
    run_dir = run_dir_or_none()
    if run_dir is None:
        allow()
        return 0
    name = _tool_name(payload)
    if name in FIGMA_WRITE or name in SONAR_MUTATE:
        deny(f"MCP tool {name} is denied while a swarm run is live (write/mutate on external system).")
        return 0
    allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
