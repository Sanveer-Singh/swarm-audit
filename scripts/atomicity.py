#!/usr/bin/env python3
"""Deterministic atomicity predicate and decomposition guards.

The model may propose a split. Only this module may authorize it.
Depth is not a progress rank. Scope must shrink. Children must be
pairwise disjoint and exactly conserve parent requirements and findings.
Runs advisory-only (shadow) until per-project calibration flips it to gating.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import caps as load_caps
from swarm_lib import dump_json, layers_from_paths, load_config, norm_path, shared_conflict_hits
from validate_payload import validate

SECURITY_MARKERS = (
    "account", "identity", "auth", "password", "lockout", "rbac",
    "session", "token", "csrf", "upload", "permission",
)


def infer_security_surfaces(paths: list[str], explicit: list[str] | None = None) -> list[str]:
    if explicit:
        return list(explicit)
    surfaces: list[str] = []
    for path in paths:
        low = norm_path(path).lower()
        for marker in SECURITY_MARKERS:
            if marker in low and marker not in surfaces:
                surfaces.append(marker)
    return surfaces


def evaluate_manifest(manifest: dict, config: dict | None = None) -> dict:
    config = config or load_config()
    thresholds = load_caps(config)["atomic"]
    errors = validate("decomposition_manifest", manifest)
    if errors:
        return {
            "cluster_id": manifest.get("cluster_id") or "",
            "atomic": False,
            "failing_checks": errors,
            "eligible_for_decompose": False,
        }
    failing: list[str] = []
    paths = manifest.get("proposed_paths") or []
    reqs = list(manifest.get("requirement_ids") or [])
    layers = list(manifest.get("layers") or layers_from_paths(config, paths))
    shared = list(manifest.get("shared_contract_hits") or shared_conflict_hits(config, paths))
    migrations = int(manifest.get("migration_count") or 0)
    acs = int(manifest.get("ac_count") or 0)
    surfaces = list(manifest.get("security_surfaces") or [])
    if len(reqs) > thresholds["max_requirements"]:
        failing.append("requirement_count")
    if len(layers) > thresholds["max_layers"]:
        failing.append("layer_span")
    if len(shared) > thresholds["max_shared"]:
        failing.append("shared_contract")
    if migrations > thresholds["max_migrations"]:
        failing.append("migration_count")
    if acs > thresholds["max_acs"]:
        failing.append("ac_count")
    if len(surfaces) > thresholds["max_security"]:
        failing.append("security_surfaces")
    return {
        "cluster_id": manifest["cluster_id"],
        "atomic": not failing,
        "failing_checks": failing,
        "eligible_for_decompose": bool(failing),
    }


def _scope_tuple(manifest: dict) -> tuple[int, int, int, int]:
    return (
        len(manifest.get("requirement_ids") or []),
        len(manifest.get("finding_fingerprints") or []),
        len(manifest.get("proposed_paths") or []),
        len(manifest.get("layers") or []),
    )


def _strict_reduction(parent: dict, child: dict) -> bool:
    parent_t = _scope_tuple(parent)
    child_t = _scope_tuple(child)
    return child_t < parent_t and child_t != (0, 0, 0, 0)


def _cluster_identity(manifest: dict) -> tuple:
    return (
        tuple(sorted(manifest.get("requirement_ids") or [])),
        tuple(sorted(manifest.get("finding_fingerprints") or [])),
    )


def authorize_decomposition(
    parent: dict,
    children: list[dict],
    *,
    ancestor_identities: list[tuple] | None = None,
    depth: int = 0,
    budget_global: int = 1,
    budget_branch: int = 1,
    resolved_fingerprints: set[str] | None = None,
    config: dict | None = None,
) -> dict:
    config = config or load_config()
    decomp = load_caps(config)["decomposition"]
    reasons: list[str] = []
    parent_reqs = set(parent.get("requirement_ids") or [])
    parent_fps = set(parent.get("finding_fingerprints") or [])
    resolved = resolved_fingerprints or set()
    if parent_fps and parent_fps <= resolved:
        return {
            "parent_cluster_id": parent.get("cluster_id"),
            "children": [],
            "authorized": False,
            "reject_reasons": ["effect_satisfied"],
            "noop": True,
        }
    if depth >= decomp["depth"]:
        reasons.append("depth_cap")
    if budget_global <= 0 or budget_branch <= 0:
        reasons.append("budget_exhausted")
    if len(children) > decomp["fanout"]:
        reasons.append("fanout_cap")
    if not children:
        reasons.append("empty_children")

    child_req_union: set[str] = set()
    child_fp_union: set[str] = set()
    seen_ids: list[tuple] = []
    ancestors = set(ancestor_identities or [])
    parent_id = _cluster_identity(parent)
    for child in children:
        ident = _cluster_identity(child)
        reqs = set(child.get("requirement_ids") or [])
        fps = set(child.get("finding_fingerprints") or [])
        if ident == parent_id:
            reasons.append("parent_equal_child")
        if ident in ancestors:
            reasons.append("ancestor_repeat")
        if ident in seen_ids:
            reasons.append("duplicate_child_identity")
        seen_ids.append(ident)
        if reqs & child_req_union:
            reasons.append("duplicate_requirement_ownership")
        if fps & child_fp_union:
            reasons.append("duplicate_finding_ownership")
        child_req_union |= reqs
        child_fp_union |= fps
        if not _strict_reduction(parent, child):
            reasons.append("no_scope_reduction")
        if reqs - parent_reqs:
            reasons.append("invented_requirement")
        if fps - parent_fps:
            reasons.append("invented_finding")

    if child_req_union != parent_reqs:
        reasons.append("requirement_not_conserved")
    if child_fp_union != parent_fps:
        reasons.append("finding_not_conserved")

    authorized = not reasons
    return {
        "parent_cluster_id": parent.get("cluster_id"),
        "children": [child.get("cluster_id") for child in children],
        "authorized": authorized,
        "reject_reasons": sorted(set(reasons)),
        "budget_remaining": max(0, budget_global - (1 if authorized else 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", help="parent decomposition_manifest JSON")
    parser.add_argument("--children", help="JSON array of child manifests")
    parser.add_argument("--shadow", action="store_true", help="advisory-only: never authorize, only report")
    parser.add_argument("--gate", action="store_true", help="explicitly authorize (overrides config shadow default)")
    parser.add_argument("--out")
    args = parser.parse_args()
    parent = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report = evaluate_manifest(parent)
    # D28: shadow is the default until per-project calibration flips config to gating.
    try:
        from swarm_lib import cfg, load_config

        config_mode = cfg(load_config(), "orchestration.decomposition", "shadow")
    except Exception:  # noqa: BLE001 - no config → safest mode
        config_mode = "shadow"
    # Gating needs BOTH the calibrated config flip AND an explicit --gate call;
    # --gate alone must not override the shadow default (delta F13).
    shadow = args.shadow or not (args.gate and config_mode == "gating")
    if args.children:
        children = json.loads(Path(args.children).read_text(encoding="utf-8"))
        decision = authorize_decomposition(parent, children)
        if shadow:
            decision["authorized"] = False
            decision.setdefault("reject_reasons", []).append("shadow_mode")
        report["decomposition"] = decision
    if args.out:
        dump_json(Path(args.out), report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
