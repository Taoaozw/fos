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


def test_dream_blocks_single_repo_chat_and_docs_from_self_promoting_to_global_p0(tmp_path: Path) -> None:
    from fos.providers import LexicalReranker, LocalHashEmbedder, ProviderBundle

    class _OverEagerPrincipleDistiller:
        @property
        def model_name(self) -> str:
            return "mock:overeager-principle"

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
                        "kind": "principle",
                        "scope": "global",
                        "severity": "P0",
                        "rule": entry["raw"].replace("Hindsight: ", ""),
                        "rationale": "root cause",
                        "trigger_when": ["trigger"],
                    }
                    for entry in batch
                ]
            }

        def unload(self) -> None:
            return

    providers = ProviderBundle(embedder=LocalHashEmbedder(), reranker=LexicalReranker(), reasoner=_OverEagerPrincipleDistiller())
    engine = BrainEngine(tmp_path / "brain", providers=providers)

    # Single-repo Antigravity chat & repo docs MUST NOT self-promote to global P0 principle
    engine.add_atom(
        kind="lesson",
        scope="fos",
        source="hindsight:antigravity:conv-1",
        content="Hindsight: 长期记忆用 source_ref 指向原文，不要复制全文",
    )
    engine.add_atom(
        kind="decision",
        scope="flowxy-gateway",
        source="hindsight:docs:flowxy-gateway",
        content="Hindsight: 网关热路径保持零拷贝和最小原语",
    )
    # Global user rules doc IS allowed to become global P0 principle
    engine.add_atom(
        kind="principle",
        scope="global",
        source="hindsight:docs:global:.gemini",
        content="Hindsight: 采用最小局部改动，禁止修改无关模块",
    )

    res = run_dream(engine)
    assert res["promoted"] is True

    chat_node = engine.get("LSN-001", tier="L1")["node"]
    assert chat_node["scope"] == "fos"
    assert chat_node["type"] == "lesson"
    assert chat_node["severity"] == "P1"

    doc_node = engine.get("DEC-001", tier="L1")["node"]
    assert doc_node["scope"] == "flowxy-gateway"
    assert doc_node["type"] == "decision"
    assert doc_node["severity"] == "P1"

    global_node = engine.get("PRN-001", tier="L1")["node"]
    assert global_node["scope"] == "global"
    assert global_node["type"] == "principle"
    assert global_node["severity"] == "P0"


def test_dream_lock_blocks_live_holder_and_reclaims_dead_or_stale_lock(tmp_path: Path) -> None:
    import json as _json
    import os
    import time

    from fos.dream import DreamLock
    from fos.errors import DreamLockedError

    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(kind="lesson", scope="fos", content="禁止无锁并发写候选快照")

    # Live lock held by current PID blocks concurrent run_dream
    with DreamLock(engine.data_dir):
        with pytest.raises(DreamLockedError):
            run_dream(engine)

    # Dead PID lock is automatically reclaimed
    lock_file = engine.data_dir / ".dream.lock"
    lock_file.write_text(
        _json.dumps({"pid": 99999999, "acquired_at": time.time(), "token": "dead"}),
        encoding="utf-8",
    )
    res_dead = run_dream(engine)
    assert res_dead["promoted"] is True
    assert not lock_file.exists()

    # Stale lock (even with live PID) is reclaimed once age >= stale_secs
    lock_file.write_text(
        _json.dumps({"pid": os.getpid(), "acquired_at": time.time() - 7200, "token": "stale"}),
        encoding="utf-8",
    )
    res_stale = run_dream(engine, lock_stale_secs=60)
    assert res_stale["promoted"] is True
    assert not lock_file.exists()


def test_dream_if_changed_skips_unchanged_and_runs_after_new_atom(tmp_path: Path) -> None:
    engine = BrainEngine(tmp_path / "brain")
    engine.add_atom(kind="lesson", scope="fos", content="首次写入触发编译")

    first = run_dream(engine, if_changed=True)
    assert first["promoted"] is True
    v1 = first["current_version"]

    # No new atoms -> short-circuits immediately without bumping snapshot version
    skipped = run_dream(engine, if_changed=True)
    assert skipped["promoted"] is False
    assert skipped["skipped"] is True
    assert skipped["skip_reason"] == "unchanged_ledger"
    assert skipped["current_version"] == v1

    # Adding a new atom invalidates the gate -> next if_changed run compiles & promotes
    engine.add_atom(kind="decision", scope="fos", content="新增决策后重新触发睡眠编译")
    second = run_dream(engine, if_changed=True)
    assert second["promoted"] is True
    assert second["current_version"] != v1


def test_reflect_rejects_invalid_demote_scope_and_supersedes_cycle(tmp_path: Path) -> None:
    from fos.dream import _reflect_active_nodes, _would_create_supersedes_cycle
    from fos.models import CompiledNode

    class _BadReflectReasoner:
        @property
        def model_name(self) -> str:
            return "mock:bad-reflect"

        def complete_json(self, prompt: str, *, system: str = "") -> dict | None:
            return {
                "actions": [
                    {
                        "action": "demote",
                        "target_id": "DEC-001",
                        "new_scope": "eng/frontend/react/hooks/too-deep",  # >3 segments, invalid!
                        "new_severity": "P2",
                        "reason": "too deep scope",
                    },
                    {
                        "action": "demote",
                        "target_id": "DEC-002",
                        "new_scope": "eng/frontend/react",  # valid 3-level scope
                        "new_severity": "P1",
                        "reason": "valid domain demote",
                    },
                ]
            }

        def unload(self) -> None:
            return

    n1 = CompiledNode(id="DEC-001", type="decision", scope="global", severity="P1", rule="规则一", valid_from="2026-11-01T00:00:00Z")
    n2 = CompiledNode(id="DEC-002", type="decision", scope="global", severity="P1", rule="规则二", valid_from="2026-11-02T00:00:00Z")
    nodes = {"DEC-001": n1, "DEC-002": n2}

    stats = _reflect_active_nodes(nodes, [], _BadReflectReasoner(), tmp_path, pinned_ids=set())
    # Invalid 5-level scope rejected fail-closed; valid 3-level scope applied
    assert n1.scope == "global"
    assert n2.scope == "eng/frontend/react"
    assert stats["reflect_demoted"] == 1

    # Cycle guard detects transitive cycle A -> B -> A
    n1.superseded_by = "DEC-002"
    assert _would_create_supersedes_cycle("DEC-001", "DEC-002", nodes) is True
    assert _would_create_supersedes_cycle("DEC-002", "DEC-001", nodes) is False
