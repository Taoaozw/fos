"""Secret redaction filter preventing credentials from entering the ledger."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.MULTILINE,
        ),
    ),
    ("OPENAI_KEY", re.compile(r"\bsk-(?:proj-|svcacct-|live-|test-)?[A-Za-z0-9_-]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "BEARER_TOKEN",
        re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    ),
    (
        "URL_CREDENTIALS",
        re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:\s/@]+:)([^@\s/]+)(@)"),
    ),
    (
        "GENERIC_SECRET_KV",
        re.compile(
            r"(?i)(\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|pwd|fos_token|client_secret)\b\s*[:=]\s*['\"]?)([^\s'\",;}{]{8,})(['\"]?)"
        ),
    ),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redacted_types: tuple[str, ...]

    @property
    def had_secrets(self) -> bool:
        return bool(self.redacted_types)


def redact_secrets(text: str) -> RedactionResult:
    """Replace detected credentials/tokens with deterministic [REDACTED:<TYPE>] markers."""
    if not text:
        return RedactionResult(text=text, redacted_types=())

    found: list[str] = []
    out = text

    for label, pattern in _PATTERNS:
        if label == "BEARER_TOKEN":
            def _rep_bearer(m: re.Match[str], lbl: str = label) -> str:
                if lbl not in found:
                    found.append(lbl)
                return f"{m.group(1)}[REDACTED:{lbl}]"

            out = pattern.sub(_rep_bearer, out)
        elif label == "URL_CREDENTIALS":
            def _rep_url(m: re.Match[str], lbl: str = label) -> str:
                if lbl not in found:
                    found.append(lbl)
                return f"{m.group(1)}[REDACTED:{lbl}]{m.group(3)}"

            out = pattern.sub(_rep_url, out)
        elif label == "GENERIC_SECRET_KV":
            def _rep_kv(m: re.Match[str], lbl: str = label) -> str:
                val = m.group(2)
                if val.startswith("[REDACTED:"):
                    return m.group(0)
                if lbl not in found:
                    found.append(lbl)
                return f"{m.group(1)}[REDACTED:{lbl}]{m.group(3)}"

            out = pattern.sub(_rep_kv, out)
        else:
            def _rep_plain(_m: re.Match[str], lbl: str = label) -> str:
                if lbl not in found:
                    found.append(lbl)
                return f"[REDACTED:{lbl}]"

            out = pattern.sub(_rep_plain, out)

    return RedactionResult(text=out, redacted_types=tuple(found))
