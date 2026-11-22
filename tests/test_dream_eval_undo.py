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


def test_dream_llm_distillation_drops_noise_and_caches(tmp_path: Path) -> None:
    from fos.providers import LexicalReranker, LocalHashEmbedder, ProviderBundle

    class _MockDistiller:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def model_name(self) -> str:
            return "mock:distiller-v1"

        def complete_json(self, prompt: str, *, system: str = "") -> dict | None:
            import json as _json

            self.calls += 1
            raw_json = prompt[prompt.index("[") :]
            batch = _json.loads(raw_json)
            items = []
            for entry in batch:
                if "MR token" in entry["raw"]:
                    items.append(
                        {
                            "id": entry["id"],
                            "keep": True,
                            "kind": "lesson",
                            "severity": "P0",
                            "rule": "MR token 模式下必须先配置模型价格，再添加到渠道",
                            "rationale": "未配置价格直接添加会导致渠道创建失败",
                            "trigger_when": ["MR token 渠道配置", "添加新模型到渠道"],
                            "entities": ["mr-token", "channel", "pricing"],
                        }
                    )
                else:
                    items.append({"id": entry["id"], "keep": False, "drop_reason": "one-off question without fix"})
            return {"items": items}

        def unload(self) -> None:
            return

    distiller = _MockDistiller()
    providers = ProviderBundle(embedder=LocalHashEmbedder(), reranker=LexicalReranker(), reasoner=distiller)
    engine = BrainEngine(tmp_path / "brain", providers=providers)

    engine.add_atom(
        kind="lesson",
        scope="goto",
        source="hindsight:codex:s1",
        content="Hindsight: 创建失败：MR token 模式下模型必须先配置价格才能添加到渠道: gpt-6.1-terra",
    )
    engine.add_atom(
        kind="lesson",
        scope="goto",
        source="hindsight:codex:s2",
        content="Hindsight: 为什么一直返回 400.你看下具体的报错信息",
    )

    res1 = run_dream(engine)
    assert res1["promoted"] is True
    assert res1["stats"]["distilled_kept"] == 1
    assert res1["stats"]["distilled_dropped"] == 1
    assert res1["stats"]["compiled_nodes"] == 1
    assert distiller.calls == 1

    got = engine.get("LSN-001", tier="L1")
    assert got["node"]["severity"] == "P0"
    assert "必须先配置模型价格" in got["node"]["rule"]

    # Second dream hits distill_cache.json with zero extra LLM calls
    res2 = run_dream(engine)
    assert res2["stats"]["distill_cache_hits"] == 2
    assert distiller.calls == 1


def test_dream_scope_invariant_keeps_single_repo_local_and_elevates_cross_repo(tmp_path: Path) -> None:
    from fos.providers import LexicalReranker, LocalHashEmbedder, ProviderBundle

    class _OverEagerGlobalDistiller:
        @property
        def model_name(self) -> str:
            return "mock:overeager"

        def complete_json(self, prompt: str, *, system: str = "") -> dict | None:
            import json as _json

            if "Review these active FlowOS memory cards" in prompt:
                return {"actions": []}
            batch = _json.loads(prompt[prompt.index("[") :])
            return {
                "items": [
                    {
                        "id": entry["id"],
                        "keep": True,
                        "kind": "lesson",
                        "scope": "global",  # LLM tries to promote everything to global
                        "severity": "P1",
                        "rule": entry["raw"].replace("Hindsight: ", ""),
                        "rationale": "root cause",
                        "trigger_when": ["trigger"],
                    }
                    for entry in batch
                ]
            }

        def unload(self) -> None:
            return

    providers = ProviderBundle(embedder=LocalHashEmbedder(), reranker=LexicalReranker(), reasoner=_OverEagerGlobalDistiller())
    engine = BrainEngine(tmp_path / "brain", providers=providers)

    # Single-repo git fix MUST remain scoped to 'goto' even when LLM suggests 'global'
    engine.add_atom(kind="lesson", scope="goto", source="hindsight:git:goto", content="Hindsight: 渠道用量必须保留小数精度")
    # Same engineering law hit in TWO distinct repos ('fops' + 'goto') MUST elevate to 'global'
    engine.add_atom(kind="lesson", scope="fops", source="hindsight:git:fops", content="Hindsight: 禁止依赖 rsync --mkpath，必须先 ssh mkdir -p")
    engine.add_atom(kind="lesson", scope="goto", source="hindsight:git:goto", content="Hindsight: 禁止依赖 rsync --mkpath，必须先 ssh mkdir -p")

    res = run_dream(engine)
    assert res["promoted"] is True
    assert engine.get("LSN-001", tier="L1")["node"]["scope"] == "goto"
    assert engine.get("LSN-002", tier="L1")["node"]["scope"] == "global"
    assert engine.get("LSN-002", tier="L1")["node"]["seen_count"] == 2
