"""Tests for Quick Dream consolidation, 4-dimension Regression Gate, Gate Failure rejection, and atomic Undo."""

from pathlib import Path

import pytest

from fos.dream import run_dream
from fos.engine import BrainEngine
from fos.errors import GateFailedError, NotFoundError
from fos.eval_gate import pin_node_in_engine, run_eval_on_engine
from fos.models import EvalCase


def test_dream_consolidation_gates_and_atomic_undo(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")

    # Add same lesson 3 times -> dream should consolidate seen_count=3 and promote to P0
    r1 = engine.add_atom(
        kind="lesson",
        scope="fops",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持",
        meta={"severity": "P1", "trigger_when": ["macOS rsync 部署"]},
    )
    lsn_id = r1["node"]["id"]
    engine.add_atom(
        kind="lesson",
        scope="fops",
        source="hindsight:claude:s1",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持",
        meta={"severity": "P1"},
    )
    engine.add_atom(
        kind="lesson",
        scope="fops",
        source="hindsight:codex:s2",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p，因为 macOS openrsync 不支持",
        meta={"severity": "P1"},
    )
    # Add low-signal chatter noise from automated source -> dream should drop it
    engine.add_atom(kind="thought", scope="fops", source="hindsight:claude:s3", content="好的")

    # Add bi-temporal decision supersession
    d1 = engine.add_atom(
        kind="decision",
        scope="fos",
        content="第一版使用单机本地文件同步方案",
        ts="2026-08-01T10:00:00+08:00",
    )["node"]["id"]
    engine.add_atom(
        kind="decision",
        scope="fos",
        content="改用 M3 Max 单一编译主节点 fosd + 客户端离线缓存方案",
        ts="2026-10-01T10:00:00+08:00",
        meta={"supersedes": d1},
    )

    # Pin the lesson into permanent P0 golden suite
    pinned = pin_node_in_engine(engine, lsn_id)
    assert pinned["pinned_node"] == lsn_id
    assert all(c["pinned"] for c in pinned["cases"])

    # Run Quick Dream + Songe export hook
    songe_file = tmp_path / "songe_stream.jsonl"
    dream_res = run_dream(engine, export_songe_path=songe_file)
    assert dream_res["promoted"] is True
    assert dream_res["current_version"] == "v0002"
    assert dream_res["gate"]["passed"] is True
    assert dream_res["stats"]["merged_duplicates"] == 2
    assert dream_res["stats"]["promoted_to_p0"] == 1
    assert dream_res["stats"]["dropped_noise"] == 1
    assert songe_file.exists() and dream_res["songe_exported"] >= 2

    # Verify consolidated node in v0002 has seen_count=3 and P0
    got = engine.get(lsn_id, tier="L2")
    assert got["node"]["seen_count"] == 3
    assert got["node"]["severity"] == "P0"
    assert len(got["evidence"]) == 3

    # Standalone eval passes all 4 suites
    ev = run_eval_on_engine(engine, suite="all")
    assert ev["passed"] is True
    assert ev["gates"]["temporal"]["stale_leaks"] == 0
    assert ev["gates"]["temporal"]["as_of_restore_rate"] == 1.0
    assert ev["gates"]["fidelity"]["retention_rate"] == 1.0

    # Atomic undo reverts v0002 -> v0001
    undone = engine.snapshots.undo()
    assert undone["previous"] == "v0002"
    assert undone["current"] == "v0001"
    assert engine.snapshots.current_version() == "v0001"

    # Second undo has no earlier version -> NotFoundError (exit code 3)
    with pytest.raises(NotFoundError):
        engine.snapshots.undo()


def test_dream_regression_gate_blocks_bad_candidate(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    res = engine.add_atom(
        kind="lesson",
        scope="fops",
        content="禁止依赖 rsync --mkpath，必须先 ssh mkdir -p",
        meta={"severity": "P0"},
    )
    lsn_id = res["node"]["id"]
    baseline_ver = engine.snapshots.current_version()

    # Add a golden fidelity assertion demanding a term that is NOT in the compiled L1 rule
    engine.ledger.append_eval_case(
        EvalCase(
            id="evl_strict_fidelity",
            suite="fidelity",
            query="rsync --mkpath",
            node_id=lsn_id,
            must_preserve_terms=["--nonexistent-mandatory-constraint"],
            pinned=True,
        )
    )

    with pytest.raises(GateFailedError) as exc_info:
        run_dream(engine)

    # Current snapshot must remain untouched at baseline!
    assert engine.snapshots.current_version() == baseline_ver
    assert exc_info.value.details["promoted"] is False
    assert exc_info.value.details["gate"]["gates"]["fidelity"]["passed"] is False
    assert exc_info.value.details["rejected_snapshot"] is not None
