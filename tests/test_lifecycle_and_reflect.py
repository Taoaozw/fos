"""Tests for self-correction (fos correct), archival (fos archive), TTL half-life decay, and Dream Reflect."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fos.cli import run_cli
from fos.dream import _apply_ttl_decay, run_dream
from fos.engine import BrainEngine
from fos.errors import ExitCode, NotFoundError
from fos.eval_gate import pin_node_in_engine
from fos.models import CompiledNode
from fos.providers import LexicalReranker, LocalHashEmbedder, ProviderBundle


def test_correct_and_archive_preserve_bitemporal_history_and_rebuild(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    r1 = engine.add_atom(
        kind="lesson",
        scope="goto",
        content="渠道用量按整数解析即可",
        ts="2026-08-01T10:00:00+08:00",
        meta={"severity": "P1"},
    )
    old_id = r1["node"]["id"]

    # Self-correct old wrong rule -> supersedes old_id keeping kind=lesson & scope=goto
    cor = engine.correct_node(
        old_id,
        "渠道用量必须保留小数精度，禁止按整数解析或截断",
        reason="上游实际返回小数值",
    )
    new_id = cor["node"]["id"]
    assert cor["superseded"] == old_id
    assert cor["node"]["type"] == "lesson"
    assert cor["node"]["scope"] == "goto"
    assert cor["node"]["confidence"] >= 0.90
    assert any(c.suite == "temporal" and old_id in c.forbidden_ids for c in engine.ledger.load_eval_cases())

    # Active recall returns ONLY corrected rule
    cur = engine.recall("渠道用量", scope="goto")
    cur_ids = [r["id"] for r in cur["results"]]
    assert new_id in cur_ids
    assert old_id not in cur_ids

    # Historical --as-of restores original rule prior to correction
    hist = engine.recall("渠道用量", scope="goto", as_of="2026-08")
    assert old_id in [r["id"] for r in hist["results"]]

    # Archive a separate obsolete rule
    obs = engine.add_atom(
        kind="lesson",
        scope="goto",
        content="临时规避老接口字段拼写错误",
        ts="2026-08-10T10:00:00+08:00",
        meta={"severity": "P2"},
    )["node"]["id"]
    arc = engine.archive_node(obs, reason="老接口已下线")
    assert arc["archived"] == obs
    assert arc["node"] is None

    # Archived node is hidden from active recall & boot, but visible via --as-of 2026-08
    assert obs not in [r["id"] for r in engine.recall("老接口字段", scope="goto")["results"]]
    assert obs in [r["id"] for r in engine.recall("老接口字段", scope="goto", as_of="2026-08")["results"]]

    # Rebuild from immutable ledger preserves BOTH correction and archive state
    engine.rebuild_from_ledger()
    assert engine.get(old_id)["node"]["status"] == "superseded"
    assert engine.get(new_id)["node"]["status"] == "active"
    assert engine.get(obs)["node"]["status"] == "archived"

    # Missing ID raises NotFoundError
    with pytest.raises(NotFoundError):
        engine.archive_node("LSN-999")


def test_deterministic_ttl_half_life_decay_protects_core_nodes() -> None:
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    def _mk(nid: str, ntype: str, sev: str, days_ago: int, seen: int = 1) -> CompiledNode:
        ts = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        return CompiledNode(
            id=nid,
            type=ntype,
            scope="fos",
            severity=sev,
            valid_from=ts,
            updated_at=ts,
            seen_count=seen,
            rule=f"rule {nid}",
            l0=f"[{nid}] rule",
        )

    nodes = [
        _mk("THT-001", "thought", "P2", days_ago=35),       # >30d -> archive
        _mk("LSN-001", "lesson", "P2", days_ago=65),        # >60d -> archive
        _mk("LSN-002", "lesson", "P1", days_ago=190),       # >180d unpinned -> archive
        _mk("LSN-003", "lesson", "P1", days_ago=200),       # pinned -> KEEP
        _mk("LSN-004", "lesson", "P1", days_ago=200, seen=2),  # recurring -> KEEP
        _mk("LSN-005", "lesson", "P0", days_ago=300),       # P0 redline -> KEEP
        _mk("PRN-001", "principle", "P1", days_ago=300),    # principle -> KEEP
        _mk("LSN-006", "lesson", "P2", days_ago=10),        # fresh -> KEEP
    ]

    expired = _apply_ttl_decay(nodes, pinned_ids={"LSN-003"}, now_dt=now)
    assert expired == 3
    by_id = {n.id: n.status for n in nodes}
    assert by_id["THT-001"] == "archived"
    assert by_id["LSN-001"] == "archived"
    assert by_id["LSN-002"] == "archived"
    for kept in ("LSN-003", "LSN-004", "LSN-005", "PRN-001", "LSN-006"):
        assert by_id[kept] == "active"


def test_dream_reflect_self_correction_respects_pinned_and_caches(tmp_path: Path) -> None:
    class _MockReflector:
        def __init__(self) -> None:
            self.reflect_calls = 0

        @property
        def model_name(self) -> str:
            return "mock:reflect-v1"

        def complete_json(self, prompt: str, *, system: str = "") -> dict | None:
            if "Review these active FlowOS memory cards" in prompt:
                self.reflect_calls += 1
                return {
                    "actions": [
                        {"action": "supersede", "winner_id": "LSN-002", "loser_id": "LSN-001", "reason": "LSN-002 corrects LSN-001"},
                        {"action": "archive", "target_id": "LSN-003", "reason": "obsolete workaround"},
                        # Attempt to archive a pinned node -> MUST be blocked by code guard!
                        {"action": "archive", "target_id": "LSN-004", "reason": "should be blocked"},
                    ]
                }
            return None

        def unload(self) -> None:
            return

    reflector = _MockReflector()
    providers = ProviderBundle(embedder=LocalHashEmbedder(), reranker=LexicalReranker(), reasoner=reflector)
    engine = BrainEngine(tmp_path / "brain", providers=providers)

    engine.add_atom(kind="lesson", scope="fos", content="旧规则：直接用整数解析用量", ts="2026-09-01T10:00:00+08:00")
    engine.add_atom(kind="lesson", scope="fos", content="新规则：必须用小数精度解析用量，禁止整数截断", ts="2026-10-01T10:00:00+08:00")
    engine.add_atom(kind="lesson", scope="fos", content="已废弃的临时兼容开关说明", ts="2026-10-02T10:00:00+08:00", meta={"severity": "P2"})
    pinned_id = engine.add_atom(kind="lesson", scope="fos", content="核心钉选规则：禁止使用 rsync --mkpath", ts="2026-10-03T10:00:00+08:00", meta={"severity": "P1"})["node"]["id"]
    pin_node_in_engine(engine, pinned_id)

    res1 = run_dream(engine, no_gate=True)
    assert res1["promoted"] is True
    assert res1["stats"]["reflect_superseded"] == 1
    assert res1["stats"]["reflect_archived"] == 1
    assert reflector.reflect_calls == 1

    assert engine.get("LSN-001")["node"]["status"] == "superseded"
    assert engine.get("LSN-001")["node"]["superseded_by"] == "LSN-002"
    assert engine.get("LSN-003")["node"]["status"] == "archived"
    # Pinned node survived reflection untouched!
    assert engine.get(pinned_id)["node"]["status"] == "active"

    # Second dream hits reflect cache -> 0 new LLM calls
    res2 = run_dream(engine, no_gate=True)
    assert res2["stats"]["reflect_cache_hit"] == 1
    assert reflector.reflect_calls == 1


def test_cli_correct_and_archive_aliases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(tmp_path / "fos_home")
    assert run_cli(["--home", home, "add", "--kind", "lesson", "--scope", "fos", "初始规则草稿", "--json"]) == int(ExitCode.OK)
    nid = json.loads(capsys.readouterr().out)["node"]["id"]

    assert run_cli(["--home", home, "cor", nid, "修正后的精确规则", "--reason", "补充边界条件", "--json"]) == int(ExitCode.OK)
    cor_data = json.loads(capsys.readouterr().out)
    assert cor_data["superseded"] == nid
    new_id = cor_data["node"]["id"]

    assert run_cli(["--home", home, "arc", new_id, "--reason", "已固化为单测", "--json"]) == int(ExitCode.OK)
    arc_data = json.loads(capsys.readouterr().out)
    assert arc_data["archived"] == new_id
