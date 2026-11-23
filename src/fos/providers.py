"""Pluggable model providers: Embedder, Reranker, and Reasoner (MLX / CLI / OpenAI / Heuristic)."""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from fos.config import ProviderConfig
from fos.tokens import extract_stemmed_terms, extract_terms, tokenize_for_fts


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def unload(self) -> None: ...


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    def rerank(self, query: str, docs: list[tuple[str, str]]) -> dict[str, float]: ...

    def unload(self) -> None: ...


class Reasoner(Protocol):
    @property
    def model_name(self) -> str: ...

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None: ...

    def unload(self) -> None: ...


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------


class LocalHashEmbedder:
    """Deterministic bilingual subword + token feature-hashing embedder (zero external deps)."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return f"local_hash:{self._dim}"

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        toks = tokenize_for_fts(text).split()
        # Add char trigrams for fuzzy morphology & CJK substring similarity
        compact = re.sub(r"\s+", " ", text.lower()).strip()
        features: list[tuple[str, float]] = [(f"t:{t}", 1.5) for t in toks]
        if len(compact) >= 3:
            for i in range(min(len(compact) - 2, 240)):
                features.append((f"g:{compact[i : i + 3]}", 0.5))

        for feat, weight in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self._dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def unload(self) -> None:
        return


class MLXEmbedder:
    """Lazy-loaded Apple Silicon MLX embedding provider (`mlx-embeddings`)."""

    def __init__(self, model_name: str, fallback_dim: int = 256) -> None:
        self._model_name = model_name
        self._fallback_dim = fallback_dim
        self._model: Any = None
        self._tokenizer: Any = None
        self._dim: int | None = None

    @property
    def model_name(self) -> str:
        return f"mlx:{self._model_name}"

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from mlx_embeddings import load  # type: ignore[import-not-found]

        self._model, self._tokenizer = load(self._model_name)

    @property
    def dim(self) -> int:
        # Keep dim lazy so read-only commands like `fos boot` never load GPU weights at startup
        return self._dim if self._dim is not None else self._fallback_dim

    def _format_texts(self, texts: list[str], *, is_query: bool) -> list[str]:
        low = self._model_name.lower()
        if "qwen3-embedding" in low:
            if is_query:
                inst = "Instruct: Retrieve relevant personal engineering rules, architecture decisions, and lessons.\nQuery: "
                return [t if t.startswith("Instruct:") else f"{inst}{t}" for t in texts]
            return texts
        if "e5" in low:
            prefix = "query: " if is_query else "passage: "
            return [t if t.startswith(("query:", "passage:")) else f"{prefix}{t}" for t in texts]
        return texts

    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        try:
            self._ensure_loaded()
            from mlx_embeddings import generate  # type: ignore[import-not-found]

            prepared = self._format_texts(texts, is_query=is_query)
            normed: list[list[float]] = []
            batch_size = 16
            for start in range(0, len(prepared), batch_size):
                chunk = prepared[start : start + batch_size]
                output = generate(self._model, self._tokenizer, texts=chunk)
                embs = getattr(output, "text_embeds", output)
                rows = embs.tolist() if hasattr(embs, "tolist") else [list(r) for r in embs]
                for row in rows:
                    vals = [float(x) for x in row]
                    n = math.sqrt(sum(v * v for v in vals))
                    normed.append([v / n for v in vals] if n > 0 else vals)
            if normed:
                self._dim = len(normed[0])
            return normed
        except ImportError:
            return LocalHashEmbedder(dim=self._fallback_dim).embed(texts)

    def embed_query(self, query: str) -> list[float]:
        vecs = self.embed([query], is_query=True)
        return vecs[0] if vecs else []

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:
            pass


class OpenAIEmbedder:
    """OpenAI-compatible HTTP embeddings provider."""

    def __init__(self, base_url: str, model_name: str, api_key_env: str = "OPENAI_API_KEY", dim: int = 1536) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._api_key_env = api_key_env
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return f"openai:{self._model_name}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import httpx

        api_key = os.environ.get(self._api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = httpx.post(
            f"{self._base_url}/embeddings",
            headers=headers,
            json={"model": self._model_name, "input": texts},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        rows = [[float(x) for x in item["embedding"]] for item in sorted(data, key=lambda d: d["index"])]
        if rows:
            self._dim = len(rows[0])
        return rows

    def unload(self) -> None:
        return


# ---------------------------------------------------------------------------
# Rerankers
# ---------------------------------------------------------------------------


class LexicalReranker:
    """Fast bilingual cross-scorer combining exact phrase match and token F1 overlap."""

    @property
    def model_name(self) -> str:
        return "lexical"

    def rerank(self, query: str, docs: list[tuple[str, str]]) -> dict[str, float]:
        q_low = query.strip().lower()
        q_exact = extract_terms(query)
        q_terms = extract_stemmed_terms(query)
        doc_terms: list[tuple[str, str, set[str], set[str]]] = [
            (doc_id, text.lower(), extract_stemmed_terms(text), extract_terms(text))
            for doc_id, text in docs
        ]
        n_docs = max(1, len(doc_terms))
        df: dict[str, int] = {}
        for t in q_terms:
            df[t] = sum(1 for _, _, dt, _ in doc_terms if t in dt)
        idf: dict[str, float] = {t: math.log((n_docs + 1.0) / (df[t] + 1.0)) + 1.0 for t in q_terms}
        total_idf = sum(idf.values()) or 1.0

        scores: dict[str, float] = {}
        for doc_id, t_low, d_terms, d_exact in doc_terms:
            if not q_terms or not d_terms:
                scores[doc_id] = 0.0
                continue
            overlap = q_terms & d_terms
            exact_overlap = q_exact & d_exact
            # Dampen short-doc precision inflation (+6 smoothing) so 1-line boilerplate doesn't beat fuller docs
            prec = len(overlap) / (len(d_terms) + 6.0)
            rec = sum(idf[t] for t in overlap) / total_idf
            raw_rec = len(overlap) / len(q_terms)
            f1 = (2 * prec * raw_rec / (prec + raw_rec)) if (prec + raw_rec) > 0 else 0.0
            phrase_bonus = 0.25 if (len(q_low) >= 4 and q_low in t_low) else 0.0
            # Reward rare exact query terms (e.g. 外键, shadcn, DataBuffer) & technical identifiers
            rare_hits = sum(1 for t in exact_overlap if df.get(t, n_docs) <= 2 and len(t) >= 2)
            rare_bonus = min(0.24, 0.12 * rare_hits)
            special_q = {t for t in q_exact if len(t) >= 4 and any(ch.isdigit() or ch in "-_" for ch in t)}
            special_bonus = 0.15 * len(special_q & d_exact) if special_q else 0.0
            scores[doc_id] = min(1.0, 0.55 * rec + 0.25 * f1 + phrase_bonus + rare_bonus + special_bonus)
        return scores

    def unload(self) -> None:
        return


class MLXReranker:
    """Local MLX cross-encoder/LLM reranker with lexical fallback."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._tokenizer: Any = None
        self._fallback = LexicalReranker()

    @property
    def model_name(self) -> str:
        return f"mlx:{self._model_name}"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        try:
            from mlx_lm import load  # type: ignore[import-not-found]

            self._model, self._tokenizer = load(self._model_name)
            return True
        except Exception:
            return False

    def rerank(self, query: str, docs: list[tuple[str, str]]) -> dict[str, float]:
        if not docs:
            return {}
        if not self._ensure_loaded():
            return self._fallback.rerank(query, docs)
        try:
            from mlx_lm import generate  # type: ignore[import-not-found]

            lex = self._fallback.rerank(query, docs)
            scores: dict[str, float] = {}
            for doc_id, text in docs:
                prompt = (
                    f"Rate relevance from 0.0 to 1.0 as a single number.\n"
                    f"Query: {query}\nDocument: {text[:400]}\nScore:"
                )
                out = generate(self._model, self._tokenizer, prompt=prompt, max_tokens=8, verbose=False)
                m = re.search(r"([01](?:\.\d+)?)", str(out))
                llm_score = max(0.0, min(1.0, float(m.group(1)))) if m else lex[doc_id]
                scores[doc_id] = 0.7 * llm_score + 0.3 * lex[doc_id]
            return scores
        except Exception:
            return self._fallback.rerank(query, docs)

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reasoners (Dream compiler & Behavior Judge)
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class HeuristicReasoner:
    """Zero-cost deterministic reasoner; returns None so dream/eval run built-in rules."""

    @property
    def model_name(self) -> str:
        return "heuristic"

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None:
        return None

    def unload(self) -> None:
        return


