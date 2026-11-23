"""Tests for unified SQLite persistence (`state.db`) and transparent legacy JSON/JSONL migration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fos.config import FosConfig, save_config
from fos.client import FosClient
from fos.engine import BrainEngine
from fos.freshness import load_usage_stats, record_recall_hits, record_utility_feedback
from fos.ingest import _load_cursors, _save_cursors
from fos.models import EvalCase, LedgerAtom
from fos.proposals import Proposal, ProposalStore, dismiss_proposal
from fos.state_db import StateDB


def test_all_brain_state_persists_in_sqlite_without_flat_json_files(tmp_path: Path) -> None:
    home = tmp_path / "fos_home"
    save_config(FosConfig(mode="local", device="test"), home)
    client = FosClient(home=home)
    engine = client._engine()
    data_dir = engine.data_dir

    # 1. Ledger + Eval + Snapshot pointer
    added = client.add(
        kind="principle",
        content="所有持久化状态统一落 SQLite state.db，禁止散落 JSONL 文件",
        scope="global",
        severity="P0",
    )
    node_id = added["node"]["id"]

    # 2. Usage stats (hits + feedback)
    record_recall_hits(data_dir, [node_id])
    record_utility_feedback(data_dir, node_id, "useful", context="verified")
    stats = load_usage_stats(data_dir)
    assert stats[node_id]["hits"] == 1
    assert stats[node_id]["useful"] == 1

    # 3. Proposals Inbox (create + dismiss + scan cache)
    pstore = ProposalStore(data_dir)
    prop = Proposal(
        id="prp_test_001",
        kind="promote_global",
        title="Test proposal",
        fingerprint="fp_abc",
        proposed_rule="Prefer SQLite over flat JSONL",
        evidence_repos=["fos", "fops"],
    )
    pstore.save(prop)
    pstore.save_cache({"repo_hashes": {"fos": "abc123"}})
    dismissed = dismiss_proposal(engine, "prp_test_001", reason="用户手动忽略")
    assert dismissed["proposal"]["status"] == "dismissed"
    assert dismissed["proposal"]["dismiss_reason"] == "用户手动忽略"

    # 4. Ingest cursors via SQLite cursor_path
    assert client.cursor_path.suffix == ".db"
    _save_cursors(client.cursor_path, {"git:fos": "deadbeef"})
    assert _load_cursors(client.cursor_path) == {"git:fos": "deadbeef"}

    # 5. Verify state.db exists and holds all tables; NO legacy flat JSON/JSONL files were created
    db_path = data_dir / "state.db"
    assert db_path.is_file()
    for legacy in (
        data_dir / "proposals.jsonl",
        data_dir / "proposal_scan_cache.json",
        data_dir / "usage_stats.json",
        data_dir / "distill_cache.json",
        data_dir / "current.json",
        data_dir / "ledger",
        data_dir / "eval",
    ):
        assert not legacy.exists(), f"Legacy flat file should not be created: {legacy}"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM ledger_atoms").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0] >= 1
    row = conn.execute("SELECT status, dismiss_reason FROM proposals WHERE id = 'prp_test_001'").fetchone()
    assert row["status"] == "dismissed"
    assert row["dismiss_reason"] == "用户手动忽略"
    assert conn.execute("SELECT hits, useful FROM usage_stats WHERE node_id = ?", (node_id,)).fetchone()[:2] == (1, 1)
    assert conn.execute(
        "SELECT COUNT(*) FROM kv_store WHERE namespace IN ('snapshot', 'proposal_scan_cache', 'ingest_cursors')"
    ).fetchone()[0] == 3
    conn.close()

    # 6. Rebuild snapshot preserves all state.db data (including dismissed proposal)
    engine.rebuild_from_ledger()
    reloaded = pstore.get("prp_test_001")
    assert reloaded is not None
    assert reloaded.status == "dismissed"
    assert reloaded.dismiss_reason == "用户手动忽略"


def test_transparent_idempotent_legacy_migration(tmp_path: Path) -> None:
    data_dir = tmp_path / "brain"
    (data_dir / "ledger").mkdir(parents=True)
    (data_dir / "eval").mkdir(parents=True)

    # Seed legacy flat files
    atom = LedgerAtom(
        id="atm_legacy_001",
        ts="2026-11-20T10:00:00+00:00",
        device="mac",
        kind="lesson",
        scope="fos",
        source="manual",
        raw="Legacy lesson atom",
        meta={"severity": "P1"},
    )
    (data_dir / "ledger" / "events-2026-11.jsonl").write_text(
        json.dumps(atom.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    ev = EvalCase(
        id="evl_legacy_001",
        suite="recall",
        query="legacy lesson",
        expected_ids=["LSN-001"],
        pinned=True,
        created_at="2026-11-20T10:00:00+00:00",
    )
    (data_dir / "eval" / "golden.jsonl").write_text(
        json.dumps(ev.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Append-only proposals.jsonl with two versions of same id (pending -> dismissed)
    p1 = Proposal(id="prp_leg_1", kind="promote_global", status="pending", title="Old")
    p2 = Proposal(
        id="prp_leg_1",
        kind="promote_global",
        status="dismissed",
        title="Old",
        dismiss_reason="already dismissed",
    )
    (data_dir / "proposals.jsonl").write_text(
        json.dumps(p1.to_dict(), ensure_ascii=False) + "\n"
        + json.dumps(p2.to_dict(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (data_dir / "proposal_scan_cache.json").write_text(
        json.dumps({"scan_ts": "2026-11-20"}), encoding="utf-8"
    )
    (data_dir / "usage_stats.json").write_text(
        json.dumps({"LSN-001": {"hits": 7, "useful": 3, "noise": 0, "guard_blocks": 1, "utility_score": 0.8}}),
        encoding="utf-8",
    )
    (data_dir / "distill_cache.json").write_text(
        json.dumps({"atm_legacy_001@cli": {"keep": True, "rule": "Distilled rule"}}),
        encoding="utf-8",
    )
    (data_dir / "current.json").write_text(
        json.dumps({"current": "v0002", "history": ["v0001", "v0002"]}),
        encoding="utf-8",
    )
    legacy_cursors = tmp_path / "ingest_cursors.json"
    legacy_cursors.write_text(json.dumps({"git:fos": "123456"}), encoding="utf-8")

    # Open StateDB -> triggers automatic migration
    with StateDB(data_dir) as db:
        db.migrate_legacy_cursors(legacy_cursors)
        assert len(db.list_atoms()) == 1
        assert db.get_atom("atm_legacy_001").raw == "Legacy lesson atom"  # type: ignore[union-attr]
        assert len(db.list_eval_cases()) == 1
        assert db.list_eval_cases()[0].pinned is True
        p = db.get_proposal_dict("prp_leg_1")
        assert p is not None and p["status"] == "dismissed" and p["dismiss_reason"] == "already dismissed"
        assert db.get_kv("proposal_scan_cache", "state") == {"scan_ts": "2026-11-20"}
        assert db.load_usage_stats()["LSN-001"]["hits"] == 7
        assert db.load_distill_cache()["atm_legacy_001@cli"]["rule"] == "Distilled rule"
        assert db.get_kv("snapshot", "pointer")["current"] == "v0002"
        assert db.get_kv("ingest_cursors", "state") == {"git:fos": "123456"}

    # Re-opening StateDB after mutating DB does NOT overwrite with stale legacy files (idempotent!)
    with StateDB(data_dir) as db:
        p = db.get_proposal_dict("prp_leg_1")
        assert p is not None
        p["dismiss_reason"] = "updated in sqlite"
        db.upsert_proposal_dict(p)

    with StateDB(data_dir) as db:
        assert db.get_proposal_dict("prp_leg_1")["dismiss_reason"] == "updated in sqlite"  # type: ignore[index]
