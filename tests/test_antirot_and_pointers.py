"""Tests for anti-rot freshness verification, source_ref pointers, and L1 anti-bloat compaction."""

from __future__ import annotations

from pathlib import Path

from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.freshness import check_source_freshness, compute_doc_digest
from fos.ingest import collect_hindsight_events
from fos.proposals import scan_proposals
from fos.tokens import estimate_tokens


def test_source_ref_drift_detection_recall_penalty_and_proposal(tmp_path: Path) -> None:
    doc_dir = tmp_path / "demo-proj"
    (doc_dir / "docs").mkdir(parents=True)
    design_doc = doc_dir / "docs" / "DESIGN.md"
    design_doc.write_text(
        "# 架构设计\n\n## 热路径约束\n网关热路径必须零数据库查询、全内存快照读取，禁止在转发链路连接 PostgreSQL。\n",
        encoding="utf-8",
    )

    events = collect_hindsight_events("docs", root_dir=doc_dir)
    assert len(events) == 1
    meta = events[0]["meta"]
    assert meta["source_ref"].startswith(str(design_doc.resolve()))
    assert meta["source_hash"] == compute_doc_digest(design_doc)

    engine = BrainEngine(tmp_path / "brain")
    res = engine.add_atom(
        kind="decision",
        scope="demo-proj",
        source=events[0]["source"],
        content=events[0]["raw"],
        meta=meta,
    )
    node_id = res["node"]["id"]
    assert res["node"]["source_ref"] == meta["source_ref"]

    # 1. Initially fresh
    assert check_source_freshness(meta["source_ref"], meta["source_hash"]) == "fresh"
    rec_fresh = engine.recall("网关热路径 零数据库", scope="demo-proj")
    assert rec_fresh["results"][0]["id"] == node_id
    assert rec_fresh["results"][0]["freshness"] == "fresh"
    assert rec_fresh["results"][0]["l2_hint"].startswith("source: ")
    fresh_score = rec_fresh["results"][0]["score"]

    # 2. Modify authoritative file -> deterministically detected as `drifted` + recall penalty
    design_doc.write_text(
        "# 架构设计\n\n## 热路径约束\n网关热路径已改用本地 SQLite 缓存，详见最新架构图。\n",
        encoding="utf-8",
    )
    assert check_source_freshness(meta["source_ref"], meta["source_hash"]) == "drifted"
    rec_drifted = engine.recall("网关热路径 零数据库", scope="demo-proj")
    assert rec_drifted["results"][0]["freshness"] == "drifted"
    assert rec_drifted["results"][0]["score"] < fresh_score

    dash = engine.dashboard_summary()
    assert dash["hygiene"]["pointer_nodes"] == 1
    assert dash["hygiene"]["drifted_nodes"] == 1
    assert dash["hygiene"]["broken_ref_nodes"] == 0

    # 3. Delete authoritative file -> `broken_ref` + severe recall penalty + 0-day retire_stale proposal
    design_doc.unlink()
    assert check_source_freshness(meta["source_ref"], meta["source_hash"]) == "broken_ref"
    rec_broken = engine.recall("网关热路径 零数据库", scope="demo-proj")
    assert rec_broken["results"][0]["freshness"] == "broken_ref"
    assert rec_broken["results"][0]["score"] < rec_drifted["results"][0]["score"]

    scan = scan_proposals(engine, root_dir=tmp_path / "no_repos")
    assert any(
        p["kind"] == "retire_stale" and node_id in p["target_node_ids"]
        for p in scan["new_proposals"]
    )


def test_dream_anti_bloat_clamps_giant_hindsight_docs_while_preserving_l2(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    doc_file = tmp_path / "ARCHITECTURE.md"
    giant_body = "网关热路径必须保持零拷贝、无锁并发、非阻塞异步转发并避免构建完整对象树；" * 30
    doc_file.write_text(f"# 架构\n\n{giant_body}\n", encoding="utf-8")

    engine.add_atom(
        kind="decision",
        scope="gateway-app",
        source="hindsight:docs:gateway-app",
        content=f"Hindsight: [ARCHITECTURE.md · 架构]\n{giant_body}",
        meta={
            "severity": "P1",
            "source_ref": f"{doc_file.resolve()}#架构",
            "source_hash": compute_doc_digest(doc_file),
        },
    )

    res = run_dream(engine, no_gate=True)
    assert res["promoted"] is True

    with engine.snapshots.open_current(read_only=True) as db:
        nodes = db.list_nodes(as_of=None)
    assert len(nodes) == 1
    n = nodes[0]
    assert estimate_tokens(n.rule) <= 95
    assert n.source_ref == f"{doc_file.resolve()}#架构"
    # L2 ledger still retains full verbatim evidence
    detail = engine.get(n.id, tier="L2")
    assert len(detail["evidence"][0]["raw"]) > len(n.rule)
