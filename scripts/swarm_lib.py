#!/usr/bin/env python3
"""Shared config loader, paths, enums, and helpers for swarm-audit machinery.

All constants come from swarm.config.json at the target repo root. Nothing
project-specific is hardcoded here. Scripts are stdlib-only.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
CONFIG_NAME = "swarm.config.json"

PACKET_RE = re.compile(r"packet_path:\s*(\S+)", re.IGNORECASE)

AGENT_ROLES = {
    "swarm-auditor",
    "swarm-architect",
    "swarm-owner",
    "swarm-planner",
    "swarm-red-team",
    "swarm-implementer",
    "swarm-ui-red-team",
    "swarm-solution-architect",
}
WRITER_ROLES = {"swarm-implementer"}

PHASES = [
    "init",
    "intake",
    "partition",
    "audit",
    "dedupe",
    "decompose",
    "record",
    "wave",
    "course",
    "verify",
    "integrate",
    "cleanup",
    "close",
]
PHASE_SET = set(PHASES) | {"blocked"}

TASK_STATES = [
    "pending",
    "coursing",
    "collected",
    "admitted",
    "remediated",
    "gated",
    "delta_passed",
    "integrated",
    "closed",
    "blocked",
    "replan_required",
]

VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "REVISE", "FAIL", "NEEDS_EVIDENCE"}
DELTA_VERDICTS = {"PASS", "FAIL", "NEEDS_EVIDENCE"}
ADMISSION_VERDICTS = {"APPROVED_IN_ENVELOPE", "REPLAN_REQUIRED", "INVALID", "NEEDS_EVIDENCE"}
EVIDENCE_LEVELS = {"observed", "static-proof", "plausible", "speculative"}
UI_DISPOSITIONS = {"APPLICABLE", "NOT_APPLICABLE", "BLOCKED_INFRASTRUCTURE"}
COURSE_STATUSES = {
    "complete",
    "blocked",
    "not_reproducible",
    "loop_tripped",
    "missing_capability",
}
MODES = {"faithfulness", "ui-ux", "code-quality", "architecture", "security"}

RED_TEAM_MODES = {"plan", "implementation", "admission", "delta"}

_CONFIG_CACHE: dict[str, tuple[dict, Path]] = {}


class ConfigError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from start (or SWARM_TARGET / cwd) until swarm.config.json is found."""
    env = os.environ.get("SWARM_TARGET")
    cursor = Path(env).resolve() if env else (start or Path.cwd()).resolve()
    for candidate in [cursor, *cursor.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    raise ConfigError(f"{CONFIG_NAME} not found from {cursor} upward; set SWARM_TARGET or run from the target repo")


def _required_keys_from_schema(schema: dict, obj: dict, path: str = "") -> list[str]:
    """Minimal structural check: required keys present; unknown keys rejected
    wherever the schema declares additionalProperties: false."""
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in obj:
            errors.append(f"missing:{path}{key}")
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False and props:
        for key in obj:
            if key not in props and not key.startswith("_"):
                errors.append(f"unknown:{path}{key}")
    for key, sub in props.items():
        if sub.get("type") == "object" and isinstance(obj.get(key), dict):
            errors.extend(_required_keys_from_schema(sub, obj[key], f"{path}{key}."))
    return errors


def load_config(repo_root: Path | None = None) -> dict:
    root = repo_root or find_repo_root()
    key = str(root)
    if key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key][0]
    cfg_path = root / CONFIG_NAME
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    schema_path = SCRIPTS.parent / "schemas" / "config.schema.json"
    if not schema_path.exists():
        schema_path = SCRIPTS.parent / "swarm.config.schema.json"
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = _required_keys_from_schema(schema, config)
        if errors:
            raise ConfigError(f"config invalid: {', '.join(errors)}")
    config["_root"] = str(root)
    _CONFIG_CACHE[key] = (config, root)
    return config


def repo_root(config: dict) -> Path:
    return Path(config["_root"])


def cfg(config: dict, dotted: str, default=None):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def run_root(config: dict) -> Path:
    return repo_root(config) / cfg(config, "paths.run_root", "Docs/swarm-audit/runs")


