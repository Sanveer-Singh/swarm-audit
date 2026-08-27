#!/usr/bin/env python3
"""Circuit breakers for red-team loops and recovery budgets.

Plan-freeze trips: double FAIL, repeated fingerprints, >=50% overlap.
Recovery budgets: every recovery transition decrements; zero blocks.
"""
from __future__ import annotations


def trip(prev_fingerprints: set[str], curr_fingerprints: set[str], fail_count: int) -> str | None:
    if fail_count >= 2:
        return "fail_twice"
    if curr_fingerprints and curr_fingerprints <= prev_fingerprints:
        return "same_fingerprint"
    union = prev_fingerprints | curr_fingerprints
    if union and prev_fingerprints and len(prev_fingerprints & curr_fingerprints) / len(union) >= 0.5:
        return "overlap"
    return None


def revise_allowed(revise_count: int, max_rounds: int) -> bool:
    return revise_count < max_rounds


RECOVERY_KINDS = ("owner_attempts", "evidence_refreshes", "architect_consults", "replans")


def consume(budget_row: dict, kind: str) -> tuple[bool, dict]:
    """Decrement one recovery budget. Returns (allowed, updated_row).

    budget_row holds remaining counts, e.g. {"owner_attempts": 2, ...}.
    Unknown kinds are refused (never allow unbudgeted recovery paths).
    """
    if kind not in RECOVERY_KINDS:
        return False, budget_row
    remaining = int(budget_row.get(kind, 0))
    if remaining <= 0:
        return False, budget_row
    updated = dict(budget_row)
    updated[kind] = remaining - 1
    return True, updated


def course_round_check(report_rounds: dict, caps: dict) -> list[str]:
    """Deterministic re-check of an owner course report's claimed rounds.

    red_team_rounds.plan counts TOTAL plan review rounds (initial + revises),
    so the legal maximum is plan_revise_rounds + 1 (F12).
    """
    violations: list[str] = []
    if int(report_rounds.get("plan", 0)) > int(caps.get("plan_revise_rounds", 2)) + 1:
        violations.append("plan_rounds_over_budget")
    if int(report_rounds.get("impl", 0)) > int(caps.get("remediation_batches", 1)):
        violations.append("remediation_over_budget")
    if int(report_rounds.get("delta", 0)) > 1:
        violations.append("delta_over_budget")
    return violations
