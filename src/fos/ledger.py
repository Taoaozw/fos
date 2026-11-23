"""Immutable experience ledger (`ledger_atoms`) and golden eval store (`eval_cases`) in SQLite `state.db`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from fos.models import CompiledNode, EvalCase, LedgerAtom, now_iso
from fos.redact import redact_secrets
from fos.state_db import StateDB
from fos.ulid import new_atom_id, new_ulid

_CONSTRAINT_RE = re.compile(
    r"(?:--[a-z][a-z0-9-]*|\b\d+(?:\.\d+)?(?:ms|s|gb|mb|kb|%|x)\b)",
    re.IGNORECASE,
)


class LedgerStore:
    """Permanent source-of-truth ledger & golden eval store backed by `<data_dir>/state.db`."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.db_path = self.data_dir / "state.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Ensure schema & any legacy JSONL files are migrated eagerly on init
        with StateDB(self.data_dir):
            pass

    def append_atom(self, atom: LedgerAtom) -> LedgerAtom:
        """Redact secrets and insert an immutable atom into SQLite `ledger_atoms`."""
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
        with StateDB(self.data_dir) as db:
            if db.get_atom(clean_atom.id) is not None:
                return clean_atom
            return db.insert_atom(clean_atom)

    def iter_atoms(self) -> Iterator[LedgerAtom]:
        """Iterate all atoms in chronological order."""
        yield from self.load_all_atoms()

    def load_all_atoms(self) -> list[LedgerAtom]:
        with StateDB(self.data_dir) as db:
            return db.list_atoms()

    def get_atom(self, atom_id: str) -> LedgerAtom | None:
        with StateDB(self.data_dir) as db:
            return db.get_atom(atom_id)

    def get_atoms_by_ids(self, atom_ids: list[str]) -> list[LedgerAtom]:
        if not atom_ids:
            return []
        with StateDB(self.data_dir) as db:
            return db.get_atoms_by_ids(atom_ids)

    # --- Golden eval store ---

    def append_eval_case(self, case: EvalCase) -> EvalCase:
        if not case.id:
            case.id = f"evl_{new_ulid()}"
        if not case.created_at:
            case.created_at = now_iso()
        with StateDB(self.data_dir) as db:
            return db.upsert_eval_case(case)

    def load_eval_cases(self) -> list[EvalCase]:
        """Load golden eval cases in insertion order; later updates supersede earlier pin/state."""
        with StateDB(self.data_dir) as db:
            return db.list_eval_cases()

    def synthesize_cases_for_node(
        self,
        node: CompiledNode,
        *,
        superseded_node: CompiledNode | None = None,
        pinned: bool = False,
        source: str = "manual",
    ) -> list[EvalCase]:
        """Auto-synthesize 1-2 regression assertions for high-value manual/P0 lessons & decisions."""
        created: list[EvalCase] = []
        existing = self.load_eval_cases()
        existing_pairs = {(c.suite, c.node_id, c.query) for c in existing}

        # Only human-curated writes (manual / manual:correct) or explicit `fos pin` mint golden eval cases.
        # Automated hindsight ingest (`hindsight:*`) contains raw un-distilled text (often English/verbose)
        # that `fos dream` will distill/translate later; freezing raw text as pinned golden queries causes
        # false recall regressions during distillation.
        is_curated = source.startswith("manual") or pinned
        # 1. Recall + Fidelity case for curated P0/P1 lessons, decisions, principles
        if is_curated and node.type in ("lesson", "decision", "principle") and (
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
