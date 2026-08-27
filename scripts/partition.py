#!/usr/bin/env python3
"""Split the requirement index into disjoint auditor assignments per mode.

Hard-fails duplicate ownership. Non-faithfulness modes partition by source
document/area rather than requirement id when the index is sparse.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import current_run_dir, dump_json, load_config, load_json


def partition_rows(rows: list[dict], parts: int) -> list[list[dict]]:
    parts = max(1, parts)
    buckets: list[list[dict]] = [[] for _ in range(parts)]
    # Group by source doc so one auditor owns a whole document (coherent context).
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        doc = (row.get("source") or "").split(":", 1)[0]
        by_source.setdefault(doc, []).append(row)
    for idx, doc in enumerate(sorted(by_source)):
        buckets[idx % parts].extend(by_source[doc])
    return [b for b in buckets if b]


def check_disjoint(buckets: list[list[dict]]) -> list[str]:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for idx, bucket in enumerate(buckets):
        for row in bucket:
            rid = row["requirement_id"]
            if rid in seen:
                duplicates.append(f"{rid}:{seen[rid]}&{idx}")
            seen[rid] = idx
    return duplicates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--parts", type=int, default=4)
    parser.add_argument("--index", default=None)
    args = parser.parse_args()
    config = load_config()
    run_dir = current_run_dir(config)
    if run_dir is None:
        print("FAIL partition: CURRENT missing")
        return 1
    index_path = Path(args.index) if args.index else run_dir / "req_index.json"
    index = load_json(index_path)
    rows = index.get("rows") or []
    buckets = partition_rows(rows, args.parts)
    duplicates = check_disjoint(buckets)
    if duplicates:
        print(f"FAIL partition: duplicate_ownership {duplicates}")
        return 1
    result = {
        "mode": args.mode,
        "partitions": [
            {
                "partition_id": f"{args.mode}-p{idx}",
                "requirement_ids": [r["requirement_id"] for r in bucket],
                "sources": sorted({(r.get("source") or "").split(":", 1)[0] for r in bucket}),
            }
            for idx, bucket in enumerate(buckets)
        ],
    }
    out = run_dir / "partitions" / f"{args.mode}.json"
    dump_json(out, result)
    print(f"partition: mode={args.mode} partitions={len(result['partitions'])} -> {out}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
