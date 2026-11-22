"""Case-insensitive Levenshtein suggestion helper for typo recovery (learned from fops)."""

from __future__ import annotations

from typing import Iterable


def _levenshtein(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


def suggest_closest(query: str, candidates: Iterable[str]) -> str:
    """Return single closest candidate using Levenshtein / prefix distance, or ''."""
    q = query.strip().lower()
    if not q:
        return ""
    if len(q) <= 2:
        max_dist = 1
    elif len(q) >= 8:
        max_dist = 3
    else:
        max_dist = 2

    best = ""
    best_score = max_dist + 1
    for cand in candidates:
        c = cand.strip().lower()
        if not c or c == q:
            continue
        d = _levenshtein(q, c)
        if c.startswith(q) and len(c) - len(q) <= 4 and d > 1:
            d = 1
        if d < best_score:
            best_score = d
            best = cand
    return best