def plans_root(config: dict) -> Path:
    return repo_root(config) / cfg(config, "paths.plans_root", "Docs/swarm-audit/plans")


def registries_root(config: dict) -> Path:
    return repo_root(config) / cfg(config, "paths.registries", "Docs/swarm-audit/registries")


def current_pointer(config: dict) -> Path:
    return run_root(config) / "CURRENT"


def current_run_dir(config: dict) -> Path | None:
    pointer = current_pointer(config)
    if not pointer.exists():
        return None
    name = pointer.read_text(encoding="utf-8").strip()
    if not name or name.startswith("_"):
        return None
    candidate = run_root(config) / name
    return candidate if candidate.is_dir() else None


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def extract_packet_path(task: str) -> str | None:
    match = PACKET_RE.search(task or "")
    if not match:
        return None
    raw = match.group(1).strip().strip("'\"")
    return raw or None


def is_pipeline(sub_type: str) -> bool:
    return any(name in (sub_type or "") for name in AGENT_ROLES)


def is_writer(sub_type: str) -> bool:
    return any(name in (sub_type or "") for name in WRITER_ROLES)


def norm_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def id_patterns(config: dict) -> list[re.Pattern]:
    patterns = cfg(config, "requirements.id_patterns", []) or []
    return [re.compile(rf"\b({p})\b") for p in patterns]


def layers_from_paths(config: dict, paths: list[str]) -> list[str]:
    layer_map: dict = cfg(config, "architecture.layer_map", {}) or {}
    found: list[str] = []
    for path in paths:
        p = norm_path(path)
        for layer, globs in layer_map.items():
            for pattern in globs:
                if fnmatch.fnmatch(p, norm_path(pattern)) or p.startswith(norm_path(pattern).rstrip("*/")):
                    if layer not in found:
                        found.append(layer)
                    break
    return found


def shared_conflict_hits(config: dict, paths: list[str]) -> list[str]:
    tokens = [norm_path(t) for t in (cfg(config, "architecture.shared_conflict_paths", []) or [])]
    hits: list[str] = []
    for path in paths:
        p = norm_path(path)
        for token in tokens:
            if (p == token or p.startswith(token)) and token not in hits:
                hits.append(token)
    return hits


def path_in_envelope(path: str, allowed: list[str]) -> bool:
    p = norm_path(path)
    for entry in allowed:
        a = norm_path(entry)
        if p == a or p.startswith(a.rstrip("*/").rstrip("/") + "/") or fnmatch.fnmatch(p, a):
            return True
    return False


def caps(config: dict) -> dict:
    c = cfg(config, "caps", {}) or {}
    return {
        "max_live_agents": c.get("max_live_agents", 8),
        "max_writers": c.get("max_writers", 2),
        "max_worktrees": c.get("max_worktrees", 10),
        "plan_revise_rounds": c.get("plan_revise_rounds", 2),
        "remediation_batches": c.get("remediation_batches", 1),
        "decomposition": {
            "depth": cfg(config, "caps.decomposition.depth", 2),
            "fanout": cfg(config, "caps.decomposition.fanout", 4),
            "global": cfg(config, "caps.decomposition.global", 30),
            "branch": cfg(config, "caps.decomposition.branch", 4),
        },
        "atomic": {
            "max_requirements": cfg(config, "caps.atomic_thresholds.max_requirements", 3),
            "max_layers": cfg(config, "caps.atomic_thresholds.max_layers", 2),
            "max_shared": cfg(config, "caps.atomic_thresholds.max_shared", 1),
            "max_migrations": cfg(config, "caps.atomic_thresholds.max_migrations", 1),
            "max_acs": cfg(config, "caps.atomic_thresholds.max_acs", 8),
            "max_security": cfg(config, "caps.atomic_thresholds.max_security", 1),
        },
    }


def per_task_budgets(config: dict) -> dict:
    b = cfg(config, "budgets.per_task", {}) or {}
    return {
        "owner_attempts": b.get("owner_attempts", 2),
        "evidence_refreshes": b.get("evidence_refreshes", 2),
        "architect_consults": b.get("architect_consults", 2),
        "replans": b.get("replans", 1),
    }
