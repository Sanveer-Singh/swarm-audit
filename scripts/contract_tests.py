#!/usr/bin/env python3
"""Swarm-audit contract tests (plan section 13, items 1-28).

Self-contained: builds a temp fixture repo (git + config + installed hooks/toolkit),
runs every deterministic contract offline. Platform-dependent behaviors (live hook
firing, real subagent nesting) are exercised at smoke time; here we replay documented
payload shapes. Exit 1 on any FAIL; SKIPs are reported.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
TOOLKIT = SCRIPTS.parent
# Source layout: swarm-audit/{scripts,schemas,templates,modes,docs,install.py}
# Installed layout: toolkit/{scripts,schemas,modes,docs}; hooks/agents live under target/.cursor
SOURCE_ROOT = TOOLKIT if (TOOLKIT / "templates").exists() else None

FIXTURE = Path(tempfile.mkdtemp(prefix="swarm-contract-"))
os.environ["SWARM_TARGET"] = str(FIXTURE)

sys.path.insert(0, str(SCRIPTS))

RESULTS: list[tuple[str, str, str]] = []


def record(item: str, status: str, detail: str = "") -> None:
    RESULTS.append((item, status, detail))


def sh(args: list[str], cwd: Path, stdin: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(args, cwd=cwd, input=stdin, text=True, capture_output=True, env=merged, check=False)


def find_dir(*candidates: Path) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


HOOKS_SRC = find_dir(
    SOURCE_ROOT / "templates" / "hooks" if SOURCE_ROOT else None,
    Path(os.environ.get("SWARM_INSTALL_TARGET", "")) / ".cursor" / "hooks" / "swarm" if os.environ.get("SWARM_INSTALL_TARGET") else None,
)
AGENTS_SRC = find_dir(
    SOURCE_ROOT / "templates" / "agents" if SOURCE_ROOT else None,
    Path(os.environ.get("SWARM_INSTALL_TARGET", "")) / ".cursor" / "agents" if os.environ.get("SWARM_INSTALL_TARGET") else None,
)
MODES_SRC = find_dir(SOURCE_ROOT / "modes" if SOURCE_ROOT else None, TOOLKIT / "modes")
EXAMPLE_CONFIG = find_dir(
    SOURCE_ROOT / "swarm.config.example.json" if SOURCE_ROOT else None,
    TOOLKIT / "swarm.config.example.json",
)


def setup_fixture() -> None:
    (FIXTURE / "src").mkdir(parents=True)
    (FIXTURE / "docs-req").mkdir()
    (FIXTURE / "docs-req" / "stories.md").write_text(
        "# Stories\n\nUS-100: user can log in.\nThe login page validates input.\n\nUS-101: user can log out.\n",
        encoding="utf-8",
    )
    config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    config["project"]["name"] = "fixture"
    config["requirements"]["roots"] = ["docs-req"]
    config["requirements"]["id_patterns"] = ["US-[0-9]+"]
    config["gates"]["commands"] = [
        {"id": "echo", "cmd": f'"{sys.executable}" -c "print(1)"', "timeout_s": 30, "required": True}
    ]
    config["gates"]["per_mode"] = {}
    config["tracker"]["adapter"] = "none"
    config["mcp"]["browser"] = True
    (FIXTURE / "swarm.config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    sh(["git", "init", "-q"], FIXTURE)
    sh(["git", "config", "user.email", "t@t"], FIXTURE)
    sh(["git", "config", "user.name", "t"], FIXTURE)
    (FIXTURE / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    sh(["git", "add", "-A"], FIXTURE)
    sh(["git", "commit", "-qm", "init"], FIXTURE)
    # install toolkit scripts + schemas into fixture per config paths.toolkit
    toolkit_dest = FIXTURE / config["paths"]["toolkit"]
    shutil.copytree(SCRIPTS, toolkit_dest / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    schemas_src = TOOLKIT / "schemas"
    shutil.copytree(schemas_src, toolkit_dest / "schemas")
    if HOOKS_SRC:
        dest = FIXTURE / ".cursor" / "hooks" / "swarm"
        dest.mkdir(parents=True)
        for f in HOOKS_SRC.glob("*.py"):
            shutil.copy2(f, dest / f.name)
    # a live run
    proc = sh([sys.executable, str(toolkit_dest / "scripts" / "new_run.py"), "--run-id", "swarm-fixture"], FIXTURE)
    if proc.returncode != 0:
        raise RuntimeError(f"fixture new_run failed: {proc.stdout} {proc.stderr}")


def fixture_run_dir(config) -> Path:
    import swarm_lib

    return swarm_lib.current_run_dir(config)


def valid_packet(run_dir: Path, name: str, role: str = "swarm-planner") -> str:
    rel = f"Docs/swarm-audit/runs/{run_dir.name}/packets/{name}.json"
    packet = {
        "run_id": run_dir.name, "agent_role": role, "goal": "g", "non_goals": ["n"],
        "requirement_ids": ["US-100"], "requirement_text": {"US-100": "t"}, "base_commit": "abc",
        "allowed_paths": ["src/"], "forbidden_paths": [], "commands": ["echo"],
        "budgets": {"owner_attempts": 2}, "output_kind": "frozen_plan", "attempt": 1,
        "packet_path": rel, "capability_manifest": {"stack": "test"},
    }
    path = run_dir / "packets" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet), encoding="utf-8")
    return rel


def hook(name: str, payload: dict | None) -> dict:
    hooks_dir = FIXTURE / ".cursor" / "hooks" / "swarm"
    stdin = json.dumps(payload) if payload is not None else ""
    proc = sh([sys.executable, str(hooks_dir / name)], FIXTURE, stdin=stdin)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": proc.stdout + proc.stderr}


# ---------------------------------------------------------------- tests

def t01_config_schema():
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    assert config["project"]["name"] == "fixture"
    schema = json.loads((TOOLKIT / "schemas" / "config.schema.json").read_text(encoding="utf-8")) if (TOOLKIT / "schemas" / "config.schema.json").exists() else json.loads((SOURCE_ROOT / "swarm.config.schema.json").read_text(encoding="utf-8"))
    good = json.loads((FIXTURE / "swarm.config.json").read_text(encoding="utf-8"))
    assert not swarm_lib._required_keys_from_schema(schema, good)
    bad_missing = dict(good)
    bad_missing.pop("project")
    assert any(e.startswith("missing:project") for e in swarm_lib._required_keys_from_schema(schema, bad_missing))
    bad_unknown = dict(good)
    bad_unknown["nonsense_key"] = 1
    assert any(e.startswith("unknown:nonsense_key") for e in swarm_lib._required_keys_from_schema(schema, bad_unknown))
    record("01 config schema", "PASS")


def t02_agent_frontmatter():
    if AGENTS_SRC is None:
        record("02 agent frontmatter", "SKIP", "agent templates not found")
        return
    depth2 = {"swarm-planner", "swarm-red-team", "swarm-implementer", "swarm-ui-red-team", "swarm-solution-architect"}
    for f in AGENTS_SRC.glob("swarm-*.md"):
        text = f.read_text(encoding="utf-8")
        front = text.split("---")[1]
        assert f"name: {f.stem}" in front, f.stem
        if f.stem == "swarm-implementer":
            assert "readonly" not in front, "implementer must be writable"
        else:
            assert "readonly: true" in front, f"{f.stem} must be readonly"
        assert f"@{f.stem}" in text, f"{f.stem} missing paired skill ref"
        if f.stem in depth2:
            assert "Task tool" in text, f"{f.stem} missing Task-forbidden clause"
    if SOURCE_ROOT and (SOURCE_ROOT / "install.py").exists():
        sys.path.insert(0, str(SOURCE_ROOT))
        import install as installer

        out = installer.inject_model("model: inherit\n", "swarm-implementer", {"models": {"implementer": "composer-x"}})
        assert "model: composer-x" in out
        out2 = installer.inject_model("model: inherit\n", "swarm-owner", {"models": {"owner": "inherit"}})
        assert "model:" not in out2
    record("02 agent frontmatter", "PASS")


def t03_payload_validation():
    from validate_payload import validate

    errs = validate("red_team_verdict", {"mode": "delta", "verdict": "REVISE", "findings": [], "attempt": 1})
    assert any("illegal_delta_verdict" in e for e in errs)
    errs = validate("finding", {"mode": "security", "requirement_ids": ["US-1"], "issue": "x", "impact": "y",
                                "offending_lines": ["a:1"], "affected_files": ["a"], "affected_features": ["US-1"],
                                "evidence_level": "observed", "evidence_refs": ["a:1"], "fingerprint": "f",
                                "proposed_minimal_fix": "z", "confidence": 0.9, "bogus_field": 1})
    assert any("unknown:bogus_field" in e for e in errs)
    report = {"task_id": "t", "plan_sha256": "h", "stage_receipts": [{"stage": "plan"}],
              "red_team_rounds": {"plan": 0, "impl": 0, "delta": 0}, "fingerprints_raised": ["x"],
              "fingerprints_resolved": ["x"], "diff_summary": {"files": []}, "tests": {"added": []},
              "ui_disposition": "NOT_APPLICABLE", "blockers": [], "residual_risks": [], "status": "complete"}
    errs = validate("course_report", report)
    assert any("invalid_stage_receipt" in e for e in errs)
    record("03 payload validation", "PASS")


def t04_fingerprint():
    import fingerprint as fp

    a = {"requirement_ids": ["US-1"], "gap_class": "missing", "affected_files": ["src/A.cs"], "symbol": "Foo.Bar",
         "remediation_class": "add-check", "mode": "security", "evidence_level": "plausible", "issue": "worded one way"}
    b = {**a, "issue": "completely different prose", "mode": "faithfulness", "evidence_level": "observed"}
    c = {**a, "symbol": "Other.Thing"}
    out = fp.dedupe([a, b, c])
    assert out["collapsed"] == 1 and len(out["unique"]) == 2
    merged = next(u for u in out["unique"] if u["symbol"] == "Foo.Bar")
    assert set(merged["modes"]) == {"security", "faithfulness"}
    assert merged["evidence_level"] == "observed"
    record("04 fingerprint dedupe", "PASS")


def t05_loop_breaker():
    import loop_breaker as lb

    assert lb.trip(set(), set(), 2) == "fail_twice"
    assert lb.trip({"f1"}, {"f1"}, 0) == "same_fingerprint"
    assert lb.trip({"f1", "f2"}, {"f1", "f2", "f3"}, 0) == "overlap"
    assert lb.trip({"f1"}, {"f9"}, 0) is None
    assert lb.revise_allowed(1, 2) and not lb.revise_allowed(2, 2)
    ok, row = lb.consume({"evidence_refreshes": 1}, "evidence_refreshes")
    assert ok and row["evidence_refreshes"] == 0
    ok, _ = lb.consume(row, "evidence_refreshes")
    assert not ok
    ok, _ = lb.consume({"bogus": 5}, "bogus")
    assert not ok, "unbudgeted recovery kinds must be refused"
    record("05 loop breaker", "PASS")


def t06_atomicity():
    import atomicity
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    parent = {"cluster_id": "p", "requirement_ids": ["US-1", "US-2"], "finding_fingerprints": ["f1", "f2"],
              "proposed_paths": ["src/a", "src/b"], "layers": ["web"], "ac_count": 2, "migration_count": 0,
              "security_surfaces": [], "evidence_provenance": ["x"], "depends_on": []}
    child_same = dict(parent, cluster_id="c1")
    res = atomicity.authorize_decomposition(parent, [child_same], budget_global=5, budget_branch=2, config=config)
    assert not res["authorized"] and "parent_equal_child" in res["reject_reasons"]
    c1 = dict(parent, cluster_id="c1", requirement_ids=["US-1"], finding_fingerprints=["f1"], proposed_paths=["src/a"])
    c2 = dict(parent, cluster_id="c2", requirement_ids=["US-1", "US-2"], finding_fingerprints=["f2"], proposed_paths=["src/b"])
    res = atomicity.authorize_decomposition(parent, [c1, c2], budget_global=5, budget_branch=2, config=config)
    assert not res["authorized"] and "duplicate_requirement_ownership" in res["reject_reasons"]
    c2ok = dict(parent, cluster_id="c2", requirement_ids=["US-2"], finding_fingerprints=[], proposed_paths=["src/b"])
    res = atomicity.authorize_decomposition(parent, [c1, c2ok], budget_global=5, budget_branch=2, config=config)
    assert not res["authorized"] and "finding_not_conserved" in res["reject_reasons"]
    c2full = dict(parent, cluster_id="c2", requirement_ids=["US-2"], finding_fingerprints=["f2"], proposed_paths=["src/b"])
    res = atomicity.authorize_decomposition(parent, [c1, c2full], budget_global=5, budget_branch=2, config=config)
    assert res["authorized"], res["reject_reasons"]
    res = atomicity.authorize_decomposition(parent, [c1, c2full], budget_global=0, budget_branch=2, config=config)
    assert not res["authorized"] and "budget_exhausted" in res["reject_reasons"]
    record("06 atomicity", "PASS")


def t07_conflict_shared_paths():
    import conflict_graph
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    shared = config["architecture"]["shared_conflict_paths"][0]
    tasks = [
        {"id": "a", "allowed_paths": [shared], "depends_on": [], "severity": "major"},
        {"id": "b", "allowed_paths": [config["architecture"]["shared_conflict_paths"][1]], "depends_on": [], "severity": "major"},
        {"id": "c", "allowed_paths": ["src/other/"], "depends_on": [], "severity": "major"},
    ]
    waves = conflict_graph.build_waves(config, tasks)
    for wave in waves:
        assert not ({"a", "b"} <= set(wave)), "shared-conflict tasks co-batched"
    assert any("c" in wave and ("a" in wave or "b" in wave) for wave in waves), "disjoint task should co-batch"
    record("07 conflict graph shared paths", "PASS")


def t08_admission_caps():
    import admission
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    occ = admission.worst_case_occupancy(config)
    assert occ["fits"], occ
    ok, reason = admission.can_admit("swarm-implementer", 8, 2, pipeline=True, writable=True, config=config)
    assert not ok and reason == "live_cap"
    ok, reason = admission.can_admit("swarm-implementer", 4, 2, pipeline=True, writable=True, config=config)
    assert not ok and reason == "writer_cap"
    ok, _ = admission.can_admit("random-agent", 99, 99, pipeline=False, writable=False, config=config)
    assert ok
    record("08 admission caps", "PASS")


def t09_run_state():
    import run_state
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    state = {"run_id": run_dir.name, "phase": "init", "next_action": "intake", "base_commit": "abc",
             "status": "running", "modes": ["code-quality"], "tier": "owner", "tasks": {}}
    run_state.persist(run_dir, state)
    run_state.transition(run_dir, "intake", "partition")
    try:
        run_state.transition(run_dir, "course", "x")
        raise AssertionError("illegal jump allowed")
    except ValueError:
        pass
    st = run_state.load_state(run_dir)
    for phase in ["partition", "audit", "dedupe", "decompose", "record", "wave", "course", "verify"]:
        run_state.transition(run_dir, phase, "next")
    try:
        run_state.transition(run_dir, "course", "redo same plan")
        raise AssertionError("verify->course without replan allowed")
    except ValueError:
        pass
    run_state.transition(run_dir, "course", "replan receipt: plans/x.r2.md")
    assert (run_dir / "checkpoints" / "course.json").exists()
    run_state.task_transition(run_dir, "task-1", "coursing", plan_sha256="h1")
    try:
        run_state.task_transition(run_dir, "task-1", "integrated")
        raise AssertionError("task state jump allowed")
    except ValueError:
        pass
    record("09 run state", "PASS")


def t10_course_verify():
    import course_verify
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    sh(["git", "add", "-A"], FIXTURE)
    sh(["git", "commit", "-qm", "fixture state"], FIXTURE)
    base = sh(["git", "rev-parse", "HEAD"], FIXTURE).stdout.strip()
    (FIXTURE / "outside.txt").write_text("x", encoding="utf-8")
    (FIXTURE / "src" / "fixed.py").write_text("ok\n", encoding="utf-8")
    packet = {"base_commit": base, "allowed_paths": ["src/"]}
    report = {"task_id": "t1", "plan_sha256": "h", "stage_receipts": [
                  {"stage": "plan", "verdict": "PASS", "payload_ref": "p", "cited_files": ["src/app.py"]}],
              "red_team_rounds": {"plan": 5, "impl": 0, "delta": 0},
              "fingerprints_raised": ["fA"], "fingerprints_resolved": [],
              "diff_summary": {"files": ["src/fixed.py"]}, "tests": {"added": ["src/test_missing.py"]},
              "ui_disposition": "NOT_APPLICABLE", "blockers": [], "residual_risks": [], "status": "complete"}
    receipt = course_verify.verify(config, report, packet, FIXTURE)
    failures = " ".join(receipt["failures"])
    assert "plan_rounds_over_budget" in failures
    assert "paths_outside_envelope" in failures
    assert "unaccounted_fingerprints" in failures
    assert "claimed_tests_not_in_diff" in failures
    (FIXTURE / "outside.txt").unlink()
    good = dict(report)
    good["red_team_rounds"] = {"plan": 1, "impl": 0, "delta": 0}
    good["fingerprints_resolved"] = ["fA"]
    good["diff_summary"] = {"files": ["src/fixed.py"], "insertions": 1, "deletions": 0}
    good["tests"] = {"added": [], "commands_run": ["echo"], "results": "pass"}
    receipt = course_verify.verify(config, good, packet, FIXTURE)
    assert receipt["passed"], receipt["failures"]
    (FIXTURE / "src" / "fixed.py").unlink()
    record("10 course verify", "PASS")


def t11_hooks():
    if not (FIXTURE / ".cursor" / "hooks" / "swarm" / "subagent_start.py").exists():
        record("11 hooks", "SKIP", "hooks not staged")
        return
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    (run_dir / "agents.jsonl").unlink(missing_ok=True)
    (run_dir / "reservations.json").unlink(missing_ok=True)
    out = hook("subagent_start.py", None)
    assert out.get("permission") == "deny", out
    out = hook("subagent_start.py", {"subagent_type": "swarm-planner", "task": "no packet"})
    assert out.get("permission") == "deny"
    rel = valid_packet(run_dir, "ct-planner")
    out = hook("subagent_start.py", {"subagent_type": "swarm-planner", "task": f"go. packet_path: {rel}"})
    assert out.get("permission") == "allow", out
    assert "followup_message" not in json.dumps(out)
    out = hook("subagent_stop.py", {"subagent_type": "swarm-planner", "task": f"done. packet_path: {rel}", "status": "completed"})
    assert out.get("permission") == "allow"
    from registry import snapshot

    snap = snapshot(run_dir)
    rec = next(a for a in snap["agents"] if a["packet_path"] == rel)
    assert rec["status"] == "completed"
    events = swarm_lib.read_jsonl(run_dir / "agents.jsonl")
    bad = [e for e in events if e.get("event") == "spawn" and not e.get("packet_path") and not e.get("subagent_id") and e.get("status") == "allowed"]
    assert not bad, "uncorrelated spawn counted live"
    record("11 hooks", "PASS")


def t12_worktree():
    import swarm_lib
    import worktree

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    res = worktree.create(config, run_dir, "ct-wt1", "HEAD")
    assert res["ok"], res
    low, high = config["worktree"]["port_range"]
    assert low <= res["port"] <= high
    wt = Path(res["path"])
    assert (wt / config["worktree"].get("env_file", ".worktree-env")).exists()
    assert not (wt / "pgms.db").exists(), "main DB must never be copied implicitly"
    allocs_path = worktree.allocations_path(run_dir)
    allocs = json.loads(allocs_path.read_text(encoding="utf-8"))
    for i in range(config["caps"]["max_worktrees"]):
        allocs[f"pad-{i}"] = {"path": f"/x/{i}", "branch": "b"}
    allocs_path.write_text(json.dumps(allocs), encoding="utf-8")
    res2 = worktree.create(config, run_dir, "ct-wt2", "HEAD")
    assert not res2["ok"] and "max_worktrees" in res2["reason"]
    allocs = json.loads(allocs_path.read_text(encoding="utf-8"))
    for i in range(config["caps"]["max_worktrees"]):
        allocs.pop(f"pad-{i}")
    allocs_path.write_text(json.dumps(allocs), encoding="utf-8")
    record("12 worktree", "PASS")


def t13_tracker():
    import swarm_lib
    import tracker

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    result = tracker.act(config, run_dir, "create", {"title": "t", "type": "task", "spec_id": "US-100", "parent": "e", "id": "fix-1"}, "ct:create:1")
    assert result["ok"]
    registry_file = swarm_lib.registries_root(config) / "issue-registry.md"
    assert registry_file.exists() and "US-100" in registry_file.read_text(encoding="utf-8")
    rendered = tracker.render("bd create --title=\"{title}\" --type={type}", {"title": "X", "type": "task"})
    assert rendered == 'bd create --title="X" --type=task'
    if (FIXTURE / ".cursor" / "hooks" / "swarm" / "before_shell.py").exists():
        cfg2 = json.loads((FIXTURE / "swarm.config.json").read_text(encoding="utf-8"))
        cfg2["tracker"]["adapter"] = "beads"
        (FIXTURE / "swarm.config.json").write_text(json.dumps(cfg2, indent=2), encoding="utf-8")
        out = hook("before_shell.py", {"command": "bd create --title=x", "cwd": str(Path.home() / ".swarm-audit" / "worktrees" / "z")})
        cfg2["tracker"]["adapter"] = "none"
        (FIXTURE / "swarm.config.json").write_text(json.dumps(cfg2, indent=2), encoding="utf-8")
        assert out.get("permission") == "deny", out
    record("13 tracker", "PASS")


def t14_installer():
    if not SOURCE_ROOT or not (SOURCE_ROOT / "install.py").exists():
        record("14 installer", "SKIP", "source layout unavailable")
        return
    target = Path(tempfile.mkdtemp(prefix="swarm-install-"))
    try:
        sh(["git", "init", "-q"], target)
        hooks_dir = target / ".cursor"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text(json.dumps({"hooks": {"subagentStart": [{"command": "python existing_hook.py"}]}}), encoding="utf-8")
        env = {"SWARM_TARGET": str(target)}
        proc = sh([sys.executable, str(SOURCE_ROOT / "install.py"), "--target", str(target), "--skip-checks"], SOURCE_ROOT, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        manifest1 = (target / "install-manifest.json").read_text(encoding="utf-8")
        merged = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        starts = [e["command"] for e in merged["hooks"]["subagentStart"]]
        assert "python existing_hook.py" in starts, "pre-existing hook lost"
        assert any("swarm" in s for s in starts)
        proc = sh([sys.executable, str(SOURCE_ROOT / "install.py"), "--target", str(target), "--skip-checks"], SOURCE_ROOT, env=env)
        assert proc.returncode == 0
        manifest2 = (target / "install-manifest.json").read_text(encoding="utf-8")
        files1 = json.loads(manifest1)["files"]
        files2 = json.loads(manifest2)["files"]
        assert files1 == files2, "re-install not idempotent"
        proc = sh([sys.executable, str(SOURCE_ROOT / "uninstall.py"), "--target", str(target)], SOURCE_ROOT, env=env)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for rel in files1:
            assert not (target / rel).exists(), f"uninstall left {rel}"
        merged = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
        assert merged["hooks"]["subagentStart"] == [{"command": "python existing_hook.py"}]
        record("14 installer", "PASS")
    finally:
        shutil.rmtree(target, ignore_errors=True)


def t15_gates_dry_run():
    import gates
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    result = gates.execute(config, FIXTURE, "HEAD", mode=None, only=None, dry_run=True)
    assert result["passed"] and all(g.get("dry_run") for g in result["gates"])
    from validate_payload import validate

    for g in result["gates"]:
        errs = validate("gate_receipt", g)
        assert not errs, errs
    record("15 gates dry-run", "PASS")


def t16_mode_packs():
    if MODES_SRC is None or not MODES_SRC.exists():
        record("16 mode packs", "SKIP", "modes/ not yet present (composer drafting)")
        return
    required = {"faithfulness", "ui-ux", "code-quality", "architecture", "security"}
    found = {p.parent.name for p in MODES_SRC.rglob("pack.md")}
    missing = required - found
    if missing:
        record("16 mode packs", "FAIL", f"missing packs: {missing}")
        return
    for pack in MODES_SRC.rglob("pack.md"):
        text = pack.read_text(encoding="utf-8")
        for section in ("## Rubric", "## Finding taxonomy", "## Auditor prompt fragment", "## Evidence requirements"):
            assert section in text, f"{pack.parent.name} missing {section}"
    import preflight
    import swarm_lib

    cfg_no_browser = json.loads((FIXTURE / "swarm.config.json").read_text(encoding="utf-8"))
    cfg_no_browser["mcp"]["browser"] = False
    alt = Path(tempfile.mkdtemp(prefix="swarm-nb-"))
    (alt / "swarm.config.json").write_text(json.dumps(cfg_no_browser), encoding="utf-8")
    swarm_lib._CONFIG_CACHE.clear()
    config2 = swarm_lib.load_config(alt)
    checks = {c["name"]: c["ok"] for c in preflight.run_checks(config2, False)}
    swarm_lib._CONFIG_CACHE.clear()
    shutil.rmtree(alt, ignore_errors=True)
    assert checks.get("ui_mode_requires_browser") is False
    record("16 mode packs", "PASS")


def t17_stop_correlation_documented_payload():
    import swarm_lib
    from registry import reduce_events

    events = [
        {"event": "spawn", "packet_path": "p/x.json", "subagent_type": "swarm-planner", "status": "allowed"},
        {"event": "stop", "packet_path": "p/x.json", "status": "completed"},  # no subagent_id anywhere
    ]
    reduced = reduce_events(events)
    assert reduced["pkt:p/x.json"]["status"] == "completed"
    assert swarm_lib.extract_packet_path("do thing.\npacket_path: p/x.json\nmore") == "p/x.json"
    record("17 stop correlation (no subagent_id)", "PASS")


def t18_journal_crash():
    import journal
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    entry = journal.Entry(run_dir, "ct:crash:1", {"action": "create"})
    entry.open()  # crash: never completed
    incomplete = journal.incomplete(run_dir)
    assert any(r["idempotency_key"] == "ct:crash:1" for r in incomplete)
    journal.mark_reconciled(run_dir, "ct:crash:1", "verified absent externally", False)
    assert not any(r["idempotency_key"] == "ct:crash:1" for r in journal.incomplete(run_dir))
    with journal.journaled(run_dir, "ct:ok:1", {"action": "create"}) as e:
        e.complete(external_id="issue-9")
    import tracker

    r1 = tracker.act(config, run_dir, "create", {"id": "dup-1", "title": "x"}, "ct:dup:1")
    r2 = tracker.act(config, run_dir, "create", {"id": "dup-1", "title": "x"}, "ct:dup:1")
    assert r2.get("stdout_tail") == "journal-replay", "retry duplicated the create"
    assert r1["ok"] and r2["ok"]
    record("18 journal crash safety", "PASS")


def t19_quarantine():
    import swarm_lib
    import worktree

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    allocs = json.loads(worktree.allocations_path(run_dir).read_text(encoding="utf-8"))
    assert "ct-wt1" in allocs
    wt = Path(allocs["ct-wt1"]["path"])
    (wt / "dirty.txt").write_text("crash artifact", encoding="utf-8")
    res = worktree.quarantine(config, run_dir, "ct-wt1")
    assert res["ok"], res
    q = Path(res["quarantined"])
    assert q.exists() and (q / "dirty.txt").exists(), "evidence lost"
    allocs = json.loads(worktree.allocations_path(run_dir).read_text(encoding="utf-8"))
    assert allocs["ct-wt1"].get("quarantined")
    shutil.rmtree(q, ignore_errors=True)
    sh(["git", "worktree", "prune"], FIXTURE)
    record("19 quarantine", "PASS")


def t20_gate_timeout():
    import gates

    start = time.monotonic()
    receipt = gates.run_gate({"id": "hang", "cmd": f'"{sys.executable}" -c "import time; time.sleep(60)"', "timeout_s": 2}, FIXTURE)
    elapsed = time.monotonic() - start
    assert receipt["exit_code"] == 124 and receipt["timeout"] is True
    assert elapsed < 30, f"kill took {elapsed}s"
    record("20 gate timeout kill", "PASS")


def t21_concurrent_admission():
    import admission
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    (run_dir / "reservations.json").unlink(missing_ok=True)
    results = []

    def attempt(key):
        ok, reason = admission.try_reserve(run_dir, "swarm-implementer", key, 0, config["caps"]["max_writers"] - 1)
        results.append(ok)

    threads = [threading.Thread(target=attempt, args=(f"pkt:race-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1, f"race admitted {results.count(True)} writers into 1 slot"
    (run_dir / "reservations.json").unlink(missing_ok=True)
    record("21 concurrent admission", "PASS")


def t22_dependency_waves():
    import conflict_graph
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    tasks = [
        {"id": "late", "allowed_paths": ["src/x/"], "depends_on": ["early"], "severity": "security"},
        {"id": "early", "allowed_paths": ["src/y/"], "depends_on": [], "severity": "minor"},
    ]
    waves = conflict_graph.build_waves(config, tasks)
    flat = [tid for wave in waves for tid in wave]
    assert flat.index("early") < flat.index("late"), "dependency order violated despite severity"
    try:
        conflict_graph.build_waves(config, [{"id": "a", "allowed_paths": [], "depends_on": ["b"]},
                                            {"id": "b", "allowed_paths": [], "depends_on": ["a"]}])
        raise AssertionError("cycle not rejected")
    except ValueError as exc:
        assert "dependency_cycle" in str(exc)
    record("22 dependency waves", "PASS")


def t23_fixture_configs():
    import swarm_lib

    base = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    node = json.loads(json.dumps(base))
    node["project"] = {"name": "node-mono", "stack": "Node 22 monorepo, pnpm, vitest"}
    node["requirements"]["mode"] = "code_contracts"
    node["requirements"]["roots"] = []
    node["gates"]["commands"] = [{"id": "test", "cmd": "pnpm test", "timeout_s": 900, "required": True}]
    alt = Path(tempfile.mkdtemp(prefix="swarm-node-"))
    (alt / "swarm.config.json").write_text(json.dumps(node), encoding="utf-8")
    swarm_lib._CONFIG_CACHE.clear()
    cfg_loaded = swarm_lib.load_config(alt)
    import intake

    idx = intake.build_index(cfg_loaded, None)
    assert idx["mode"] == "code_contracts" and idx["waiver"]
    none_cfg = json.loads(json.dumps(base))
    none_cfg["requirements"]["mode"] = "none"
    (alt / "swarm.config.json").write_text(json.dumps(none_cfg), encoding="utf-8")
    swarm_lib._CONFIG_CACHE.clear()
    idx = intake.build_index(swarm_lib.load_config(alt), None)
    assert idx["mode"] == "none" and not idx["rows"]
    empty_doc = json.loads(json.dumps(base))
    empty_doc["requirements"]["mode"] = "documented"
    empty_doc["requirements"]["roots"] = ["no-such-docs"]
    (alt / "no-such-docs").mkdir()
    (alt / "swarm.config.json").write_text(json.dumps(empty_doc), encoding="utf-8")
    swarm_lib._CONFIG_CACHE.clear()
    try:
        intake.build_index(swarm_lib.load_config(alt), None)
        raise AssertionError("empty documented index did not fail loudly")
    except ValueError:
        pass
    swarm_lib._CONFIG_CACHE.clear()
    swarm_lib.load_config(FIXTURE)
    shutil.rmtree(alt, ignore_errors=True)
    record("23 fixture configs", "PASS")


def t24_budget_exhaustion():
    import budgets
    import swarm_lib

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    ok = True
    for _ in range(config["budgets"]["per_task"]["replans"]):
        ok, _n = budgets.consume_task(run_dir, config, "ct-task", "replans")
        assert ok
    ok, _n = budgets.consume_task(run_dir, config, "ct-task", "replans")
    assert not ok, "replans over budget allowed"
    state = budgets.load(run_dir, config)
    state["run"]["agents_spawned"] = state["run"]["max_agents"]
    budgets.save(run_dir, state)
    ok, _n = budgets.count_agent(run_dir, config)
    assert not ok, "agent budget exhaustion not enforced"
    state["run"]["agents_spawned"] = 0
    budgets.save(run_dir, state)
    import run_state

    st = run_state.load_state(run_dir)
    assert st["status"] == "running", "state must stay resumable after budget stop"
    record("24 budget exhaustion", "PASS")


def t25_ui_blocked_infrastructure():
    from validate_payload import validate

    payload = {"verdict": "NEEDS_EVIDENCE", "disposition": "BLOCKED_INFRASTRUCTURE",
               "checks_run": ["boot"], "findings": [],
               "app_boot_receipt": {"url": "http://localhost:5601/", "status": "connection_refused", "output": "..."},
               "failure_class": "app_boot", "missing_evidence": ["journey walk"]}
    assert not validate("ui_review", payload), validate("ui_review", payload)
    bad = dict(payload, verdict="PASS")
    assert any("blocked_infrastructure_requires_needs_evidence" in e for e in validate("ui_review", bad))
    record("25 ui blocked-infrastructure", "PASS")


def t26_install_guard_live_run():
    if not SOURCE_ROOT or not (SOURCE_ROOT / "install.py").exists():
        record("26 install guards", "SKIP", "source layout unavailable")
        return
    sys.path.insert(0, str(SOURCE_ROOT))
    import install as installer

    # FIXTURE has a live run (status running) → upgrade refused
    assert installer.run_is_live(FIXTURE) is True
    rc = installer.install(FIXTURE, skip_checks=True)
    assert rc == 1, "install proceeded during live run"
    # rollback preserves user-modified files: simulate via Transaction
    target = Path(tempfile.mkdtemp(prefix="swarm-tx-"))
    try:
        user_file = target / "user.txt"
        user_file.write_text("user content", encoding="utf-8")
        tx = installer.Transaction(target)
        tx.write(user_file, b"overwritten")
        tx.write(target / "new.txt", b"new")
        tx.rollback()
        assert user_file.read_text(encoding="utf-8") == "user content"
        assert not (target / "new.txt").exists()
    finally:
        shutil.rmtree(target, ignore_errors=True)
    record("26 install guards", "PASS")


def t27_artifact_registry():
    from validate_payload import REGISTRY, registry_kinds

    kinds = registry_kinds()
    assert len(kinds) >= 20, kinds
    for kind in kinds:
        spec = REGISTRY[kind]
        for field in ("version", "producer", "consumer", "required"):
            assert field in spec, f"{kind} missing {field}"
    from validate_payload import validate

    assert validate("not_a_registered_kind", {}) == ["unknown payload kind: not_a_registered_kind"]
    record("27 artifact registry", "PASS")


def t28_tier_flag():
    import run_state
    import swarm_lib
    from validate_payload import validate

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)
    state = run_state.load_state(run_dir)
    state["tier"] = "flat"
    run_state.persist(run_dir, state)
    assert run_state.load_state(run_dir)["tier"] == "flat"
    bad = dict(state, tier="pyramid")
    assert any("illegal_tier" in e for e in validate("run_state", bad))
    record("28 tier flag", "PASS")


def t29_redteam_fix_pack():
    """Regression pack for red-team findings F01-F14."""
    import swarm_lib
    from validate_payload import validate

    config = swarm_lib.load_config(FIXTURE)
    run_dir = fixture_run_dir(config)

    # F01: stage fragments are distinct admission keys; file part still validates
    import registry as reg

    ev = [
        {"event": "spawn", "packet_path": "packets/owner-T1.json#plan-1", "subagent_type": "swarm-planner", "status": "allowed", "at": "2026-01-01T00:00:00+00:00"},
        {"event": "spawn", "packet_path": "packets/owner-T1.json#implement-1", "subagent_type": "swarm-implementer", "status": "allowed", "at": "2026-01-01T00:01:00+00:00"},
        {"event": "stop", "packet_path": "packets/owner-T1.json#plan-1", "status": "completed", "at": "2026-01-01T00:02:00+00:00"},
    ]
    reduced = reg.reduce_events(ev)
    assert len(reduced) == 2, "fragmented packet paths must be distinct keys"
    assert reduced["pkt:packets/owner-T1.json#plan-1"]["status"] == "completed"
    assert reduced["pkt:packets/owner-T1.json#implement-1"]["status"] == "running"

    # F02: replan_required -> pending demands a NEW plan hash; delta closes the loop
    import run_state

    run_state.task_transition(run_dir, "t29", "coursing", plan_sha256="hash-a")
    run_state.task_transition(run_dir, "t29", "replan_required")
    for stamps in ({}, {"plan_sha256": "hash-a"}):
        try:
            run_state.task_transition(run_dir, "t29", "pending", **stamps)
            raise AssertionError(f"same/missing plan hash accepted: {stamps}")
        except ValueError:
            pass
    run_state.task_transition(run_dir, "t29", "pending", plan_sha256="hash-b")
    # delta_done blocks re-implementation even where TASK_FORWARD would allow it
    state = run_state.load_state(run_dir)
    state["tasks"]["t29"] = {"state": "pending", "delta_done": True, "plan_sha256": "hash-b"}
    run_state.persist(run_dir, state)
    try:
        run_state.task_transition(run_dir, "t29", "coursing", plan_sha256="hash-b")
        raise AssertionError("re-implement after delta allowed")
    except ValueError:
        pass
    # ...but an accepted new-hash replan resets the delta lock and opens a fresh course
    state = run_state.load_state(run_dir)
    state["tasks"]["t29"] = {"state": "replan_required", "delta_done": True, "plan_sha256": "hash-b"}
    run_state.persist(run_dir, state)
    run_state.task_transition(run_dir, "t29", "pending", plan_sha256="hash-c")
    run_state.task_transition(run_dir, "t29", "coursing", plan_sha256="hash-c")
    assert run_state.load_state(run_dir)["tasks"]["t29"]["state"] == "coursing"

    # F03: audit_payload wrapper validates, including nested rows
    good = {"coverage_rows": [{"requirement_id": "US-1", "classification": "gap", "evidence_refs": ["a:1"], "needs_human": False}],
            "findings": [{"mode": "security", "requirement_ids": ["US-1"], "issue": "x", "impact": "y",
                          "offending_lines": ["src/a.py:1-2"], "affected_files": ["src/a.py"], "affected_features": ["US-1"],
                          "evidence_level": "observed", "evidence_refs": ["src/a.py:1"], "gap_class": "missing-check",
                          "symbol": "A.b", "remediation_class": "add-check", "fingerprint": "",
                          "proposed_minimal_fix": "add", "confidence": 0.9, "severity": "major"}]}
    assert not validate("audit_payload", good), validate("audit_payload", good)
    bad = {"coverage_rows": [{"requirement_id": "US-1"}], "findings": []}
    assert any("coverage_rows[0]" in e for e in validate("audit_payload", bad))

    # F12: plan rounds = revises + 1 initial
    import loop_breaker as lb

    caps_cfg = {"plan_revise_rounds": 2}
    assert not lb.course_round_check({"plan": 3, "impl": 1, "delta": 1}, caps_cfg)
    assert "plan_rounds_over_budget" in lb.course_round_check({"plan": 4, "impl": 1, "delta": 1}, caps_cfg)

    # F14: string-typed hooks.json entry survives merge
    if SOURCE_ROOT and (SOURCE_ROOT / "install.py").exists():
        sys.path.insert(0, str(SOURCE_ROOT))
        import install as installer

        merged = installer.merge_hooks_json(
            {"hooks": {"subagentStart": "python legacy.py"}},
            {"swarm_audit_version": 1, "hooks": {"subagentStart": [{"command": "python swarm.py"}]}},
        )
        entries = merged["hooks"]["subagentStart"]
        assert {"command": "python legacy.py"} in entries and {"command": "python swarm.py"} in entries, entries

    # F06: journal reconcile auto-resolves declared path checks
    import journal

    probe = run_dir / "reconcile-probe.txt"
    probe.write_text("x", encoding="utf-8")
    journal.Entry(run_dir, "ct:t29:wt", {"action": "worktree-create", "check": "path_exists", "path": str(probe)}).open()
    journal.Entry(run_dir, "ct:t29:ext", {"action": "tracker-create"}).open()
    result = journal.reconcile(run_dir)
    assert any(r["key"] == "ct:t29:wt" and r["landed"] for r in result["resolved"]), result
    assert any(r["idempotency_key"] == "ct:t29:ext" for r in result["needs_review"]), result
    journal.mark_reconciled(run_dir, "ct:t29:ext", "checked externally", False)
    probe.unlink()

    # F07 delta: create failure AFTER port allocation releases the global port
    import worktree as wt_fail

    real_dump = wt_fail.dump_json
    allocs_file = wt_fail.allocations_path(run_dir)

    def exploding_dump(path, data):
        if Path(path) == allocs_file and any(
            isinstance(v, dict) and v.get("path") for k, v in data.items() if k == "t29-boom"
        ):
            raise OSError("simulated crash writing allocation")
        real_dump(path, data)

    wt_fail.dump_json = exploding_dump
    try:
        wt_fail.create(config, run_dir, "t29-boom", "HEAD")
        raise AssertionError("simulated crash did not propagate")
    except OSError:
        pass
    finally:
        wt_fail.dump_json = real_dump
    gpath_now = wt_fail.global_ports_path()
    if gpath_now.exists():
        leaked = [m for m in json.loads(gpath_now.read_text(encoding="utf-8")).values() if m.get("task") == "t29-boom"]
        assert not leaked, f"port leaked on create failure: {leaked}"
    sh(["git", "worktree", "remove", "--force", str(wt_fail.worktrees_dir(config) / f"{run_dir.name}-t29-boom")], FIXTURE)
    sh(["git", "branch", "-D", f"swarm/{run_dir.name}/t29-boom"], FIXTURE)

    # F05: merge commits NEW files but never the seeded env file
    import worktree as wt_mod_merge

    res = wt_mod_merge.create(config, run_dir, "t29-merge", "HEAD")
    assert res["ok"], res
    wt_path = Path(res["path"])
    (wt_path / "src").mkdir(exist_ok=True)
    (wt_path / "src" / "test_new_file.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (wt_path / "src" / "__pycache__").mkdir(exist_ok=True)
    (wt_path / "src" / "__pycache__" / "junk.cpython-313.pyc").write_text("bytecode", encoding="utf-8")
    merged_res = wt_mod_merge.merge(config, run_dir, "t29-merge")
    assert merged_res["ok"], merged_res
    show = sh(["git", "show", "HEAD:src/test_new_file.py"], FIXTURE)
    assert show.returncode == 0, "new test file missing from merge commit"
    env_name = config["worktree"].get("env_file", ".worktree-env")
    env_show = sh(["git", "show", f"HEAD:{env_name}"], FIXTURE)
    assert env_show.returncode != 0, "seeded env file leaked into the merge"
    pyc_show = sh(["git", "show", "HEAD:src/__pycache__/junk.cpython-313.pyc"], FIXTURE)
    assert pyc_show.returncode != 0, "build artifact leaked into the merge"
    wt_mod_merge.remove(config, run_dir, "t29-merge")

    # F07: allocated ports are bind-probed and unique across a busy socket
    import socket as _socket

    import worktree as wt_mod

    gpath = wt_mod.global_ports_path()
    saved = gpath.read_text(encoding="utf-8") if gpath.exists() else None
    try:
        if gpath.exists():
            gpath.unlink()
        low = config["worktree"]["port_range"][0] if "worktree" in config and "port_range" in config.get("worktree", {}) else 5600
        blocker = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", low))
        blocker.listen(1)
        try:
            port = wt_mod.allocate_port(config, run_dir, "t29-port")
            assert port != low, "allocated a port that is already bound"
        finally:
            blocker.close()
        wt_mod.release_port(run_dir, "t29-port")
    finally:
        if saved is not None:
            gpath.write_text(saved, encoding="utf-8")
        elif gpath.exists():
            gpath.unlink()

    record("29 red-team fix pack", "PASS")


TESTS = [
    t01_config_schema, t02_agent_frontmatter, t03_payload_validation, t04_fingerprint,
    t05_loop_breaker, t06_atomicity, t07_conflict_shared_paths, t08_admission_caps,
    t09_run_state, t10_course_verify, t11_hooks, t12_worktree, t13_tracker, t14_installer,
    t15_gates_dry_run, t16_mode_packs, t17_stop_correlation_documented_payload,
    t18_journal_crash, t19_quarantine, t20_gate_timeout, t21_concurrent_admission,
    t22_dependency_waves, t23_fixture_configs, t24_budget_exhaustion,
    t25_ui_blocked_infrastructure, t26_install_guard_live_run, t27_artifact_registry,
    t28_tier_flag, t29_redteam_fix_pack,
]


def main() -> int:
    try:
        setup_fixture()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL contract_tests: fixture setup: {exc}")
        return 1
    for test in TESTS:
        name = test.__name__
        try:
            test()
        except AssertionError as exc:
            record(name, "FAIL", str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            record(name, "FAIL", f"{type(exc).__name__}: {str(exc)[:300]}")
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    for item, status, detail in RESULTS:
        print(f"{status:5} {item}" + (f" — {detail}" if detail else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed"
          + (f", {sum(1 for r in RESULTS if r[1] == 'SKIP')} skipped" if any(r[1] == "SKIP" for r in RESULTS) else ""))
    shutil.rmtree(FIXTURE, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
