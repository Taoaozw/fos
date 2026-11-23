#!/usr/bin/env python3
"""End-to-end A/B benchmark runner evaluating real `codex exec` with vs. without FlowOS.

Measures:
- Arm A (Control): Stock `codex exec` with empty `FOS_HOME` (no FlowOS memories).
- Arm B (Treatment): `codex exec` augmented with FlowOS L0+L1 pointer-first context
  (or autonomous `fos` CLI invocation / isolated anti-rot sandbox).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Allow running directly via `python3 scripts/run_codex_eval.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fos.engine import BrainEngine  # noqa: E402
from fos.freshness import compute_doc_digest  # noqa: E402
from fos.output import compact_source_ref  # noqa: E402
from fos.scopes import detect_named_repo_scopes  # noqa: E402
from fos.tokens import estimate_tokens  # noqa: E402


@dataclass
class ArmResult:
    passed: bool
    latency_s: float
    exit_code: int
    context_tokens: int
    missing_patterns: list[str]
    forbidden_hits: list[str]
    response: str


@dataclass
class CaseReport:
    id: str
    category: str
    mode: str
    scope: str | None
    arm_a: ArmResult
    arm_b: ArmResult


def _grade(text: str, must_match_all: list[str], must_not_match: list[str]) -> tuple[bool, list[str], list[str]]:
    missing: list[str] = []
    for pat in must_match_all:
        if not re.search(pat, text, re.IGNORECASE | re.DOTALL | re.MULTILINE):
            missing.append(pat)
    forbidden: list[str] = []
    for pat in must_not_match:
        if re.search(pat, text, re.IGNORECASE | re.DOTALL | re.MULTILINE):
            forbidden.append(pat)
    return (not missing and not forbidden), missing, forbidden


def _run_codex(prompt: str, *, env_overrides: dict[str, str], timeout_s: int = 90) -> tuple[int, str, float]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    env.update(env_overrides)
    with tempfile.TemporaryDirectory(prefix="fos-codex-eval-") as tmp_dir:
        out_file = Path(tmp_dir) / "last.txt"
        argv = [
            "codex",
            "exec",
            "-s",
            "read-only",
            "-C",
            tmp_dir,
            "--skip-git-repo-check",
            "--ephemeral",
            "-o",
            str(out_file),
            prompt,
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                check=False,
            )
            dt = round(time.perf_counter() - t0, 2)
            text = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else proc.stdout.strip()
            return proc.returncode, text, dt
        except subprocess.TimeoutExpired:
            return 124, "[TIMEOUT]", round(time.perf_counter() - t0, 2)


def _format_fos_context(engine: BrainEngine, *, scope: str | None, recall_query: str) -> tuple[str, int, list[str]]:
    mounted = detect_named_repo_scopes(scope) or None
    boot = engine.boot(project=scope, scopes=mounted, budget=300)
    # Only skip L1 re-emission for L0 entries whose full rule already fit verbatim (no '…' truncation) and have no source_ref pointer needed
    complete_boot_ids = [
        str(e["id"])
        for e in (boot.get("entries") or [])
        if e.get("id") and "…" not in str(e.get("l0", ""))
    ]
    # Keep complete_boot_ids only when they don't hide a source_ref pointer
    exclude_for_recall: list[str] = []
    if complete_boot_ids:
        with engine.snapshots.open_current(read_only=True) as db:
            for bid in complete_boot_ids:
                bn = db.get_node(bid)
                if bn is not None and not bn.source_ref and estimate_tokens(bn.rule) <= 28:
                    exclude_for_recall.append(bid)

    rec = engine.recall(
        recall_query,
        scope=scope,
        scopes=mounted,
        budget=900,
        limit=8,
        exclude_ids=exclude_for_recall or None,
    )
    recalled_ids = [str(item["id"]) for item in (rec.get("results") or []) if item.get("id")]

    lines = [
        "以下是 FlowOS (`fos`) 已沉淀规则与上下文（权威参考；若标记 `broken_ref` 表示源文件已删除、规则已失效，请勿盲从）：",
        boot.get("kernel_text", "").strip(),
    ]
    hits = rec.get("results") or []
    if hits:
        lines.append(f"# FOS L1 (scope={scope or 'global'}, {len(hits)} hits)")
        for item in hits:
            fresh = item.get("freshness", "")
            fresh_tag = f"|{fresh}" if fresh in ("drifted", "broken_ref") else ""
            sc = item.get("scope", "")
            sc_tag = f"|{sc}" if (sc and sc != (scope or "global") and not (sc == "global" and item.get("type") == "principle")) else ""
            lines.append(f"[{item['id']}|{item['severity']}{sc_tag}{fresh_tag}] {item['rule']}")
            if item.get("source_ref"):
                lines.append(f"  ref: {compact_source_ref(item['source_ref'])}")
    block = "\n".join(lines).strip()
    return block, estimate_tokens(block), recalled_ids


def _build_sandbox_antirot_context(scope: str, recall_query: str) -> tuple[str, int]:
    """Create an isolated temporary brain with a superseded decision + broken_ref stale doc."""
    with tempfile.TemporaryDirectory(prefix="fos-sandbox-brain-") as tmp:
        root = Path(tmp)
        eng = BrainEngine(root / "brain")

        # 1. Superseded decision: Redux -> TanStack Query
        old = eng.add_atom(
            kind="decision",
            scope=scope,
            source="manual",
            content="前端服务端数据缓存使用 Redux Toolkit。",
            meta={"severity": "P1"},
        )["node"]["id"]
        eng.correct_node(
            old,
            "前端服务端数据缓存统一改用 TanStack Query，彻底废弃 Redux Toolkit。",
            reason="减少样板代码并原生支持 keepPreviousData 与缓存失效",
        )

        # 2. Broken-ref doc card: file deleted after capture -> freshness="broken_ref"
        doc_path = root / "docs" / "legacy-date.md"
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text("# 日期规范\n\n前端日期格式化统一使用 Moment.js。\n", encoding="utf-8")
        digest = compute_doc_digest(doc_path)
        eng.add_atom(
            kind="decision",
            scope=scope,
            source=f"hindsight:docs:{scope}",
            content="前端日期格式化统一使用 Moment.js。",
            meta={
                "severity": "P1",
                "source_ref": f"{doc_path.resolve()}#日期规范",
                "source_hash": digest,
            },
        )
        # Simulate deletion of authoritative doc (rot!)
        doc_path.unlink()

        return _format_fos_context(eng, scope=scope, recall_query=recall_query)[:2]


def _evaluate_case(
    case: dict[str, Any],
    brain_dir: Path,
    empty_fos_home: Path,
    *,
    record_feedback: bool = False,
) -> CaseReport:
    cid = case["id"]
    mode = case["mode"]
    scope = case.get("scope")
    prompt = case["prompt"]
    must_match = list(case.get("must_match_all") or [])
    must_not = list(case.get("must_not_match") or [])

    # --- Arm A: Baseline Codex (strictly isolated from ~/.fos) ---
    base_env = {"FOS_HOME": str(empty_fos_home)}
    if mode == "autonomous_cli":
        prompt_a = prompt
    else:
        prompt_a = f"请直接用简体中文简洁回答以下工程问题（不要调用外部工具）：\n\n{prompt}"

    rc_a, resp_a, dt_a = _run_codex(prompt_a, env_overrides=base_env)
    ok_a, miss_a, forb_a = _grade(resp_a, must_match, must_not)
    arm_a = ArmResult(
        passed=ok_a and rc_a == 0,
        latency_s=dt_a,
        exit_code=rc_a,
        context_tokens=0,
        missing_patterns=miss_a,
        forbidden_hits=forb_a,
        response=resp_a,
    )

    # --- Arm B: FlowOS-Augmented Codex ---
    recalled_ids: list[str] = []
    if mode == "autonomous_cli":
        ctx_tokens = 0
        env_b = {"FOS_HOME": str(brain_dir.parent)}
        rc_b, resp_b, dt_b = _run_codex(prompt, env_overrides=env_b)
    else:
        if mode == "sandbox_antirot":
            ctx_block, ctx_tokens = _build_sandbox_antirot_context(scope or "sandbox-web", case["recall_query"])
        else:
            eng = BrainEngine(brain_dir)
            ctx_block, ctx_tokens, recalled_ids = _format_fos_context(
                eng, scope=scope, recall_query=case["recall_query"]
            )
        prompt_b = (
            f"{ctx_block}\n\n"
            f"---\n"
            f"请结合以上 FlowOS 已沉淀的个人原则与项目规则，直接用简体中文简洁回答以下工程问题（不要调用外部工具）：\n\n"
            f"{prompt}"
        )
        rc_b, resp_b, dt_b = _run_codex(prompt_b, env_overrides=base_env)

    ok_b, miss_b, forb_b = _grade(resp_b, must_match, must_not)
    if record_feedback and mode == "ab_memory" and recalled_ids:
        from fos.freshness import record_utility_feedback

        verdict = "useful" if (ok_b and rc_b == 0) else "noise"
        for nid in recalled_ids[:3]:
            record_utility_feedback(brain_dir, nid, verdict, context=f"Codex eval {cid}")
    arm_b = ArmResult(
        passed=ok_b and rc_b == 0,
        latency_s=dt_b,
        exit_code=rc_b,
        context_tokens=ctx_tokens,
        missing_patterns=miss_b,
        forbidden_hits=forb_b,
        response=resp_b,
    )

    return CaseReport(
        id=cid,
        category=case["category"],
        mode=mode,
        scope=scope,
        arm_a=arm_a,
        arm_b=arm_b,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end Codex A/B evaluation suite for FlowOS.")
    parser.add_argument(
        "--suite",
        type=Path,
        default=REPO_ROOT / "evals" / "codex_benchmark.json",
        help="Path to benchmark JSON file",
    )
    parser.add_argument(
        "--brain-dir",
        type=Path,
        default=Path.home() / ".fos" / "brain",
        help="FlowOS brain directory",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel Codex workers")
    parser.add_argument("--filter", type=str, default="", help="Optional case ID substring filter")
    parser.add_argument("--record-feedback", action="store_true", help="Record PlugMem utility feedback back to brain")
    parser.add_argument("--out", type=Path, default=None, help="Optional JSON report output path")
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    cases = suite["cases"]
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c["id"].lower() or args.filter.lower() in c["category"].lower()]
    if not cases:
        print("No cases matched filter.", file=sys.stderr)
        return 2

    t_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fos-empty-home-") as empty_dir:
        empty_home = Path(empty_dir)
        reports_by_id: dict[str, CaseReport] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {
                pool.submit(
                    _evaluate_case,
                    c,
                    args.brain_dir,
                    empty_home,
                    record_feedback=args.record_feedback,
                ): c["id"]
                for c in cases
            }
            for fut in concurrent.futures.as_completed(futs):
                rep = fut.result()
                reports_by_id[rep.id] = rep
                mark_a = "PASS" if rep.arm_a.passed else "FAIL"
                mark_b = "PASS" if rep.arm_b.passed else "FAIL"
                print(
                    f"[{rep.id}] {rep.category:20s} | Base={mark_a} ({rep.arm_a.latency_s:4.1f}s) "
                    f"| +FOS={mark_b} ({rep.arm_b.latency_s:4.1f}s, ctx={rep.arm_b.context_tokens}t)",
                    flush=True,
                )

    ordered = [reports_by_id[c["id"]] for c in cases]
    total = len(ordered)
    pass_a = sum(1 for r in ordered if r.arm_a.passed)
    pass_b = sum(1 for r in ordered if r.arm_b.passed)
    mem_tokens = [r.arm_b.context_tokens for r in ordered if r.arm_b.context_tokens > 0]
    avg_ctx = round(sum(mem_tokens) / len(mem_tokens), 1) if mem_tokens else 0.0
    elapsed = round(time.perf_counter() - t_start, 1)

    by_cat: dict[str, dict[str, int]] = {}
    for r in ordered:
        b = by_cat.setdefault(r.category, {"total": 0, "pass_a": 0, "pass_b": 0})
        b["total"] += 1
        b["pass_a"] += int(r.arm_a.passed)
        b["pass_b"] += int(r.arm_b.passed)

    summary = {
        "suite": str(args.suite),
        "total_cases": total,
        "baseline_pass": pass_a,
        "baseline_rate": round(pass_a / total, 4),
        "fos_pass": pass_b,
        "fos_rate": round(pass_b / total, 4),
        "lift_pct_points": round((pass_b - pass_a) / total * 100.0, 1),
        "avg_fos_context_tokens": avg_ctx,
        "elapsed_s": elapsed,
        "by_category": by_cat,
        "cases": [asdict(r) for r in ordered],
    }

    print("\n=== Codex End-to-End A/B Benchmark Summary ===")
    print(f"Baseline Codex (no fos): {pass_a}/{total} ({summary['baseline_rate']*100:.1f}%)")
    print(f"Codex + FlowOS (v0007):  {pass_b}/{total} ({summary['fos_rate']*100:.1f}%)")
    print(f"Net Improvement (Lift):  +{summary['lift_pct_points']:.1f} pp")
    print(f"Avg FlowOS Context Cost: {avg_ctx} tokens | Total Elapsed: {elapsed}s")
    print("\nBy Dimension:")
    for cat, stats in by_cat.items():
        print(f"  - {cat:22s}: Baseline {stats['pass_a']}/{stats['total']} -> +FOS {stats['pass_b']}/{stats['total']}")

    failures_b = [r for r in ordered if not r.arm_b.passed]
    if failures_b:
        print("\n=== +FOS Failures (for diagnosis) ===")
        for r in failures_b:
            print(f"  - {r.id}: missing={r.arm_b.missing_patterns}, forbidden={r.arm_b.forbidden_hits}")
            print(f"    response={r.arm_b.response[:320]}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved full JSON report to {args.out}")

    return 0 if pass_b == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
