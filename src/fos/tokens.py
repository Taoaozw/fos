"""Lightweight bilingual (CJK + Latin) token estimator and FTS tokenizer."""

from __future__ import annotations

import json
import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "any", "are", "as", "at", "be", "been", "being", "but",
        "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
        "have", "here", "how", "i", "if", "in", "into", "is", "it", "its", "me",
        "my", "of", "on", "or", "our", "should", "so", "some", "that", "the",
        "their", "them", "there", "these", "they", "this", "to", "was", "we",
        "were", "what", "when", "where", "which", "who", "why", "will", "with",
        "would", "you", "your",
    }
)


def estimate_tokens(text: str) -> int:
    """Fast deterministic token count estimate for CJK + Latin text.

    Approximates cl100k/o200k tokenization within ~10-15% with zero external models:
    - CJK ideographs: ~1.2 tokens per char
    - Latin/ASCII words & punctuation: ~1 token per 4 chars
    """
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    other_chars = len(text) - cjk_chars
    est = int(cjk_chars * 1.2 + (other_chars + 3) // 4)
    return max(1, est)


def estimate_json_tokens(obj: Any) -> int:
    return estimate_tokens(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def tokenize_for_fts(text: str) -> str:
    """Produce FTS5-friendly token string supporting English identifiers, camelCase, and CJK bigrams."""
    if not text:
        return ""
    tokens: list[str] = []
    # Keep normalized Latin/technical tokens (including --mkpath, rsync, DEC-001, DataBuffer)
    for m in _WORD_RE.finditer(text):
        raw_tok = m.group(0).strip(".-:/_")
        if not raw_tok:
            continue
        tok = raw_tok.lower()
        tokens.append(tok)
        # Split kebab/snake/path AND camelCase sub-parts (e.g. DataBuffer -> data, buffer)
        camel_split = _CAMEL_BOUNDARY_RE.sub(" ", raw_tok).lower()
        for part in re.split(r"[-_./:\s]+", camel_split):
            if part and part != tok:
                tokens.append(part)

    # Emit CJK bigrams (and unigrams for 1-char or boundary precision)
    for m in _CJK_RUN_RE.finditer(text):
        run = m.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
            if len(run) <= 4:
                tokens.append(run)
    return " ".join(tokens)


def is_stopword(token: str) -> bool:
    return token in _EN_STOPWORDS


def extract_terms(text: str) -> set[str]:
    """Extract normalized content search terms (filtering high-frequency English stopwords)."""
    raw = set(tokenize_for_fts(text).split())
    content = {t for t in raw if t not in _EN_STOPWORDS}
    return content if content else raw


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text cleanly to stay within max_tokens."""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    # Binary search on prefix length
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid] + "…") <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo > 0 else ""
