#!/usr/bin/env python3
"""Requirement intake: index all requirement ids and text from configured roots.

requirements.mode:
- documented: walk requirements.roots, extract ids by id_patterns, embed text.
  Empty index fails loudly (F12) unless --waive with a reason.
- code_contracts: emit a skeleton instructing auditors to derive contracts from
  routes/schemas/tests; rows are created by the orchestrator from auditor output.
- none: faithfulness mode disabled; emits an empty index with waiver.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from swarm_lib import cfg, current_run_dir, dump_json, id_patterns, load_config, now_iso, repo_root

TEXT_SUFFIXES = {".md", ".txt", ".yml", ".yaml", ".rst", ".adoc"}
CONTEXT_LINES = 6


def extract_rows(config: dict) -> list[dict]:
    root = repo_root(config)
    patterns = id_patterns(config)
    rows: dict[str, dict] = {}
    for rel in cfg(config, "requirements.roots", []) or []:
        base = root / rel
        if not base.exists():
            raise FileNotFoundError(f"requirements root missing: {rel}")
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES)
        for file in files:
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines):
                for pattern in patterns:
                    for match in pattern.finditer(line):
                        req_id = match.group(1)
                        if req_id in rows:
                            continue
                        lo = max(0, idx - 1)
                        hi = min(len(lines), idx + CONTEXT_LINES)
                        rows[req_id] = {
                            "requirement_id": req_id,
                            "source": f"{file.relative_to(root)}:{idx + 1}",
                            "text": "\n".join(lines[lo:hi]).strip()[:2000],
                            "kind": req_id.split("-", 1)[0],
                        }
    return [rows[k] for k in sorted(rows)]


def build_index(config: dict, waive: str | None) -> dict:
    mode = cfg(config, "requirements.mode", "documented")
    index = {
        "mode": mode,
        "generated_at": now_iso(),
        "roots": cfg(config, "requirements.roots", []),
        "inferred_allowed": bool(cfg(config, "requirements.allow_inferred", True)),
        "rows": [],
    }
    if mode == "none":
        index["waiver"] = waive or "requirements.mode=none: faithfulness mode disabled"
        return index
    if mode == "code_contracts":
        index["waiver"] = "code_contracts: rows derived from routes/schemas/tests by auditors, persisted by orchestrator"
        return index
    rows = extract_rows(config)
    if not rows:
        if waive:
            index["waiver"] = waive
        else:
            raise ValueError(
                "empty requirement index in documented mode; fix requirements.roots/id_patterns "
                "or switch requirements.mode, or pass --waive with a reason"
            )
    index["rows"] = rows
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--waive", default=None, help="explicit reason to accept an empty index")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    config = load_config()
    try:
        index = build_index(config, args.waive)
    except (ValueError, FileNotFoundError) as exc:
        print(f"FAIL intake: {exc}")
        return 1
    run_dir = current_run_dir(config)
    out = Path(args.out) if args.out else (run_dir / "req_index.json" if run_dir else None)
    if out is None:
        print("FAIL intake: no CURRENT run and no --out")
        return 1
    dump_json(out, index)
    print(f"intake: mode={index['mode']} rows={len(index['rows'])} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