class MLXReasoner:
    """On-demand Apple Silicon MLX LLM reasoner; unloads to release unified memory after Dream."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def model_name(self) -> str:
        return f"mlx:{self._model_name}"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        try:
            from mlx_lm import load  # type: ignore[import-not-found]

            self._model, self._tokenizer = load(self._model_name)
            return True
        except Exception:
            return False

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None:
        if not self._ensure_loaded():
            return None
        try:
            from mlx_lm import generate  # type: ignore[import-not-found]

            full = f"{system}\n\n{prompt}\n\nRespond with valid JSON only." if system else f"{prompt}\n\nRespond with valid JSON only."
            if hasattr(self._tokenizer, "apply_chat_template"):
                msgs = []
                if system:
                    msgs.append({"role": "system", "content": system})
                msgs.append({"role": "user", "content": prompt + "\nRespond with valid JSON only."})
                full = self._tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            out = generate(self._model, self._tokenizer, prompt=full, max_tokens=512, verbose=False)
            return _extract_json_object(str(out))
        except Exception:
            return None

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:
            pass


class CLISubprocessReasoner:
    """Non-interactive CLI provider adapter (`codex exec` / `claude -p` / `agy -p`)."""

    def __init__(self, command: str = "codex exec", timeout_s: int = 180) -> None:
        self._command = command
        self._timeout_s = timeout_s

    @property
    def model_name(self) -> str:
        return f"cli:{self._command}"

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None:
        import tempfile
        from pathlib import Path

        full_prompt = (
            f"{system}\n\n{prompt}\n\nReturn strict valid JSON only (no markdown fences, no commentary)."
            if system
            else f"{prompt}\n\nReturn strict valid JSON only (no markdown fences, no commentary)."
        )
        tokens = shlex.split(self._command)
        is_codex = len(tokens) >= 2 and Path(tokens[0]).name == "codex" and tokens[1] == "exec"

        try:
            if is_codex:
                with tempfile.TemporaryDirectory(prefix="fos-reasoner-") as tmp_dir:
                    out_file = Path(tmp_dir) / "last.txt"
                    argv = tokens + [
                        "-s",
                        "read-only",
                        "-C",
                        tmp_dir,
                        "--skip-git-repo-check",
                        "--ephemeral",
                        "-o",
                        str(out_file),
                        full_prompt,
                    ]
                    proc = subprocess.run(
                        argv,
                        capture_output=True,
                        text=True,
                        timeout=self._timeout_s,
                        check=False,
                    )
                    if proc.returncode != 0:
                        return None
                    raw_out = out_file.read_text(encoding="utf-8") if out_file.exists() else proc.stdout
                    return _extract_json_object(raw_out)

            argv = tokens + [full_prompt]
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
            if proc.returncode != 0:
                return None
            return _extract_json_object(proc.stdout)
        except (OSError, subprocess.SubprocessError):
            return None

    def unload(self) -> None:
        return


class OpenAIReasoner:
    """OpenAI-compatible HTTP chat completions JSON reasoner."""

    def __init__(self, base_url: str, model_name: str, api_key_env: str = "OPENAI_API_KEY") -> None:
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._api_key_env = api_key_env

    @property
    def model_name(self) -> str:
        return f"openai:{self._model_name}"

    def complete_json(self, prompt: str, *, system: str = "") -> dict[str, Any] | None:
        import httpx

        api_key = os.environ.get(self._api_key_env, "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt + "\nRespond with valid JSON only."})
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json={"model": self._model_name, "messages": messages, "temperature": 0.1},
                timeout=60.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json_object(content)
        except Exception:
            return None

    def unload(self) -> None:
        return


@dataclass
class ProviderBundle:
    embedder: Embedder
    reranker: Reranker
    reasoner: Reasoner

    def unload_heavy(self) -> None:
        self.reasoner.unload()


def build_providers(cfg: ProviderConfig) -> ProviderBundle:
    if cfg.embedder == "mlx":
        embedder: Embedder = MLXEmbedder(cfg.embed_model, fallback_dim=cfg.embed_dim)
    elif cfg.embedder == "openai" and cfg.openai_base_url:
        embedder = OpenAIEmbedder(cfg.openai_base_url, cfg.embed_model, cfg.openai_api_key_env, cfg.embed_dim)
    else:
        embedder = LocalHashEmbedder(dim=cfg.embed_dim)

    if cfg.reranker == "mlx":
        reranker: Reranker = MLXReranker(cfg.rerank_model)
    else:
        reranker = LexicalReranker()

    if cfg.reasoner == "mlx":
        reasoner: Reasoner = MLXReasoner(cfg.reasoner_model)
    elif cfg.reasoner == "cli":
        reasoner = CLISubprocessReasoner(cfg.cli_command)
    elif cfg.reasoner == "openai" and cfg.openai_base_url:
        reasoner = OpenAIReasoner(cfg.openai_base_url, cfg.reasoner_model, cfg.openai_api_key_env)
    else:
        reasoner = HeuristicReasoner()

    return ProviderBundle(embedder=embedder, reranker=reranker, reasoner=reasoner)
