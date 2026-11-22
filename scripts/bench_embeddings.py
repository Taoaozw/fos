#!/usr/bin/env python3
"""Open-source & personal-brain embedding benchmark comparing FlowOS vector engines on M3 Max.

Compares:
  1. local_hash (256-dim deterministic baseline)
  2. mlx-community/multilingual-e5-small-mlx (384-dim)
  3. mlx-community/Qwen3-Embedding-4B-4bit-DWQ (2560-dim)

Suites:
  - CoIR-Retrieval/cosqa (Open-source Code Search QA retrieval benchmark)
  - C-MTEB/DuRetrieval (Open-source Chinese passage retrieval benchmark)
  - FlowOS Personal Brain Cross-Lingual & Paraphrase Retrieval (15 hard queries on ~/.fos active cards)

Metrics: Recall@1, Recall@5, MRR@5, NDCG@5, Cosine Separation Margin (pos - top_neg), Warm Latency (ms).
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fos.providers import LocalHashEmbedder, MLXEmbedder  # noqa: E402
from fos.store import SnapshotManager  # noqa: E402


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _eval_retrieval(
    embedder: Any,
    queries: list[tuple[str, str]],  # (qid, text)
    corpus: list[tuple[str, str]],  # (did, text)
    qrels: dict[str, set[str]],  # qid -> set of relevant dids
) -> dict[str, Any]:
    t0 = time.perf_counter()
    c_texts = [t for _, t in corpus]
    c_ids = [cid for cid, _ in corpus]
    c_vecs = embedder.embed(c_texts)
    corpus_ms = (time.perf_counter() - t0) * 1000.0

    # Warm query benchmark
    q_latencies: list[float] = []
    r1 = r3 = r5 = 0
    mrr_sum = 0.0
    ndcg_sum = 0.0
    margins: list[float] = []

    for qid, qtext in queries:
        rel_ids = qrels.get(qid) or set()
        if not rel_ids:
            continue
        tq = time.perf_counter()
        qvec = embedder.embed_query(qtext) if hasattr(embedder, "embed_query") else embedder.embed([qtext])[0]
        q_latencies.append((time.perf_counter() - tq) * 1000.0)

        scored = [(_dot(qvec, cv), did) for did, cv in zip(c_ids, c_vecs)]
        scored.sort(key=lambda x: x[0], reverse=True)

        top5 = [did for _, did in scored[:5]]
        top3 = top5[:3]
        if top5 and top5[0] in rel_ids:
            r1 += 1
        if any(d in rel_ids for d in top3):
            r3 += 1
        if any(d in rel_ids for d in top5):
            r5 += 1

        rr = 0.0
        dcg = 0.0
        for rank, did in enumerate(top5, start=1):
            if did in rel_ids:
                if rr == 0.0:
                    rr = 1.0 / rank
                dcg += 1.0 / math.log2(rank + 1.0)
        ideal_k = min(5, len(rel_ids))
        idcg = sum(1.0 / math.log2(r + 1.0) for r in range(1, ideal_k + 1))
        mrr_sum += rr
        ndcg_sum += (dcg / idcg) if idcg > 0 else 0.0

        pos_scores = [sc for sc, did in scored if did in rel_ids]
        neg_scores = [sc for sc, did in scored if did not in rel_ids]
        if pos_scores and neg_scores:
            margins.append(max(pos_scores) - neg_scores[0])

    n = max(1, len(q_latencies))
    warm_lats = q_latencies[1:] if len(q_latencies) > 1 else q_latencies
    return {
        "queries": n,
        "corpus_size": len(corpus),
        "recall_at_1": round(r1 / n, 4),
        "recall_at_3": round(r3 / n, 4),
        "recall_at_5": round(r5 / n, 4),
        "mrr_at_5": round(mrr_sum / n, 4),
        "ndcg_at_5": round(ndcg_sum / n, 4),
        "avg_margin": round(sum(margins) / max(1, len(margins)), 4),
        "corpus_embed_ms": round(corpus_ms, 1),
        "warm_query_ms": round(sum(warm_lats) / max(1, len(warm_lats)), 1),
    }


def _load_cosqa(n_queries: int = 40, corpus_cap: int = 180) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, set[str]]]:
    """Load open-source CoIR-Retrieval/cosqa (Code Search QA) slice from Hugging Face."""
    from datasets import load_dataset

    q_ds = load_dataset("CoIR-Retrieval/cosqa", "queries", split="queries")
    c_ds = load_dataset("CoIR-Retrieval/cosqa", "corpus", split="corpus")
    r_ds = load_dataset("CoIR-Retrieval/cosqa", "default", split="test")

    q_map = {str(x["_id"]): str(x["text"]) for x in q_ds}
    c_map = {str(x["_id"]): str(x["text"])[:600] for x in c_ds}

    qrels: dict[str, set[str]] = {}
    selected_q: list[tuple[str, str]] = []
    needed_c: set[str] = set()
    for row in r_ds:
        if float(row.get("score", 0)) <= 0:
            continue
        qid = str(row["query-id"])
        cid = str(row["corpus-id"])
        if qid not in q_map or cid not in c_map:
            continue
        if qid not in qrels:
            if len(selected_q) >= n_queries:
                continue
            selected_q.append((qid, q_map[qid]))
            qrels[qid] = set()
        qrels[qid].add(cid)
        needed_c.add(cid)

    corpus: list[tuple[str, str]] = [(cid, c_map[cid]) for cid in sorted(needed_c)]
    for cid, txt in c_map.items():
        if len(corpus) >= corpus_cap:
            break
        if cid not in needed_c:
            corpus.append((cid, txt))
    return selected_q, corpus, qrels


def _load_duretrieval(n_queries: int = 40, corpus_cap: int = 180) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, set[str]]]:
    """Load open-source C-MTEB/DuRetrieval (Chinese Retrieval) slice from Hugging Face."""
    from datasets import load_dataset

    q_ds = load_dataset("C-MTEB/DuRetrieval", "queries", split="dev")
    c_ds = load_dataset("C-MTEB/DuRetrieval", "corpus", split="dev")
    r_ds = load_dataset("C-MTEB/DuRetrieval-qrels", split="dev")

    q_map = {str(x["id"]): str(x["text"]) for x in q_ds}
    c_map: dict[str, str] = {}
    for idx, x in enumerate(c_ds):
        c_map[str(x["id"])] = str(x["text"])[:600]
        if idx >= 8000:
            break

    qrels: dict[str, set[str]] = {}
    selected_q: list[tuple[str, str]] = []
    needed_c: set[str] = set()
    for row in r_ds:
        if float(row.get("score", 0)) <= 0:
            continue
        qid = str(row["qid"])
        cid = str(row["pid"])
        if qid not in q_map or cid not in c_map:
            continue
        if qid not in qrels:
            if len(selected_q) >= n_queries:
                continue
            selected_q.append((qid, q_map[qid]))
            qrels[qid] = set()
        qrels[qid].add(cid)
        needed_c.add(cid)

    corpus: list[tuple[str, str]] = [(cid, c_map[cid]) for cid in sorted(needed_c)]
    for cid, txt in c_map.items():
        if len(corpus) >= corpus_cap:
            break
        if cid not in needed_c:
            corpus.append((cid, txt))
    return selected_q, corpus, qrels


def _load_personal_cross_lingual(brain_dir: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, set[str]]]:
    """15 hard cross-lingual (English -> Chinese) & paraphrased queries against user's live brain."""
    sm = SnapshotManager(brain_dir)
    with sm.open_current(read_only=True) as db:
        nodes = db.list_nodes(as_of=None)
    corpus = [(n.id, f"{n.scope} | {n.rule} | {n.rationale}") for n in nodes]
    active_ids = {n.id for n in nodes}

    raw_cases = [
        ("X01", "prefer command line tool and skill over MCP server for local AI agents", {"PRN-001"}),
        ("X02", "six-month durability criteria for admitting long-term memory rules", {"PRN-002"}),
        ("X03", "forbid SQL foreign key constraints and Spring Security in flowxy-gateway", {"DEC-003"}),
        ("X04", "gateway service must read in-memory Redpanda snapshot instead of querying business database", {"DEC-005"}),
        ("X05", "zero-copy streaming DataBuffer forwarding and ByteBuffer usage parsing", {"DEC-024"}),
        ("X06", "shared pagination API contract pageSize max 200 sort field", {"DEC-038"}),
        ("X07", "use shadcn components instead of raw native HTML button or input elements", {"DEC-039", "DEC-030", "DEC-008"}),
        ("X08", "single static Go binary embedding React frontend with pure-Go CGO-free SQLite", {"DEC-041"}),
        ("X09", "encrypt SSH private keys and passwords with XChaCha20-Poly1305 master key", {"DEC-044"}),
        ("X10", "preserve fractional decimal quota precision instead of integer truncation", {"LSN-005"}),
        ("X11", "retain disabled local channel record when uploading remote-only and hide amount from vendor", {"LSN-007"}),
        ("X12", "compute regression delta only on intersection of overlapping golden evaluation cases", {"LSN-001"}),
        ("X13", "resolve golden evaluation references through immutable evidence_ids across merges", {"LSN-002"}),
        ("X14", "run mygo CLI through scripts/mygo.mjs wrapper instead of npm launcher", {"LSN-009"}),
        ("X15", "publish signed macOS auto-update to R2 via release.sh and never delete old bucket artifacts", {"DEC-013", "DEC-014"}),
    ]
    queries: list[tuple[str, str]] = []
    qrels: dict[str, set[str]] = {}
    for qid, qtext, targets in raw_cases:
        valid = targets & active_ids
        if valid:
            queries.append((qid, qtext))
            qrels[qid] = valid
    return queries, corpus, qrels


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark FlowOS embedding engines on open-source & personal suites.")
    parser.add_argument("--brain-dir", type=Path, default=Path.home() / ".fos" / "brain")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    print("Loading benchmark suites...", flush=True)
    suites: dict[str, tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, set[str]]]] = {}
    suites["personal_cross_lingual"] = _load_personal_cross_lingual(args.brain_dir)
    for name, loader in (("CoIR_cosqa_code", _load_cosqa), ("CMTEB_DuRetrieval_zh", _load_duretrieval)):
        try:
            suites[name] = loader()
            print(f"  loaded {name}: {len(suites[name][0])} queries, {len(suites[name][1])} docs", flush=True)
        except Exception as exc:
            print(f"  [warn] skipped {name}: {exc}", flush=True)

    engines: list[tuple[str, Any]] = [
        ("local_hash (256d)", LocalHashEmbedder(dim=256)),
        ("e5-small-mlx (384d)", MLXEmbedder("mlx-community/multilingual-e5-small-mlx", fallback_dim=384)),
        ("Qwen3-Emb-4B-4bit (2560d)", MLXEmbedder("mlx-community/Qwen3-Embedding-4B-4bit-DWQ", fallback_dim=2560)),
    ]

    report: dict[str, Any] = {"engines": {}}
    for label, emb in engines:
        print(f"\n=== Benchmarking {label} ===", flush=True)
        eng_res: dict[str, Any] = {}
        for sname, (qs, cs, qr) in suites.items():
            metrics = _eval_retrieval(emb, qs, cs, qr)
            eng_res[sname] = metrics
            print(
                f"  {sname:24s} | R@1={metrics['recall_at_1']*100:5.1f}% | R@5={metrics['recall_at_5']*100:5.1f}% "
                f"| MRR={metrics['mrr_at_5']:.3f} | NDCG@5={metrics['ndcg_at_5']:.3f} "
                f"| Margin={metrics['avg_margin']:+.3f} | Query={metrics['warm_query_ms']:5.1f}ms",
                flush=True,
            )
        report["engines"][label] = eng_res
        emb.unload()
        gc.collect()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved benchmark report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
