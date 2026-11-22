#!/usr/bin/env python3
"""End-to-end FlowOS Hybrid Engine benchmark on industry open-source memory & code retrieval datasets.

Evaluates FlowOS's ACTUAL production `SnapshotDB` + `query_snapshot_recall` pipeline
(FTS5 + sqlite-vec Qwen3-Embedding-4B + Trigger/Entity + RRF + Reranker + Packing)
against pure Qwen3-Embedding-4B vector retrieval to expose hybrid fusion / reranking gaps.

Open-source benchmarks:
  1. CoIR-Retrieval/stackoverflow-qa (test split: real developer debugging & architecture Q&A)
  2. CoIR-Retrieval/cosqa (test split: natural-language intent -> code retrieval)
  3. xiaowu0162/longmemeval-cleaned (oracle: long-term memory across 6 LongMemEval categories)
  4. FlowOS Live Brain + Code Patterns (20 real cross-lingual & Kotlin WebFlux code-pattern queries)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fos.engine import (  # noqa: E402
    build_l0_line,
    extract_entities,
    extract_triggers,
    node_embedding_text,
    query_snapshot_recall,
    split_rule_and_rationale,
)
from fos.models import CompiledNode  # noqa: E402
from fos.providers import LexicalReranker, MLXEmbedder  # noqa: E402
from fos.store import SnapshotDB, SnapshotManager  # noqa: E402

DEFAULT_MODEL = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if (na > 0 and nb > 0) else 0.0


def _score_ranks(
    queries: list[tuple[str, set[str], str]],
    get_ranking: Any,
) -> dict[str, Any]:
    r1 = r3 = r5 = mrr5 = ndcg5 = 0.0
    n = len(queries)
    misses: list[dict[str, Any]] = []
    by_cat: dict[str, list[float]] = defaultdict(list)

    t0 = time.perf_counter()
    for q_text, pos_ids, cat in queries:
        ranked_ids = get_ranking(q_text)
        top5 = ranked_ids[:5]
        hit1 = 1.0 if (top5 and top5[0] in pos_ids) else 0.0
        hit3 = 1.0 if any(d in pos_ids for d in top5[:3]) else 0.0
        hit5 = 1.0 if any(d in pos_ids for d in top5) else 0.0
        r1 += hit1
        r3 += hit3
        r5 += hit5
        by_cat[cat].append(hit1)

        rr = 0.0
        dcg = 0.0
        for rank, did in enumerate(top5, start=1):
            if did in pos_ids:
                if rr == 0.0:
                    rr = 1.0 / rank
                dcg += 1.0 / math.log2(rank + 1)
        idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(pos_ids), 5) + 1))
        mrr5 += rr
        ndcg5 += (dcg / idcg) if idcg > 0 else 0.0

        if hit1 == 0.0 and len(misses) < 12:
            misses.append(
                {
                    "cat": cat,
                    "query": q_text[:110],
                    "expected": sorted(pos_ids)[:3],
                    "got_top3": top5[:3],
                }
            )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "n": n,
        "recall@1": round(r1 / max(1, n), 4),
        "recall@3": round(r3 / max(1, n), 4),
        "recall@5": round(r5 / max(1, n), 4),
        "mrr@5": round(mrr5 / max(1, n), 4),
        "ndcg@5": round(ndcg5 / max(1, n), 4),
        "avg_ms": round(elapsed_ms / max(1, n), 2),
        "by_cat_r1": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_cat.items())},
        "misses": misses,
    }


def _eval_corpus_with_fos(
    name: str,
    docs: list[dict[str, Any]],
    queries: list[tuple[str, set[str], str]],
    embedder: MLXEmbedder,
    *,
    budget: int = 4000,
) -> dict[str, Any]:
    """Build an ephemeral SnapshotDB with real CompiledNodes, compare Pure Vector vs FlowOS Hybrid."""
    nodes: list[CompiledNode] = []
    for d in docs:
        nid = str(d["id"])
        raw = str(d["text"]).strip()
        rule, rationale = split_rule_and_rationale(raw[:650])
        if d.get("rule"):
            rule = str(d["rule"])
        if d.get("rationale"):
            rationale = str(d["rationale"])
        entities = extract_entities(raw[:900])
        triggers = extract_triggers(rule, str(d.get("scope") or "global"), entities)
        node = CompiledNode(
            id=nid,
            type=str(d.get("type") or "decision"),
            scope=str(d.get("scope") or "global"),
            severity=str(d.get("severity") or "P1"),
            valid_from=str(d.get("valid_from") or "2026-01-01T00:00:00Z"),
            rule=rule,
            rationale=rationale,
            trigger_when=triggers,
            entities=entities,
            compat=dict(d.get("compat") or {}),
        )
        node.l0 = build_l0_line(node)
        nodes.append(node)

    # Embed all docs once (shared by pure vector and FlowOS hybrid for apples-to-apples comparison)
    doc_texts = [node_embedding_text(n) for n in nodes]
    t_emb = time.perf_counter()
    doc_vecs = embedder.embed(doc_texts)
    emb_s = time.perf_counter() - t_emb
    vec_by_id = {n.id: v for n, v in zip(nodes, doc_vecs)}

    # Pre-embed all queries once so both pure-vec and hybrid share exact query vectors
    q_texts = [q[0] for q in queries]
    q_vecs = [embedder.embed_query(qt) for qt in q_texts]
    q_vec_map = {qt: qv for qt, qv in zip(q_texts, q_vecs)}

    class _CachedQueryEmbedder:
        dim = embedder.dim
        model_name = embedder.model_name

        def embed_query(self, q: str) -> list[float]:
            return q_vec_map[q]

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [q_vec_map.get(t) or embedder.embed_query(t) for t in texts]

    cached_emb = _CachedQueryEmbedder()
    reranker = LexicalReranker()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "bench.db"
        with SnapshotDB(db_path, embedding_dim=embedder.dim) as db:
            for n in nodes:
                db.upsert_node(n, embedding=vec_by_id[n.id])

            def _pure_vec(q_text: str) -> list[str]:
                qv = q_vec_map[q_text]
                scored = [(nid, _cosine(qv, v)) for nid, v in vec_by_id.items()]
                scored.sort(key=lambda x: x[1], reverse=True)
                return [nid for nid, _ in scored[:10]]

            def _fos_hybrid(q_text: str) -> list[str]:
                res = query_snapshot_recall(
                    db,
                    q_text,
                    embedder=cached_emb,
                    reranker=reranker,
                    budget=budget,
                    limit=10,
                )
                return [str(r["id"]) for r in res["results"]]

            pure_metrics = _score_ranks(queries, _pure_vec)
            hybrid_metrics = _score_ranks(queries, _fos_hybrid)

    return {
        "suite": name,
        "corpus_size": len(nodes),
        "query_count": len(queries),
        "embed_sec": round(emb_s, 2),
        "pure_vec": pure_metrics,
        "fos_hybrid": hybrid_metrics,
        "delta_r1_pp": round((hybrid_metrics["recall@1"] - pure_metrics["recall@1"]) * 100.0, 2),
        "delta_mrr5_pp": round((hybrid_metrics["mrr@5"] - pure_metrics["mrr@5"]) * 100.0, 2),
    }


def load_coir_suite(
    dataset_name: str,
    *,
    max_queries: int = 120,
    max_corpus: int = 240,
) -> tuple[list[dict[str, Any]], list[tuple[str, set[str], str]]]:
    from datasets import load_dataset

    ds_q = load_dataset(dataset_name, "queries", split="queries")
    ds_c = load_dataset(dataset_name, "corpus", split="corpus")
    ds_r = load_dataset(dataset_name, "default", split="test")

    q_map = {row["_id"]: row["text"] for row in ds_q}
    c_map = {row["_id"]: (row.get("title") or "", row["text"]) for row in ds_c}

    qrels: dict[str, set[str]] = defaultdict(set)
    for row in ds_r:
        if int(row.get("score", 1)) > 0 and row["query-id"] in q_map and row["corpus-id"] in c_map:
            qrels[row["query-id"]].add(row["corpus-id"])

    selected_qids = list(qrels.keys())[:max_queries]
    pos_cids: set[str] = set()
    for qid in selected_qids:
        pos_cids.update(qrels[qid])

    corpus_ids = list(pos_cids)
    for cid in c_map:
        if len(corpus_ids) >= max_corpus:
            break
        if cid not in pos_cids:
            corpus_ids.append(cid)

    docs = []
    for cid in corpus_ids:
        title, text = c_map[cid]
        combined = f"{title}\n{text}".strip() if title else text
        docs.append({"id": cid, "text": combined[:800], "type": "decision"})

    queries = [(q_map[qid][:400], qrels[qid], "default") for qid in selected_qids]
    return docs, queries


def _parse_lme_date(raw: str) -> str:
    # '2023/04/10 (Mon) 17:50' -> '2023-04-10T17:50:00Z'
    m = re.match(r"(\d{4})/(\d{2})/(\d{2}).*?(\d{2}:\d{2})", raw or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:00Z"
    return "2023-06-01T00:00:00Z"


def _summarize_session(turns: list[dict[str, Any]], max_chars: int = 750) -> str:
    user_parts = []
    asst_parts = []
    for t in turns:
        role = str(t.get("role") or "")
        content = " ".join(str(t.get("content") or "").split())
        if not content:
            continue
        if role == "user":
            user_parts.append(content[:280])
        else:
            asst_parts.append(content[:180])
    # Prioritize user utterances (most LongMemEval facts live in user turns) + brief assistant context
    joined = " | ".join(user_parts[:4])
    if len(joined) < max_chars - 120 and asst_parts:
        joined += " || " + " | ".join(asst_parts[:2])
    return joined[:max_chars]


def load_longmemeval_suite(
    *,
    per_category: int = 25,
) -> tuple[list[dict[str, Any]], list[tuple[str, set[str], str]]]:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download("xiaowu0162/longmemeval-cleaned", "longmemeval_oracle.json", repo_type="dataset")
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data:
        by_type[item["question_type"]].append(item)

    docs_by_id: dict[str, dict[str, Any]] = {}
    queries: list[tuple[str, set[str], str]] = []

    for qtype, items in sorted(by_type.items()):
        for item in items[:per_category]:
            ans_sids = set(item.get("answer_session_ids") or [])
            sids = item.get("haystack_session_ids") or []
            dates = item.get("haystack_dates") or []
            sessions = item.get("haystack_sessions") or []
            valid_ans: set[str] = set()
            for sid, dt, turns in zip(sids, dates, sessions):
                if sid not in docs_by_id:
                    summary = _summarize_session(turns)
                    docs_by_id[sid] = {
                        "id": sid,
                        "text": summary,
                        "rule": summary[:320],
                        "rationale": summary[320:600],
                        "valid_from": _parse_lme_date(dt),
                        "type": "thought",
                    }
                if sid in ans_sids:
                    valid_ans.add(sid)
            if valid_ans:
                queries.append((str(item["question"]), valid_ans, qtype))

    return list(docs_by_id.values()), queries


def eval_live_personal_brain(embedder: MLXEmbedder) -> dict[str, Any]:
    """Evaluate FlowOS full hybrid engine on 20 real personal brain + code-pattern queries."""
    brain_dir = Path.home() / ".fos" / "brain"
    sm = SnapshotManager(brain_dir, embedding_dim=embedder.dim)
    reranker = LexicalReranker()

    personal_queries: list[tuple[str, set[str], str]] = [
        ("How should I avoid memory copy in Kotlin WebFlux streaming proxy?", {"DEC-047", "DEC-024"}, "code_pattern"),
        ("WebFlux 客户端断连后怎么继续读完上游计费", {"DEC-047"}, "code_pattern"),
        ("How to parse huge JSON payloads with zero allocation and no GC pressure?", {"DEC-048"}, "code_pattern"),
        ("热路径避免反序列化大请求体 JSON 的零拷贝扫描", {"DEC-048"}, "code_pattern"),
        ("Why shouldn't we use MCP servers for local AI engineering CLIs?", {"PRN-001"}, "principle"),
        ("Can I put raw HTML buttons or hardcoded blue colors in React components?", {"DEC-039", "DEC-030"}, "frontend"),
        ("Should I create foreign keys in MySQL or PostgreSQL database schemas?", {"DEC-003"}, "database"),
        ("How should AI discover hosts and run scp or rsync via ssh-center?", {"DEC-043"}, "ops"),
        ("How should MyGo CLI commands be invoked in invoice-creator?", {"LSN-009"}, "ops"),
        ("How must automatic release updates be published in invoice-creator?", {"DEC-013", "DEC-014"}, "ops"),
        ("How should Spring Boot 4 config key upgrades be verified?", {"LSN-013"}, "backend"),
        ("Should frontend API types be handwritten or generated from OpenAPI?", {"DEC-033"}, "frontend"),
        ("How is auth handled in flowxy-gateway console instead of Spring Security?", {"DEC-022"}, "backend"),
        ("What should I do before making non-trivial architectural edits?", {"PRN-004"}, "principle"),
        ("When should a lesson be promoted to permanent memory?", {"PRN-002"}, "principle"),
        ("What is the scope of tests required for small vs large code changes?", {"PRN-006"}, "principle"),
        ("How to keep changes small and avoid over-engineering?", {"PRN-003", "PRN-008"}, "principle"),
        ("Does user explicit instruction override skill guidelines?", {"PRN-007"}, "principle"),
        ("WebFlux 协程调用链如果必须阻塞该怎么切线程", {"DEC-023"}, "code_pattern"),
        ("高并发流式转发怎么防慢客户端占死连接", {"DEC-047"}, "code_pattern"),
    ]

    with sm.open_current(read_only=True) as db:
        active = {n.id for n in db.list_nodes(as_of=None)}
        filtered = [(q, exp & active, cat) for q, exp, cat in personal_queries if (exp & active)]

        q_vec_map = {q: embedder.embed_query(q) for q, _, _ in filtered}

        class _Cached:
            dim = embedder.dim
            model_name = embedder.model_name

            def embed_query(self, q: str) -> list[float]:
                return q_vec_map[q]

            def embed(self, texts: list[str]) -> list[list[float]]:
                return [q_vec_map[t] for t in texts]

        cached = _Cached()

        def _pure_vec(q_text: str) -> list[str]:
            hits = db.search_vec(q_vec_map[q_text], limit=10)
            return [nid for nid, _ in hits if nid in active]

        def _fos_hybrid(q_text: str) -> list[str]:
            res = query_snapshot_recall(
                db,
                q_text,
                embedder=cached,
                reranker=reranker,
                budget=1200,
                limit=10,
            )
            return [str(r["id"]) for r in res["results"]]

        pure_m = _score_ranks(filtered, _pure_vec)
        hyb_m = _score_ranks(filtered, _fos_hybrid)

    return {
        "suite": "PersonalBrain+CodePatterns",
        "corpus_size": len(active),
        "query_count": len(filtered),
        "pure_vec": pure_m,
        "fos_hybrid": hyb_m,
        "delta_r1_pp": round((hyb_m["recall@1"] - pure_m["recall@1"]) * 100.0, 2),
        "delta_mrr5_pp": round((hyb_m["mrr@5"] - pure_m["mrr@5"]) * 100.0, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-queries", type=int, default=100)
    parser.add_argument("--max-corpus", type=int, default=220)
    parser.add_argument("--lme-per-cat", type=int, default=25)
    parser.add_argument("--json-out", type=str, default=None)
    args = parser.parse_args()

    print(f"Loading MLX embedder: {args.model} ...", flush=True)
    embedder = MLXEmbedder(model_name=args.model)
    _ = embedder.embed_query("warmup")

    results: list[dict[str, Any]] = []

    print("\n[1/4] CoIR-Retrieval/stackoverflow-qa (Developer Q&A & Architecture)...", flush=True)
    so_docs, so_q = load_coir_suite("CoIR-Retrieval/stackoverflow-qa", max_queries=args.max_queries, max_corpus=args.max_corpus)
    results.append(_eval_corpus_with_fos("CoIR/stackoverflow-qa", so_docs, so_q, embedder))

    print("\n[2/4] CoIR-Retrieval/cosqa (NL -> Code Pattern Search)...", flush=True)
    cosqa_docs, cosqa_q = load_coir_suite("CoIR-Retrieval/cosqa", max_queries=args.max_queries, max_corpus=args.max_corpus)
    results.append(_eval_corpus_with_fos("CoIR/cosqa", cosqa_docs, cosqa_q, embedder))

    print("\n[3/4] xiaowu0162/longmemeval-cleaned (ICLR LongMemEval, 6 categories)...", flush=True)
    lme_docs, lme_q = load_longmemeval_suite(per_category=args.lme_per_cat)
    results.append(_eval_corpus_with_fos("LongMemEval-Oracle", lme_docs, lme_q, embedder))

    print("\n[4/4] Live ~/.fos Personal Brain + Kotlin WebFlux Code Patterns...", flush=True)
    results.append(eval_live_personal_brain(embedder))

    print("\n" + "=" * 96)
    print(f"{'Suite':<28} {'Corpus':>6} {'Q':>4} | {'Vec R@1':>8} {'FOS R@1':>8} {'ΔR@1':>7} | {'Vec MRR':>8} {'FOS MRR':>8} {'ΔMRR':>7}")
    print("-" * 96)
    for r in results:
        pv = r["pure_vec"]
        fh = r["fos_hybrid"]
        print(
            f"{r['suite']:<28} {r['corpus_size']:>6} {r['query_count']:>4} | "
            f"{pv['recall@1']*100:>7.1f}% {fh['recall@1']*100:>7.1f}% {r['delta_r1_pp']:>+6.1f}p | "
            f"{pv['mrr@5']*100:>7.1f}% {fh['mrr@5']*100:>7.1f}% {r['delta_mrr5_pp']:>+6.1f}p"
        )
    print("=" * 96)

    for r in results:
        fh = r["fos_hybrid"]
        if len(fh["by_cat_r1"]) > 1:
            print(f"\n[{r['suite']}] Recall@1 by category (FOS Hybrid): {fh['by_cat_r1']}")
            print(f"[{r['suite']}] Recall@1 by category (Pure Vec):   {r['pure_vec']['by_cat_r1']}")
        if fh["misses"]:
            print(f"\n[{r['suite']}] Sample FOS Hybrid misses ({len(fh['misses'])} shown):")
            for m in fh["misses"][:5]:
                print(f"  - ({m['cat']}) Q: {m['query']} | exp={m['expected']} got={m['got_top3']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
