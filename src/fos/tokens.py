"""Lightweight bilingual (CJK + Latin) token estimator and FTS tokenizer."""

from __future__ import annotations

import json
import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")


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
    """Produce FTS5-friendly token string supporting both English identifiers and CJK bigrams."""
    if not text:
        return ""
    tokens: list[str] = []
    # Keep normalized Latin/technical tokens (including --mkpath, rsync, DEC-001)
    for m in _WORD_RE.finditer(text.lower()):
        tok = m.group(0).strip(".-:/_")
        if tok:
            tokens.append(tok)
            # Also split sub-parts of kebab/snake/path tokens for broader BM25 match
            for part in re.split(r"[-_./:]+", tok):
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


def extract_terms(text: str) -> set[str]:
    """Extract normalized search terms for overlap scoring and lexical checks."""
    return set(tokenize_for_fts(text).split())


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
