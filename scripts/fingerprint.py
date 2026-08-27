#!/usr/bin/env python3
"""Canonical finding fingerprint and cross-mode dedupe.

Identity = requirement id + gap class + path + symbol + remediation class.
Line numbers and prose are evidence, not identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SEP = "|"


def norm(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = text.replace("\\", "/")
    text = re.sub(r"\s+", " ", text)
    return text


def fingerprint(requirement_id: str, gap_class: str, path: str, symbol: str, remediation_class: str) -> str:
    payload = SEP.join(
        ["req", norm(requirement_id), norm(gap_class), norm(path), norm(symbol), norm(remediation_class)]
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def from_finding(finding: dict) -> str:
    req_ids = finding.get("requirement_ids") or [finding.get("requirement_id") or ""]
    primary = sorted(str(r) for r in req_ids)[0] if req_ids else ""
    path = ""
    files = finding.get("affected_files") or []
    if files:
        path = sorted(norm(str(f)) for f in files)[0]
    return fingerprint(
        primary,
        finding.get("gap_class") or "",
        path,
        finding.get("symbol") or "",
        finding.get("remediation_class") or "",
    )


def dedupe(findings: list[dict]) -> dict:
    """Cross-mode dedupe: same fingerprint from different modes collapses into one
    entry carrying the union of modes and the strongest evidence level."""
    order = {"observed": 0, "static-proof": 1, "plausible": 2, "speculative": 3}
    merged: dict[str, dict] = {}
    for finding in findings:
        fp = finding.get("fingerprint") or from_finding(finding)
        finding = dict(finding)
        finding["fingerprint"] = fp
        existing = merged.get(fp)
        if existing is None:
            finding["modes"] = sorted({finding.get("mode") or "unknown"})
            merged[fp] = finding
            continue
        modes = set(existing.get("modes") or []) | {finding.get("mode") or "unknown"}
        existing["modes"] = sorted(modes)
        if order.get(finding.get("evidence_level"), 9) < order.get(existing.get("evidence_level"), 9):
            existing["evidence_level"] = finding["evidence_level"]
            existing["evidence_refs"] = finding.get("evidence_refs") or existing.get("evidence_refs")
    return {"unique": list(merged.values()), "collapsed": len(findings) - len(merged)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute or dedupe swarm-audit fingerprints.")
    parser.add_argument("--requirement-id", default="")
    parser.add_argument("--gap-class", default="")
    parser.add_argument("--path", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--remediation-class", default="")
    parser.add_argument("--json", dest="json_path", help="Finding JSON object or array")
    parser.add_argument("--dedupe", action="store_true", help="With --json array: collapse duplicates")
    args = parser.parse_args()

    if args.json_path:
        data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        if args.dedupe:
            json.dump(dedupe(items), sys.stdout, indent=2)
        else:
            out = [{"fingerprint": from_finding(item)} for item in items]
            json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(fingerprint(args.requirement_id, args.gap_class, args.path, args.symbol, args.remediation_class))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
