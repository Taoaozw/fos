"""Immutable append-only ledger (ledger/events-YYYY-MM.jsonl) and golden eval store."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from fos.models import CompiledNode, EvalCase, LedgerAtom, now_iso
from fos.redact import redact_secrets
from fos.ulid import new_atom_id, new_ulid

_CONSTRAINT_RE = re.compile(
    r"(?:禁止|不要|必须|严禁|不能|不支持|never|must|do not|don't|avoid|--[a-z0-9-]+|\b\d+(?:\.\d+)?(?:ms|s|gb|mb|%|x)?\b)",
    re.IGNORECASE,
)


class LedgerStore:
    """Permanent source-of-truth append-only JSONL files under <data_dir>/ledger and <data_dir>/eval."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.ledger_dir = self.data_dir / "ledger"
        self.eval_dir = self.data_dir / "eval"
        self.golden_path = self.eval_dir / "golden.jsonl"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.eval_dir.mkdir(parents=True, exist_ok=True)

    def _month_file(self, ts: str) -> Path:
        ym = ts[:7] if len(ts) >= 7 and ts[4] == "-" else now_iso()[:7]
        return self.ledger_dir / f"events-{ym}.jsonl"

    def append_atom(self, atom: LedgerAtom) -> LedgerAtom:
        """Redact secrets and append an immutable atom line to its monthly shard."""
        red = redact_secrets(atom.raw)
        clean_meta = dict(atom.meta)
        if red.had_secrets:
            clean_meta["redacted_types"] = list(red.redacted_types)
        # Also sanitize string values inside meta
        for key, val in list(clean_meta.items()):
            if isinstance(val, str):
                mred = redact_secrets(val)
                if mred.had_secrets:
                    clean_meta[key] = mred.text
                    existing = list(clean_meta.get("redacted_types") or [])
                    for t in mred.redacted_types:
                        if t not in existing:
                            existing.append(t)
                    clean_meta["redacted_types"] = existing

        clean_atom = LedgerAtom(
            id=atom.id or new_atom_id(),
            ts=atom.ts or now_iso(),
            device=atom.device,
            kind=atom.kind,
            scope=atom.scope or "global",
            source=atom.source or "manual",
            raw=red.text,
            meta=clean_meta,
        )

        # Deduplicate by atom id if already persisted (idempotent outbox flush)
        if self.get_atom(clean_atom.id) is not None:
            return clean_atom

        path = self._month_file(clean_atom.ts)
        line = json.dumps(clean_atom.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
        return clean_atom

    def iter_atoms(self) -> Iterator[LedgerAtom]:
        """Iterate all atoms in chronological shard order."""
        for path in sorted(self.ledger_dir.glob("events-*.jsonl")):
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield LedgerAtom.from_dict(json.loads(line))
                    except (json.JSONDecodeError, KeyError):
                        continue

    def load_all_atoms(self) -> list[LedgerAtom]:
        atoms = list(self.iter_atoms())
        atoms.sort(key=lambda a: (a.ts, a.id))
        return atoms

    def get_atom(self, atom_id: str) -> LedgerAtom | None:
        for atom in self.iter_atoms():
            if atom.id == atom_id:
                return atom
        return None

    def get_atoms_by_ids(self, atom_ids: list[str]) -> list[LedgerAtom]:
        wanted = set(atom_ids)
        if not wanted:
            return []
        by_id: dict[str, LedgerAtom] = {}
        for atom in self.iter_atoms():
            if atom.id in wanted:
                by_id[atom.id] = atom
                if len(by_id) == len(wanted):
                    break
        return [by_id[aid] for aid in atom_ids if aid in by_id]

    # --- Golden eval store (append-only) ---

    def append_eval_case(self, case: EvalCase) -> EvalCase:
        if not case.id:
            case.id = f"evl_{new_ulid()}"
        if not case.created_at:
            case.created_at = now_iso()
        line = json.dumps(case.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.golden_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
        return case

    def load_eval_cases(self) -> list[EvalCase]:
        """Load golden eval cases; later entries with the same id supersede earlier pin/state."""
        if not self.golden_path.exists():
            return []
        by_id: dict[str, EvalCase] = {}
        order: list[str] = []
        with self.golden_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = EvalCase.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError):
                    continue
                if c.id not in by_id:
                    order.append(c.id)
                by_id[c.id] = c
        return [by_id[cid] for cid in order]

    def synthesize_cases_for_node(
        self,
        node: CompiledNode,
        *,
        superseded_node: CompiledNode | None = None,
        pinned: bool = False,
    ) -> list[EvalCase]:
        """Auto-synthesize 1-2 regression assertions for high-value lessons/decisions."""
        created: list[EvalCase] = []
        existing = self.load_eval_cases()
        existing_pairs = {(c.suite, c.node_id, c.query) for c in existing}

        # 1. Recall + Fidelity case for P0/P1 lessons, decisions, principles
        if node.type in ("lesson", "decision", "principle") and (
            node.severity in ("P0", "P1") or pinned
        ):
            query = (
                " ".join(node.trigger_when[:2])
                if node.trigger_when
                else (node.rule[:80] or node.l0)
            ).strip()
            if query and ("recall", node.id, query) not in existing_pairs:
                terms = list(dict.fromkeys(m.group(0) for m in _CONSTRAINT_RE.finditer(node.rule)))[:6]
                created.append(
                    self.append_eval_case(
                        EvalCase(
                            id=f"evl_{new_ulid()}",
                            suite="recall",
                            query=query,
                            scope=node.scope if node.scope != "global" else None,
                            expected_ids=[node.id],
                            must_preserve_terms=terms,
                            evidence_ids=list(node.evidence_ids),
                            node_id=node.id,
                            pinned=pinned or node.severity == "P0",
                            description=f"Auto recall assertion for {node.id}",
                        )
                    )
                )

        # 2. Temporal supersession case whenever a decision supersedes an older one
        if superseded_node is not None:
            query = (
                " ".join((node.trigger_when or superseded_node.trigger_when)[:2])
                or node.rule[:64]
            ).strip()
            if query and ("temporal", node.id, query) not in existing_pairs:
                created.append(
                    self.append_eval_case(
                        EvalCase(
                            id=f"evl_{new_ulid()}",
                            suite="temporal",
                            query=query,
                            scope=node.scope if node.scope != "global" else None,
                            expected_ids=[node.id],
                            forbidden_ids=[superseded_node.id],
                            node_id=node.id,
                            pinned=True,
                            description=f"Temporal check: {node.id} supersedes {superseded_node.id}",
                        )
                    )
                )

        return created

    def pin_node(self, node: CompiledNode) -> list[EvalCase]:
        """Pin an existing node into the permanent P0 regression suite."""
        cases = self.load_eval_cases()
        updated: list[EvalCase] = []
        for c in cases:
            if c.node_id == node.id or node.id in c.expected_ids:
                if not c.pinned:
                    c.pinned = True
                    self.append_eval_case(c)
                updated.append(c)
        if not updated:
            updated = self.synthesize_cases_for_node(node, pinned=True)
        return updated
