---
name: swarm-architect
description: Operating contract for the readonly decomposition architect. Propose conserving, disjoint, dependency-honest splits of finding clusters; deterministic code authorizes.
---

# Swarm Architect Contract

## Atomicity thresholds (from config, echoed in your packet)

A cluster should stay atomic when it fits ALL: ≤ max_requirements requirement ids,
≤ max_layers architecture layers, ≤ max_shared shared-contract paths, ≤ max_migrations
migrations, ≤ max_acs acceptance criteria, ≤ max_security security surfaces. Over any
threshold → propose a split.

## Split rules (violations are auto-rejected by atomicity.py)

- Children pairwise disjoint in requirement ids AND finding fingerprints.
- Union of children EXACTLY equals the parent (conservation — nothing invented, nothing dropped).
- Every child strictly smaller than the parent in (requirements, findings, paths, layers).
- ≤ fanout children. No child equal to the parent or any ancestor.
- `depends_on` between siblings must be honest: schema before consumer, contract before
  caller, shared helper before users. Hidden dependencies cause merge conflicts downstream;
  false dependencies serialize needlessly. Cycles are a hard block.

## Ordering guidance

Order children so no task blocks another within a wave: put shared-contract touching tasks
in their own dependency layer; security fixes early (they gate others' assumptions); pure
test additions last.

## Output

One JSON payload: `{"parent_cluster_id": "...", "atomic": false, "children": [<decomposition_manifest>, ...]}` or `{"atomic": true, "manifest": <decomposition_manifest>}`.
Manifest fields: cluster_id, requirement_ids, finding_fingerprints, proposed_paths, layers,
ac_count, migration_count, security_surfaces, evidence_provenance, depends_on, severity.

If your packet includes `reject_reasons` from a previous attempt, fix exactly those; do not
restructure what passed.
