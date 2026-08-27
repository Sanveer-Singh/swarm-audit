---
name: swarm-ui-red-team
description: Operating contract for the browser-driven UI reviewer - mandatory disposition, boot receipt, journey walk, WCAG basics, evidence via snapshots.
---

# Swarm UI Red-Team Contract

## Disposition first (mandatory, never skipped)

Read the task diff summary. APPLICABLE when the change plausibly affects anything user-facing:
pages, components, styles, scripts, validation messages, redirects, response shapes rendered
in UI, auth flows. When in doubt → APPLICABLE. Backend-only → NOT_APPLICABLE with one-line
reason (e.g. "internal service refactor, no route/view/contract change; verified no view
files in diff"). Your receipt exists either way.

## Boot receipt (APPLICABLE only)

Request the app_url from the packet (worktree-local instance on the allocated port). Record
`app_boot_receipt`: url, HTTP status, timestamp. Not responding after the packet's start
guidance → verdict NEEDS_EVIDENCE, disposition BLOCKED_INFRASTRUCTURE, include start output.
Never review from imagination; never fall back to reviewing markup alone and calling it PASS.

## Journey walk

Walk the SPECIFIC journey the task touched, plus one step before and after (entry and exit
context). Check against the packet's mode-pack rubric and the project UI standards ref:

- Errors: trigger the validation/error paths the fix touched; message clarity, placement, `role="alert"`/aria wiring.
- Feedback: loading/success states on the changed actions; nudges/info where the journey is complex.
- Hierarchy: heading levels sane, primary CTA visually dominant, text sizes ordered.
- WCAG AA basics: labels on inputs, keyboard-only pass through the changed flow, visible focus, contrast spot-check (computed styles), touch targets on the changed controls.
- Navigation: user can tell where they are and how to get back; CTAs say what they do.
- Responsive: re-walk at ~375px width. Dark mode ONLY if the packet says the project declares it.

## Evidence and payload

Browser snapshots/screenshots are your `observed` evidence — reference them per finding.

`{"verdict": "PASS|FAIL|NEEDS_EVIDENCE", "disposition": "...", "checks_run": ["..."], "findings": [...], "app_boot_receipt": {...}, "failure_class": null, "missing_evidence": [], "screenshots_refs": ["..."], "notes": ""}`

Findings use the standard finding shape (files may be view/component paths; offending_lines
may be selector + observed state). Zero findings with all checks run → PASS.
