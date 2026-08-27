#!/usr/bin/env python3
"""Validate swarm-audit artifacts against the schema registry.

Strict: unknown fields fail. Mode-aware verdicts (delta rejects REVISE).
Enum legality enforced against swarm_lib enums.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import (
    ADMISSION_VERDICTS,
    COURSE_STATUSES,
    DELTA_VERDICTS,
    EVIDENCE_LEVELS,
    MODES,
    PHASE_SET,
    RED_TEAM_MODES,
    UI_DISPOSITIONS,
    VERDICTS,
)

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
REGISTRY = json.loads((SCHEMAS / "required-fields.json").read_text(encoding="utf-8"))


def registry_kinds() -> list[str]:
    return [k for k in REGISTRY if not k.startswith("_")]


def _spec(kind: str) -> dict | None:
    spec = REGISTRY.get(kind)
    if spec is None or kind.startswith("_"):
        return None
    return spec


def missing_fields(obj: dict, required: list[str]) -> list[str]:
    return [key for key in required if key not in obj or obj[key] in (None, "")]


def allowed_keys(spec: dict) -> set[str]:
    keys = set(spec.get("required") or [])
    keys.update(spec.get("optional") or [])
    return keys


def validate(kind: str, obj: dict) -> list[str]:
    spec = _spec(kind)
    if spec is None:
        return [f"unknown payload kind: {kind}"]
    if not isinstance(obj, dict):
        return ["not_an_object"]
    errors: list[str] = []
    errors.extend(f"missing:{field}" for field in missing_fields(obj, spec["required"]))
    extra = sorted(set(obj) - allowed_keys(spec))
    errors.extend(f"unknown:{field}" for field in extra)

    verdict = obj.get("verdict")
    mode = obj.get("mode")

    if kind == "red_team_verdict":
        if mode not in RED_TEAM_MODES:
            errors.append(f"illegal_red_team_mode:{mode}")
        if mode == "delta":
            if verdict not in DELTA_VERDICTS:
                errors.append(f"illegal_delta_verdict:{verdict}")
        elif mode == "admission":
            # admission verdicts live per-row; envelope verdict must be PASS/FAIL/NEEDS_EVIDENCE
            if verdict not in DELTA_VERDICTS:
                errors.append(f"illegal_admission_envelope_verdict:{verdict}")
        elif verdict not in VERDICTS:
            errors.append(f"illegal_verdict:{verdict}")
        for finding in obj.get("findings") or []:
            if isinstance(finding, dict):
                level = finding.get("evidence_level")
                if level is not None and level not in EVIDENCE_LEVELS:
                    errors.append(f"illegal_evidence_level:{level}")
                if level == "speculative":
                    errors.append("speculative_finding_reported")

    if kind == "ui_review":
        if verdict not in DELTA_VERDICTS:
            errors.append(f"illegal_ui_verdict:{verdict}")
        disposition = obj.get("disposition")
        if disposition not in UI_DISPOSITIONS:
            errors.append(f"illegal_disposition:{disposition}")
        if disposition == "BLOCKED_INFRASTRUCTURE" and verdict != "NEEDS_EVIDENCE":
            errors.append("blocked_infrastructure_requires_needs_evidence")
        if disposition == "APPLICABLE" and verdict == "PASS" and obj.get("missing_evidence"):
            errors.append("applicable_pass_with_missing_evidence")

    if kind == "finding":
        level = obj.get("evidence_level")
        if level not in EVIDENCE_LEVELS:
            errors.append(f"illegal_evidence_level:{level}")
        if mode is not None and mode not in MODES:
            errors.append(f"illegal_mode:{mode}")
        req_ids = obj.get("requirement_ids") or []
        inferred = [r for r in req_ids if str(r).startswith("INF-")]
        if inferred and not obj.get("inferred_provenance"):
            errors.append("inferred_requirement_without_provenance")

    if kind == "coverage_row":
        cls = obj.get("classification")
        if cls not in {"covered", "gap", "partial", "ambiguous"}:
            errors.append(f"illegal_classification:{cls}")

    if kind == "admission_row" and obj.get("admission") not in ADMISSION_VERDICTS:
        errors.append(f"illegal_admission:{obj.get('admission')}")

    if kind == "run_state":
        if obj.get("phase") not in PHASE_SET:
            errors.append(f"illegal_phase:{obj.get('phase')}")
        if obj.get("tier") not in {"owner", "flat", None}:
            errors.append(f"illegal_tier:{obj.get('tier')}")

    if kind == "course_report":
        if obj.get("status") not in COURSE_STATUSES:
            errors.append(f"illegal_course_status:{obj.get('status')}")
        rounds = obj.get("red_team_rounds") or {}
        if isinstance(rounds, dict):
            for key in ("plan", "impl", "delta"):
                if key not in rounds:
                    errors.append(f"missing_round_counter:{key}")
        receipts = obj.get("stage_receipts")
        if isinstance(receipts, list):
            for idx, receipt in enumerate(receipts):
                if not isinstance(receipt, dict) or missing_fields(
                    receipt, ["stage", "verdict", "payload_ref"]
                ):
                    errors.append(f"invalid_stage_receipt:{idx}")

    if kind == "journal_entry" and obj.get("status") not in {"intent", "completed", "rolled_back", "reconciled"}:
        errors.append(f"illegal_journal_status:{obj.get('status')}")

    if kind == "audit_payload":
        # nested validation: every finding and coverage row must be valid itself
        for idx, row in enumerate(obj.get("coverage_rows") or []):
            for err in validate("coverage_row", row if isinstance(row, dict) else {}):
                errors.append(f"coverage_rows[{idx}].{err}")
        for idx, row in enumerate(obj.get("findings") or []):
            row = dict(row) if isinstance(row, dict) else {}
            if not row.get("fingerprint"):
                row["fingerprint"] = "pending"  # orchestrator computes identity post-hoc
            for err in validate("finding", row):
                errors.append(f"findings[{idx}].{err}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind")
    parser.add_argument("path")
    args = parser.parse_args()
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    failed = False
    for item in items:
        errs = validate(args.kind, item)
        if errs:
            failed = True
            print(f"FAIL {args.kind}: {', '.join(errs)}")
        else:
            print(f"OK {args.kind}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
