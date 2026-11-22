"""Tests for BrainEngine: ledger, sqlite-vec, rebuild, hybrid recall, bi-temporal graph, boot, get, mirror, export."""

from pathlib import Path

import pytest

from fos.engine import BrainEngine
from fos.errors import NotFoundError
from fos.models import GraphEdge


def test_ledger_sqlite_vec_and_rebuild(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain", device="m3max")
    res = engine.add_atom(
        kind="lesson",
        scope="fops",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持，token=sk-proj-1234567890abcdefghijklmnop",
        meta={"severity": "P0", "trigger_when": ["编写跨平台部署脚本", "在 macOS 调用 rsync"]},
    )
    assert res["redacted"] is True
    assert "sk-proj-" not in res["atom"]["raw"]
    node_id = res["node"]["id"]
    assert node_id == "LSN-001"

    # Verify sqlite-vec is active in this environment
    with engine.snapshots.open_current(read_only=True) as db:
        assert db.has_sqlite_vec is True

    # Delete all SQLite snapshots and rebuild solely from immutable ledger/*.jsonl
    for db_file in (tmp_path / "brain" / "snapshots").glob("*.db*"):
        db_file.unlink()
    rebuilt = engine.rebuild_from_ledger()
    assert rebuilt["atom_count"] == 1
    assert rebuilt["node_count"] == 1

    fetched = engine.get(node_id, tier="L2", include_graph=True)
    assert fetched["node"]["id"] == node_id
    assert len(fetched["evidence"]) == 1
    assert "openrsync" in fetched["evidence"][0]["raw"]
    assert any(e["edge_type"] == "APPLIES_TO" for e in fetched["graph"])


def test_bitemporal_supersession_and_as_of_time_travel(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    d1 = engine.add_atom(
        kind="decision",
        scope="fos",
        content="采用纯 Markdown 笔记库作为记忆存储引擎",
        ts="2026-08-01T10:00:00+08:00",
        meta={"trigger_when": ["记忆存储引擎选型"]},
    )
    dec1_id = d1["node"]["id"]
    assert dec1_id == "DEC-001"

    d2 = engine.add_atom(
        kind="decision",
        scope="fos",
        content="改用 SQLite WAL + FTS5 + sqlite-vec 作为 AI 原生记忆存储引擎，因为纯 Markdown 检索低效",
        ts="2026-10-01T10:00:00+08:00",
        meta={"supersedes": dec1_id, "trigger_when": ["记忆存储引擎选型"]},
    )
    dec2_id = d2["node"]["id"]
    assert d2["superseded"] == dec1_id

    # Default recall MUST NOT return superseded DEC-001
    cur = engine.recall("记忆存储引擎选型", scope="fos", budget=1200)
    cur_ids = [r["id"] for r in cur["results"]]
    assert dec2_id in cur_ids
    assert dec1_id not in cur_ids

    # Historical --as-of 2026-08 MUST return DEC-001 and NOT DEC-002
    hist = engine.recall("记忆存储引擎选型", scope="fos", as_of="2026-08", budget=1200)
    hist_ids = [r["id"] for r in hist["results"]]
    assert dec1_id in hist_ids
    assert dec2_id not in hist_ids

    # Graph shows SUPERSEDES edge
    g = engine.get(dec2_id, tier="L1", include_graph=True)
    assert any(e["edge_type"] == "SUPERSEDES" and e["dst_id"] == dec1_id for e in g["graph"])


def test_hybrid_recall_graph_expansion_and_token_budget(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    dec = engine.add_atom(
        kind="decision",
        scope="infra",
        content="生产发布通道使用 Tailscale 内网直连部署",
        meta={"severity": "P1"},
    )["node"]["id"]
    lsn = engine.add_atom(
        kind="lesson",
        scope="infra",
        content="严禁暴露公网 7622 端口，必须绑定 Tailscale 网卡并启用 SHA-256 Bearer 鉴权",
        meta={"severity": "P0", "caused_by": dec},
    )["node"]["id"]
    for i in range(12):
        engine.add_atom(
            kind="thought",
            scope="infra",
            content=f"部署观察笔记 #{i}: Tailscale 内网延迟稳定在 {i + 5}ms 以内",
        )

    # Recall with strict budget must never exceed budget
    rec = engine.recall("Tailscale 部署", scope="infra", budget=320)
    assert rec["used_tokens"] <= 320
    assert rec["count"] >= 1
    assert lsn in [r["id"] for r in rec["results"]]

    # Boot must respect <= 300 tokens and prioritize P0 redline
    boot = engine.boot(project="infra", budget=180)
    assert boot["used_tokens"] <= 180
    assert boot["entries"][0]["id"] == lsn


def test_mirror_checks_and_markdown_export(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(
        kind="principle",
        scope="fops",
        content="禁止在生产部署脚本中假设远端目录已存在或依赖 GNU rsync 专有参数",
        meta={"severity": "P0"},
    )
    engine.add_atom(
        kind="lesson",
        scope="fops",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持",
        meta={"severity": "P0", "trigger_when": ["macOS rsync 部署"]},
    )
    old_dec = engine.add_atom(
        kind="decision",
        scope="fops",
        content="使用 Go 纯静态编译打包所有存储扩展",
        ts="2026-07-01T10:00:00+08:00",
    )["node"]["id"]
    engine.add_atom(
        kind="decision",
        scope="fops",
        content="改用 Python 3.12 + sqlite-vec，放弃 Go CGO 静态编译",
        ts="2026-09-01T10:00:00+08:00",
        meta={"supersedes": old_dec},
    )

    report = engine.mirror("假设我们在 macOS 部署脚本里直接用 rsync --mkpath 同步目录", scope="fops")
    assert len(report["conflicts"]) >= 1
    assert len(report["lesson_warnings"]) >= 1
    assert any(b["kind"] == "unverified_assumption" for b in report["blind_spots"])

    # Mirror against rejected historical decision surfaces superseded prior_decisions
    report2 = engine.mirror("用 Go 纯静态编译打包 sqlite 存储扩展", scope="fops")
    assert any(d["id"] == old_dec and d["status"] == "superseded" for d in report2["prior_decisions"])

    # Export markdown views
    exp = engine.export_markdown(tmp_path / "views")
    assert exp["node_count"] == 4
    assert (tmp_path / "views" / "README.md").exists()
    assert (tmp_path / "views" / "lessons.md").exists()

    with pytest.raises(NotFoundError):
        engine.get("LSN-999")
