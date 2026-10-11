"""Select smaller, paired study matrices from declared scientific contexts.

This is a planning operation. It never reads results, execution state, model
responses, or resource usage, and never changes a task's scientific contract.
The caller records the selection as a protocol revision before dispatching it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
import uuid
from typing import Any, Mapping, Sequence


def _identity(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _key(value: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    missing = [name for name in fields if name not in value]
    if missing:
        raise ValueError(f"Selection context is missing declared fields: {missing}")
    return tuple(_identity(value[name]) for name in fields)


def _order(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0, value
    return 1, _identity(value)


@dataclass(frozen=True)
class SelectionRule:
    """A public, outcome-independent rule for one original matrix category."""

    category: str
    context_count: int
    arms: tuple[str, ...]
    context_fields: tuple[str, ...]
    group_field: str
    group_order: tuple[str, ...]
    ordinal_fields: tuple[str, ...]
    partition: str = "core"
    repeat_field: str | None = "repeat"
    unique_axes: tuple[tuple[str, ...], ...] = ()
    alternating_field: str | None = None
    alternating_order: tuple[str, ...] = ()
    group_offsets: Mapping[str, int] = field(default_factory=dict)
    reference_category: str | None = None
    reference_fields: tuple[str, ...] = ()
    balance_fields: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelectionRule":
        names = cls.__dataclass_fields__
        unknown = set(value) - set(names)
        if unknown:
            raise ValueError(f"Unknown selection rule fields: {sorted(unknown)}")
        data = dict(value)
        for name in ("arms", "context_fields", "group_order", "ordinal_fields",
                     "alternating_order", "reference_fields", "balance_fields"):
            if name in data:
                data[name] = tuple(data[name])
        if "unique_axes" in data:
            data["unique_axes"] = tuple(tuple(axis) for axis in data["unique_axes"])
        return cls(**data)

    def validate(self) -> None:
        if isinstance(self.context_count, bool) or not isinstance(self.context_count, int) or self.context_count < 1:
            raise ValueError("context_count must be a positive integer")
        if not self.category or not self.arms or len(set(self.arms)) != len(self.arms):
            raise ValueError("A category and distinct comparison arms are required")
        if not self.context_fields or not self.ordinal_fields:
            raise ValueError("Explicit context and ordinal fields are required")
        if not self.group_order or len(set(self.group_order)) != len(self.group_order):
            raise ValueError("Explicit distinct group order is required")
        if bool(self.alternating_field) != bool(self.alternating_order):
            raise ValueError("Alternating selection needs both a field and its declared order")
        if self.reference_category and not self.reference_fields:
            raise ValueError("A reference category needs explicit context matching fields")
        if self.partition not in {"core", "supplement"}:
            raise ValueError("Selection partition must be core or supplement")
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in self.group_offsets.values()):
            raise ValueError("Group offsets must be nonnegative ordinal integers")
        if set(self.group_offsets) - set(self.group_order):
            raise ValueError("A group offset names an undeclared group")


def _public_cells(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read matrix definitions only; task family enrichment does not edit cells."""
    cells = manifest.get("cells", [])
    if not isinstance(cells, list) or not all(isinstance(row, Mapping) for row in cells):
        raise ValueError("manifest.cells must be a list of matrix definitions")
    tasks = {str(row["task_id"]): row for row in manifest.get("tasks", [])}
    result = []
    for cell in cells:
        value = dict(cell)
        task = tasks.get(str(value.get("task_id", "")), {})
        if "family" not in value and task.get("family_id"):
            value["family"] = task["family_id"]
        result.append(value)
    return result


