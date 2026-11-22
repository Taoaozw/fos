"""Core AI-native data models for FlowOS."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

AtomKind = Literal["thought", "decision", "lesson", "outcome", "principle"]
VALID_KINDS: tuple[str, ...] = ("thought", "decision", "lesson", "outcome", "principle")

NodeStatus = Literal["active", "superseded", "archived"]
Severity = Literal["P0", "P1", "P2"]
EdgeType = Literal["SUPERSEDES", "CONTRADICTS", "CAUSED_BY", "SUPPORTS", "APPLIES_TO"]
VALID_EDGE_TYPES: tuple[str, ...] = (
    "SUPERSEDES",
    "CONTRADICTS",
    "CAUSED_BY",
    "SUPPORTS",
    "APPLIES_TO",
)

KIND_PREFIX: dict[str, str] = {
    "lesson": "LSN",
    "decision": "DEC",
    "thought": "THT",
    "outcome": "OUT",
    "principle": "PRN",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_severity(raw: str | None, kind: str = "thought") -> str:
    if not raw:
        return "P0" if kind == "principle" else ("P1" if kind in ("lesson", "decision") else "P2")
    val = raw.strip().lower()
    if val in ("p0", "critical", "high", "blocker"):
        return "P0"
    if val in ("p1", "medium", "med", "normal"):
        return "P1"
    return "P2"


@dataclass
class LedgerAtom:
    """Immutable verbatim experience atom written to ledger/events-YYYY-MM.jsonl."""

    id: str
    ts: str
    device: str
    kind: str
    scope: str
    source: str
    raw: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "device": self.device,
            "kind": self.kind,
            "scope": self.scope,
            "source": self.source,
            "raw": self.raw,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerAtom:
        return cls(
            id=str(data["id"]),
            ts=str(data["ts"]),
            device=str(data.get("device", "unknown")),
            kind=str(data.get("kind", "thought")),
            scope=str(data.get("scope", "global")),
            source=str(data.get("source", "manual")),
            raw=str(data.get("raw", "")),
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class CompiledNode:
    """Compiled AI-native cognitive node (L0 wakeup + L1 compact card + L2 evidence refs)."""

    id: str
    type: str
    scope: str
    status: str = "active"
    severity: str = "P1"
    valid_from: str = ""
    valid_until: str | None = None
    superseded_by: str | None = None
    seen_count: int = 1
    seen_dates: list[str] = field(default_factory=list)
    confidence: float = 0.8
    trigger_when: list[str] = field(default_factory=list)
    rule: str = ""
    rationale: str = ""
    guard_hint: str = ""
    guard_pattern: str = ""  # Deterministic static guard (e.g. `cmd:mygo build -upload` or `path:*.tsx !components/ui :: <button\b`)
    graduated: bool = False  # True once mechanically enforced via CI/lint/fos guard (excluded from L0 boot)
    source_ref: str = ""  # Authoritative pointer (e.g. /abs/path/docs/DESIGN.md#section or git:repo@sha)
    source_hash: str = ""  # 16-char SHA-256 of authoritative source content at capture/verify time
    verified_at: str = ""  # YYYY-MM-DD when last verified against authoritative source
    entities: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    l0: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_l1_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "scope": self.scope,
            "status": self.status,
            "severity": self.severity,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "superseded_by": self.superseded_by,
            "seen_count": self.seen_count,
            "seen_dates": list(self.seen_dates),
            "confidence": round(float(self.confidence), 3),
            "trigger_when": list(self.trigger_when),
            "rule": self.rule,
            "rationale": self.rationale,
            "guard_hint": self.guard_hint,
            "guard_pattern": self.guard_pattern,
            "graduated": bool(self.graduated),
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "verified_at": self.verified_at,
            "entities": list(self.entities),
            "contradicts": list(self.contradicts),
            "evidence_ids": list(self.evidence_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        d = self.to_l1_dict()
        d["l0"] = self.l0
        d["created_at"] = self.created_at
        d["updated_at"] = self.updated_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompiledNode:
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            scope=str(data.get("scope", "global")),
            status=str(data.get("status", "active")),
            severity=str(data.get("severity", "P1")),
            valid_from=str(data.get("valid_from", "")),
            valid_until=data.get("valid_until"),
            superseded_by=data.get("superseded_by"),
            seen_count=int(data.get("seen_count", 1)),
            seen_dates=list(data.get("seen_dates") or []),
            confidence=float(data.get("confidence", 0.8)),
            trigger_when=list(data.get("trigger_when") or []),
            rule=str(data.get("rule", "")),
            rationale=str(data.get("rationale", "")),
            guard_hint=str(data.get("guard_hint", "")),
            guard_pattern=str(data.get("guard_pattern", "")),
            graduated=bool(data.get("graduated", False)),
            source_ref=str(data.get("source_ref", "")),
            source_hash=str(data.get("source_hash", "")),
            verified_at=str(data.get("verified_at", "")),
            entities=list(data.get("entities") or []),
            contradicts=list(data.get("contradicts") or []),
            evidence_ids=list(data.get("evidence_ids") or []),
            l0=str(data.get("l0", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class GraphEdge:
    """Bi-temporal directed relation between compiled nodes."""

    src_id: str
    dst_id: str
    edge_type: str
    valid_from: str
    valid_until: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalCase:
    """Golden regression test case stored in eval/golden.jsonl."""

    id: str
    suite: str  # recall | temporal | fidelity | behavior
    query: str
    scope: str | None = None
    as_of: str | None = None
    expected_ids: list[str] = field(default_factory=list)
    forbidden_ids: list[str] = field(default_factory=list)
    must_preserve_terms: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    node_id: str | None = None
    pinned: bool = False
    description: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalCase:
        return cls(
            id=str(data["id"]),
            suite=str(data["suite"]),
            query=str(data.get("query", "")),
            scope=data.get("scope"),
            as_of=data.get("as_of"),
            expected_ids=list(data.get("expected_ids") or []),
            forbidden_ids=list(data.get("forbidden_ids") or []),
            must_preserve_terms=list(data.get("must_preserve_terms") or []),
            evidence_ids=list(data.get("evidence_ids") or []),
            node_id=data.get("node_id"),
            pinned=bool(data.get("pinned", False)),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", "")),
        )


def parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO8601 or YYYY / YYYY-MM / YYYY-MM-DD timestamps into UTC-aware datetime."""
    if not ts:
        return None
    s = ts.strip()
    try:
        if len(s) == 4 and s.isdigit():
            return datetime(int(s), 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        if len(s) == 7 and s[4] == "-":
            year, month = int(s[:4]), int(s[5:7])
            # End of month so --as-of 2026-08 includes everything in 2026-08
            if month == 12:
                return datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
            next_m = datetime(year, month + 1, 1, tzinfo=timezone.utc)
            return datetime.fromtimestamp(next_m.timestamp() - 1, tz=timezone.utc)
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), 23, 59, 59, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def is_valid_at(valid_from: str, valid_until: str | None, as_of: str | None) -> bool:
    """Evaluate bi-temporal validity interval [valid_from, valid_until) at point as_of."""
    if as_of is None:
        return valid_until is None
    target = parse_timestamp(as_of)
    if target is None:
        return valid_until is None
    start = parse_timestamp(valid_from)
    end = parse_timestamp(valid_until)
    if start is not None and start > target:
        return False
    if end is not None and end <= target:
        return False
    return True
