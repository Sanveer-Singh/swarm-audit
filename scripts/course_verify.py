#!/usr/bin/env python3
"""Deterministic owner course-report verification.

The orchestrator never trusts a course report raw. This module checks:
- schema validity (course_report kind)
- red-team round counts within budgets
- cited files exist at worktree HEAD
- diff paths inside the allowed_paths envelope
- every raised fingerprint resolved, admitted-deferred, or residual
- claimed test files present in the diff

Gate re-run is separate (gates.py); this module verifies structure and envelope.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from loop_breaker import course_round_check
from swarm_lib import (
    caps as load_caps,
    current_run_dir,
    dump_json,
    load_config,
    load_json,
    norm_path,
    now_iso,
    path_in_envelope,
)
from validate_payload import validate


def _build_artifact(path: str) -> bool:
    """Bytecode caches etc. are never a legitimate course change or escape."""
    parts = norm_path(path).split("/")
    return "__pycache__" in parts or path.endswith((".pyc", ".pyo"))


def _swarm_seeded_names(config: dict) -> set[str]:
    from swarm_lib import cfg as _cfg

    names = {_cfg(config, "worktree.env_file", ".worktree-env")}
    db_template = _cfg(config, "worktree.db_template")
    if db_template:
        names.add(Path(db_template).name)
    return names


def git_diff_paths(worktree: Path, base_commit: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", base_commit, "--"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    tracked = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    proc2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    untracked = [line.strip() for line in proc2.stdout.splitlines() if line.strip()]
    return sorted(set(tracked + untracked))


def verify(config: dict, report: dict, packet: dict, worktree: Path) -> dict:
    checks: dict[str, bool | str] = {}
    failures: list[str] = []

    schema_errors = validate("course_report", report)
    checks["schema"] = not schema_errors
    if schema_errors:
        failures.extend(f"schema:{e}" for e in schema_errors)

    limits = load_caps(config)
    round_violations = course_round_check(report.get("red_team_rounds") or {}, limits)
    checks["rounds_within_budget"] = not round_violations
    failures.extend(round_violations)

    seeded = _swarm_seeded_names(config)
    diff_files = [
        f for f in git_diff_paths(worktree, packet.get("base_commit", "HEAD"))
        if not _build_artifact(f) and Path(f).name not in seeded
    ]
    allowed = packet.get("allowed_paths") or []
    outside = [f for f in diff_files if not path_in_envelope(f, allowed)]
    checks["diff_inside_envelope"] = not outside
    if outside:
        failures.append(f"paths_outside_envelope:{outside[:10]}")

    claimed = {norm_path(f) for f in (report.get("diff_summary") or {}).get("files") or []}
    actual = {norm_path(f) for f in diff_files}
    undeclared = sorted(actual - claimed)
    checks["diff_fully_declared"] = not undeclared
    if undeclared:
        failures.append(f"undeclared_diff_files:{undeclared[:10]}")

    missing_cited: list[str] = []
    for receipt in report.get("stage_receipts") or []:
        for cited in receipt.get("cited_files") or []:
            if not (worktree / norm_path(cited)).exists():
                missing_cited.append(cited)
    checks["cited_files_exist"] = not missing_cited
    if missing_cited:
        failures.append(f"cited_files_missing:{missing_cited[:10]}")

    raised = set(report.get("fingerprints_raised") or [])
    resolved = set(report.get("fingerprints_resolved") or [])
    residual_fps = set()
    for risk in report.get("residual_risks") or []:
        if isinstance(risk, dict) and risk.get("fingerprint"):
            residual_fps.add(risk["fingerprint"])
        elif isinstance(risk, str) and risk.startswith("sha256:"):
            residual_fps.add(risk)
    unaccounted = raised - resolved - residual_fps
    checks["fingerprints_accounted"] = not unaccounted
    if unaccounted:
        failures.append(f"unaccounted_fingerprints:{sorted(unaccounted)[:10]}")

    tests_added = [norm_path(t) for t in (report.get("tests") or {}).get("added") or []]
    tests_missing = [t for t in tests_added if t not in actual]
    checks["claimed_tests_in_diff"] = not tests_missing
    if tests_missing:
        failures.append(f"claimed_tests_not_in_diff:{tests_missing[:10]}")

    if report.get("status") == "complete" and report.get("blockers"):
        checks["complete_without_blockers"] = False
        failures.append("status_complete_with_blockers")
    else:
        checks["complete_without_blockers"] = True

    # Confinement backstop (F09): the main checkout must be untouched by the course.
    # Run artifacts (state, receipts, ledger) legitimately change; everything else is escape.
    from swarm_lib import cfg as _cfg, repo_root as _repo_root

    main_root = _repo_root(config)
    if main_root.resolve() != Path(worktree).resolve():
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=main_root, text=True, capture_output=True, check=False
        )
        artifact_roots = tuple(
            norm_path(_cfg(config, key, default)).rstrip("/") + "/"
            for key, default in (("paths.run_root", "Docs/swarm-audit/runs"),
                                 ("paths.toolkit", "Docs/swarm-audit/toolkit"))
        )
        # git reports on-disk case; on case-insensitive filesystems (Windows)
        # config-path casing may differ, so compare case-insensitively there.
        fold = (lambda s: s.casefold()) if os.name == "nt" else (lambda s: s)
        folded_roots = tuple(fold(r) for r in artifact_roots)
        dirty = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            rel = norm_path(line[3:].strip().strip('"'))
            if _build_artifact(rel) or fold(rel).startswith(folded_roots):
                continue
            dirty.append(line)
        checks["main_checkout_clean"] = not dirty
        if dirty:
            failures.append(f"main_checkout_dirty:{dirty[:5]}")
    else:
        checks["main_checkout_clean"] = True

    return {
        "task_id": report.get("task_id"),
        "course_report_ref": "",
        "checks": checks,
        "failures": failures,
        "passed": not failures,
        "plan_sha256": report.get("plan_sha256"),
        "addendum_sha256": report.get("addendum_sha256"),
        "at": now_iso(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_json")
    parser.add_argument("packet_json")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    config = load_config()
    report = load_json(Path(args.report_json))
    packet = load_json(Path(args.packet_json))
    receipt = verify(config, report, packet, Path(args.worktree))
    receipt["course_report_ref"] = args.report_json
    if args.out:
        dump_json(Path(args.out), receipt)
    else:
        run_dir = current_run_dir(config)
        if run_dir is not None:
            dump_json(run_dir / "gate-receipts" / f"verify-{receipt.get('task_id')}.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