def _paired_contexts(cells: Sequence[Mapping[str, Any]], rule: SelectionRule) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, ...], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    representatives: dict[tuple[str, ...], dict[str, Any]] = {}
    for cell in cells:
        if cell.get("category") != rule.category:
            continue
        arm = str(cell.get("arm", ""))
        if arm not in rule.arms:
            raise ValueError(f"Category {rule.category} contains an undeclared arm: {arm}")
        context = _key(cell, rule.context_fields)
        if arm in pairs[context]:
            raise ValueError(f"Duplicate arm {arm} for the same {rule.category} context")
        pairs[context][arm] = cell
        representatives.setdefault(context, dict(cell))
    candidates = []
    for context, by_arm in pairs.items():
        if set(by_arm) != set(rule.arms):
            raise ValueError(f"Incomplete comparison arms in {rule.category}: {sorted(by_arm)}")
        representative = representatives[context]
        # These fields influence inclusion; every arm must describe the same
        # context so an arm's input order cannot affect the selection.
        shared = set(rule.context_fields) | set(rule.ordinal_fields) | {rule.group_field}
        shared.update(name for axis in rule.unique_axes for name in axis)
        shared.update(rule.balance_fields)
        if rule.repeat_field:
            shared.add(rule.repeat_field)
        if rule.alternating_field:
            shared.add(rule.alternating_field)
        for cell in by_arm.values():
            for name in shared:
                if name not in representative or name not in cell or _identity(cell[name]) != _identity(representative[name]):
                    raise ValueError(f"Comparison arms disagree on selection field {name!r}")
        candidates.append({"context": representative, "cells": by_arm})
    if not candidates:
        raise ValueError(f"No original paired contexts for category {rule.category}")
    if rule.repeat_field:
        lowest = min(_order(item["context"][rule.repeat_field]) for item in candidates)
        candidates = [item for item in candidates if _order(item["context"][rule.repeat_field]) == lowest]
    if rule.reference_category:
        references = {_key(cell, rule.reference_fields) for cell in cells
                      if cell.get("category") == rule.reference_category}
        candidates = [item for item in candidates if _key(item["context"], rule.reference_fields) in references]
    return candidates


def select_contexts(cells: Sequence[Mapping[str, Any]], rule: SelectionRule) -> list[dict[str, Any]]:
    """Round-robin declared groups, retaining identical contexts in every arm.

    Local order uses public task/context ordinals. Optional stream alternation
    uses the selected context ordinal; uniqueness excludes already represented
    task or change axes. The procedure visits a finite original candidate set.
    It raises if the declared request is infeasible instead of weakening it.
    """
    rule.validate()
    candidates = _paired_contexts(cells, rule)
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in rule.group_order}
    for item in candidates:
        name = str(item["context"][rule.group_field])
        if name not in groups:
            raise ValueError(f"Candidate group {name!r} is missing from the declared group order")
        groups[name].append(item)
    for name, group in groups.items():
        group.sort(key=lambda item: tuple(_order(item["context"][field]) for field in rule.ordinal_fields))
        offset = rule.group_offsets.get(name, 0)
        if group and offset >= len(group):
            raise ValueError(f"Group offset for {name!r} exceeds the available context ordinals")
        groups[name] = group[offset:] + group[:offset]
    selected = []
    used = [set() for _ in rule.unique_axes]
    while len(selected) < rule.context_count:
        advanced = False
        for name in rule.group_order:
            legal = [item for item in groups[name] if all(_key(item["context"], axis) not in used[index]
                     for index, axis in enumerate(rule.unique_axes))]
            if not legal:
                continue
            if rule.alternating_field:
                preferred = rule.alternating_order[len(selected) % len(rule.alternating_order)]
                matching = [item for item in legal if item["context"][rule.alternating_field] == preferred]
                if not matching:
                    raise ValueError(f"Cannot preserve declared {rule.alternating_field} alternation in group {name!r}")
                legal = matching
            item = legal[0]
            selected.append(item)
            groups[name].remove(item)
            for index, axis in enumerate(rule.unique_axes):
                used[index].add(_key(item["context"], axis))
            advanced = True
            if len(selected) == rule.context_count:
                break
        if not advanced:
            raise ValueError(f"Only {len(selected)} legal paired contexts are available; requested {rule.context_count}")
    return selected


