"""Zero-dependency monotonic ULID generator (Crockford Base32, 26 chars)."""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOCK = threading.Lock()
_LAST_MS = -1
_LAST_RAND = 0


def _encode_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid(timestamp_ms: int | None = None) -> str:
    """Generate a lexicographically sortable 26-char monotonic ULID."""
    global _LAST_MS, _LAST_RAND
    ms = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    if ms < 0 or ms >= (1 << 48):
        raise ValueError(f"ULID timestamp out of 48-bit range: {ms}")

    with _LOCK:
        if timestamp_ms is None and ms <= _LAST_MS:
            ms = _LAST_MS
            _LAST_RAND = (_LAST_RAND + 1) & ((1 << 80) - 1)
            rand = _LAST_RAND
        else:
            rand = int.from_bytes(os.urandom(10), "big")
            if timestamp_ms is None:
                _LAST_MS = ms
                _LAST_RAND = rand

    return _encode_base32(ms, 10) + _encode_base32(rand, 16)


def new_atom_id() -> str:
    return f"atm_{new_ulid()}"
