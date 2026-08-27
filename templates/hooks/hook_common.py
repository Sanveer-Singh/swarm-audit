#!/usr/bin/env python3
"""Shared swarm-audit hook helpers.

Discovery: walk up from this file until swarm.config.json is found, then import
the toolkit scripts from paths.toolkit. Hooks must never crash Cursor: when the
repo has no swarm config, everything is allowed EXCEPT swarm-* pipeline spawns,
which are denied (fail-closed applies to governed traffic only).

Correlation contract (D20): packet_path echoed in the task text is the primary
identity; subagent_id is optional enrichment.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS = Path(__file__).resolve().parent


def _find_root() -> Path | None:
    for candidate in [HOOKS, *HOOKS.parents]:
        if (candidate / "swarm.config.json").is_file():
            return candidate
    return None


ROOT = _find_root()
TOOLKIT_SCRIPTS: Path | None = None
if ROOT is not None:
    try:
        _config = json.loads((ROOT / "swarm.config.json").read_text(encoding="utf-8"))
        _toolkit = _config.get("paths", {}).get("toolkit", "Docs/swarm-audit/toolkit")
        for candidate in (ROOT / _toolkit / "scripts", ROOT / "swarm-audit" / "scripts"):
            if candidate.is_dir():
                TOOLKIT_SCRIPTS = candidate
                break
    except (json.JSONDecodeError, OSError):
        TOOLKIT_SCRIPTS = None

if TOOLKIT_SCRIPTS is not None:
    sys.path.insert(0, str(TOOLKIT_SCRIPTS))
    os.environ.setdefault("SWARM_TARGET", str(ROOT))


class HookInputError(ValueError):
    """Empty or unparseable hook stdin. Governing hooks deny, never allow."""


NESTED_KEYS = ("input", "hook_input", "tool_input", "payload")
IDENTITY_KEYS = ("subagent_type", "subagent_id", "task", "subagent_model")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _looks_like_subagent(obj: dict) -> bool:
    return any(obj.get(key) for key in IDENTITY_KEYS)


def unwrap_hook_payload(obj: dict) -> dict:
    if not isinstance(obj, dict):
        return {}
    for key in NESTED_KEYS:
        nested = obj.get(key)
        if isinstance(nested, dict) and _looks_like_subagent(nested):
            return nested
    return obj


def read_hook_raw() -> str:
    raw = sys.stdin.read()
    if raw and raw.strip():
        return raw
    if len(sys.argv) > 1 and sys.argv[1].lstrip().startswith("{"):
        return sys.argv[1]
    for env_key in ("CURSOR_HOOK_INPUT", "CURSOR_HOOK_PAYLOAD"):
        env = os.environ.get(env_key)
        if env and env.strip():
            return env
    return raw or ""


def load_stdin(*, fail_closed: bool = True) -> dict:
    raw = read_hook_raw()
    if not raw.strip():
        if fail_closed:
            raise HookInputError("empty_stdin")
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        if fail_closed:
            raise HookInputError(f"invalid_json:{exc}") from exc
        return {}
    if not isinstance(obj, dict):
        if fail_closed:
            raise HookInputError("stdin_not_object")
        return {}
    return unwrap_hook_payload(obj)


def toolkit_available() -> bool:
    return TOOLKIT_SCRIPTS is not None


def swarm_config() -> dict | None:
    if ROOT is None:
        return None
    try:
        import swarm_lib

        return swarm_lib.load_config(ROOT)
    except Exception:  # noqa: BLE001 - hooks must not crash
        return None


def run_dir_or_none():
    config = swarm_config()
    if config is None:
        return None
    import swarm_lib

    return swarm_lib.current_run_dir(config)


def append_event(run_dir, event: dict) -> None:
    if run_dir is None:
        return
    log = run_dir / "agents.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def deny(message: str) -> None:
    json.dump({"permission": "deny", "user_message": message}, sys.stdout)
    sys.stdout.write("\n")


def allow() -> None:
    json.dump({"permission": "allow"}, sys.stdout)
    sys.stdout.write("\n")


def expected_model(root: Path, sub_type: str, config: dict | None) -> str | None:
    if config is None:
        return None
    models = config.get("models", {})
    role_key = {
        "swarm-auditor": "auditor",
        "swarm-architect": "architect",
        "swarm-owner": "owner",
        "swarm-planner": "critic",
        "swarm-red-team": "critic",
        "swarm-ui-red-team": "critic",
        "swarm-solution-architect": "solution_architect",
        "swarm-implementer": "implementer",
    }
    for role, key in role_key.items():
        if role in (sub_type or ""):
            value = models.get(key)
            return None if value in (None, "inherit") else value
    return None


def validate_packet_file(root: Path, rel: str) -> list[str]:
    # Stage-spawn convention (F01): a readonly owner cannot write packet files, so
    # stage spawns echo `packet_path: <parent-packet>#<stage>-<attempt>`. The file
    # part (before '#') is validated; the FULL string is the admission/registry key,
    # making every stage spawn a unique slot.
    rel = rel.split("#", 1)[0]
    path = Path(rel)
    if not path.is_absolute():
        path = (root / rel).resolve()
    if not path.is_file():
        return [f"packet_missing:{rel}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"packet_json:{exc}"]
    try:
        from validate_payload import validate

        return validate("packet", data)
    except Exception as exc:  # noqa: BLE001
        return [f"validator_unavailable:{exc}"]