def build_selection(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return an auditable selection without changing a manifest or ledger."""
    revision_id = str(plan.get("selection_revision_id", ""))
    if not revision_id:
        raise ValueError("An explicit selection revision ID is required")
    cells = _public_cells(manifest)
    identities = [str(row.get("matrix_cell_id") or row.get("episode_id") or "") for row in cells]
    work_ids = [str(row.get("episode_id") or "") for row in cells]
    if not all(identities) or len(set(identities)) != len(identities):
        raise ValueError("Every original matrix cell must have a distinct logical identity")
    if not all(work_ids) or len(set(work_ids)) != len(work_ids):
        raise ValueError("Every original matrix cell must have a distinct execution identity")
    rules = [SelectionRule.from_dict(value) for value in plan.get("rules", [])]
    if not rules or len({rule.category for rule in rules}) != len(rules):
        raise ValueError("Exactly one declared rule per selected category is required")
    summaries = []
    selected_cells = []
    for rule in rules:
        contexts = select_contexts(cells, rule)
        chosen = [dict(item["cells"][arm]) for item in contexts for arm in rule.arms]
        selected_cells.extend(chosen)
        summaries.append({
            "category": rule.category, "partition": rule.partition,
            "contexts": [{name: item["context"][name] for name in rule.context_fields} for item in contexts],
            "context_count": len(contexts), "unit_count": len(chosen),
            "arm_counts": dict(sorted(Counter(cell["arm"] for cell in chosen).items())),
            "balance": {name: dict(sorted(Counter(str(item["context"][name]) for item in contexts).items()))
                        for name in rule.balance_fields},
        })
    selected_ids = [str(cell.get("matrix_cell_id") or cell["episode_id"]) for cell in selected_cells]
    selected_work_ids = [str(cell["episode_id"]) for cell in selected_cells]
    selected_set = set(selected_ids)
    partition_counts = {partition: sum(row["unit_count"] for row in summaries if row["partition"] == partition)
                        for partition in ("core", "supplement")}
    if "expected_counts" in plan:
        expected = plan["expected_counts"]
        actual = {"total": len(selected_cells), **partition_counts}
        if dict(expected) != actual:
            raise ValueError(f"Declared size does not match selected size: expected {expected}, actual {actual}")
    return {
        "selection_revision_id": revision_id,
        "original_unit_count": len(cells), "selected_unit_count": len(selected_cells),
        "partition_counts": partition_counts, "categories": summaries,
        "selected_matrix_cell_ids": selected_ids,
        "excluded_matrix_cell_ids": sorted(set(identities) - selected_set),
        "selected_work_ids": selected_work_ids,
        "excluded_work_ids": sorted(set(work_ids) - set(selected_work_ids)),
        "selected_cells": selected_cells,
        "rule": dict(plan),
        "selection_basis": "declared public context ordinals; no outcomes or execution state",
    }


def _nonexecution_planning_result(encoded: str | None) -> bool:
    """Recognize a planning-only receipt without discarding its original record."""
    if encoded is None:
        return False
    try:
        result = json.loads(encoded)
    except (TypeError, ValueError):
        return False
    if not isinstance(result, Mapping) or str(result.get("status", "")).upper() != "NOT_RUN":
        return False
    # Explicit zero observations are required. Missing fields and booleans do
    # not establish that a provider or GPU process was never dispatched.
    return all(isinstance(result.get(name), (int, float)) and not isinstance(result.get(name), bool)
               and result[name] == 0 for name in ("physical_model_requests", "gpu_actual_seconds"))


def apply_selection(ledger: Any, *, campaign_id: str, external_ledger_id: str,
                    report: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically withdraw unexecuted excluded work in the original ledger.

    ``ledger`` is the existing ``StudyLedger`` instance. Selection never deletes
    old work, results, actions, or resource records. Active or ambiguous excluded
    work must be reconciled by its executor before the scope change is applied.
    """
    if not external_ledger_id or ledger.ledger_id != external_ledger_id:
        raise ValueError("Selection must use the campaign's original external ledger")
    revision_id = str(report.get("selection_revision_id", ""))
    selected = list(map(str, report.get("selected_work_ids", [])))
    excluded = list(map(str, report.get("excluded_work_ids", [])))
    if (not revision_id or not selected or len(set(selected)) != len(selected)
            or len(set(excluded)) != len(excluded) or set(selected) & set(excluded)):
        raise ValueError("Selection identities are incomplete, duplicated, or overlapping")
    encoded = _identity(dict(report))
    withdrawn = []
    restored = []
    preserved_terminal = []
    preserved_planning = []
    now = time.time()
    with ledger.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = db.execute("SELECT ledger_id FROM forest_study_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not campaign or campaign[0] != external_ledger_id:
            raise ValueError("Campaign is not registered with the original external ledger")
        db.execute("""CREATE TABLE IF NOT EXISTS forest_study_selections (
            campaign_id TEXT NOT NULL, selection_revision_id TEXT NOT NULL,
            external_ledger_id TEXT NOT NULL, report_json TEXT NOT NULL,
            application_json TEXT NOT NULL, recorded_at REAL NOT NULL,
            PRIMARY KEY(campaign_id,selection_revision_id))""")
        prior = db.execute("SELECT report_json,application_json FROM forest_study_selections WHERE campaign_id=? AND selection_revision_id=?",
                           (campaign_id, revision_id)).fetchone()
        if prior:
            if prior[0] != encoded:
                raise ValueError("A selection revision cannot be reused with different membership or rules")
            return {**json.loads(prior[1]), "already_applied": True}
        rows = {}
        for work_id in selected + excluded:
            row = db.execute("SELECT * FROM forest_study_work WHERE campaign_id=? AND work_id=?", (campaign_id, work_id)).fetchone()
            if row is None:
                raise ValueError(f"Selection work is not registered in the original campaign: {work_id}")
            rows[work_id] = row
        for work_id in excluded:
            row = rows[work_id]
            if row["state"] in {"completed", "failed", "cancelled", "not_run", "budget_exhausted"}:
                preserved_terminal.append(work_id)
                continue
            if (row["state"] != "queued" or row["lease_owner"] is not None
                    or row["supervisor_work_id"] is not None or row["decision_json"] is not None
                    or (row["result_json"] is not None and not _nonexecution_planning_result(row["result_json"]))
                    or ledger._has_execution_evidence(db, campaign_id, work_id)):
                raise ValueError(f"Excluded work requires executor reconciliation before withdrawal: {work_id} ({row['state']})")
            if row["result_json"] is not None:
                preserved_planning.append(work_id)
            withdrawn.append(work_id)
        reason = f"protocol_scope_reduction: excluded_by_selection_revision={revision_id}"
        for work_id in selected:
            row = rows[work_id]
            if row["state"] == "not_run" and str(row["error"] or "").startswith("protocol_scope_reduction:"):
                if (row["decision_json"] is not None
                        or (row["result_json"] is not None and not _nonexecution_planning_result(row["result_json"]))
                        or row["supervisor_work_id"] is not None or ledger._has_execution_evidence(db, campaign_id, work_id)):
                    raise ValueError(f"Previously excluded work has execution evidence and requires reconciliation: {work_id}")
                restored.append(work_id)
        for work_id in withdrawn:
            db.execute("UPDATE forest_study_work SET state='not_run',error=?,updated_at=? WHERE campaign_id=? AND work_id=? AND state='queued'",
                       (reason, now, campaign_id, work_id))
        for work_id in restored:
            db.execute("UPDATE forest_study_work SET state='queued',error=NULL,updated_at=? WHERE campaign_id=? AND work_id=? AND state='not_run'",
                       (now, campaign_id, work_id))
        application = {
            "selection_revision_id": revision_id, "external_ledger_id": external_ledger_id,
            "selected_unit_count": len(selected), "excluded_unit_count": len(excluded),
            "withdrawn_work_ids": withdrawn, "restored_work_ids": restored,
            "preserved_terminal_work_ids": preserved_terminal, "already_applied": False,
            "preserved_planning_result_work_ids": preserved_planning,
        }
        db.execute("INSERT INTO forest_study_selections VALUES(?,?,?,?,?,?)",
                   (campaign_id, revision_id, external_ledger_id, encoded, _identity(application), now))
        db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                   (str(uuid.uuid4()), campaign_id, None, "study_selection_applied", _identity(application), now))
    return application


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    report = build_selection(manifest, plan)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
