"""Read-only source checks for registered selection and comparison actions.

The helper prevents analysis code from opening a future receipt path before
its registered fit/inference/scoring operation has produced that receipt. It
does not submit work, alter ledger state, or decide scientific eligibility;
the existing analysis executor remains responsible for validating complete
receipts and computing selection/comparison results.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


_ACTIVE_SUPERVISOR_STATES = {"accepted", "queued", "running", "launching", "started"}
_FAILED_STATES = {"failed", "cancelled", "budget_exhausted"}
_COMPLETED_STATES = {"completed", "succeeded", "success"}
_ANALYSIS_OPERATIONS = {"select", "compare"}


def _load_receipt(path: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Receipt root must be an object")
    return value


def _state(record: Mapping[str, Any] | None, *keys: str) -> str | None:
    if not isinstance(record, Mapping):
        return None
    for key in keys:
        value = record.get(key)
        if value is not None:
            return str(value).lower()
    return None


def _source_ids(binding: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    """Return the complete evidence rows the existing executor will read."""
    entries = binding.get("evidence")
    if not isinstance(entries, list):
        return [], ["analysis_binding_missing_evidence"]
    valid_entries: list[Mapping[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            problems.append(f"evidence[{index}]_is_not_an_object")
            continue
        work_id = entry.get("work_id")
        receipt_path = entry.get("receipt_path")
        if not isinstance(work_id, str) or not work_id:
            problems.append(f"evidence[{index}]_missing_work_id")
            continue
        if work_id in seen:
            problems.append(f"duplicate_source_work_id:{work_id}")
            continue
        seen.add(work_id)
        if not isinstance(receipt_path, str) or not receipt_path:
            problems.append(f"source_receipt_reference_missing:{work_id}")
            continue
        valid_entries.append(entry)
    if binding.get("analysis_operation") == "compare":
        pairs = binding.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            problems.append("comparison_pairs_missing")
        else:
            ids = {str(entry.get("work_id")) for entry in valid_entries}
            for index, pair in enumerate(pairs):
                if not isinstance(pair, Mapping):
                    problems.append(f"pairs[{index}]_is_not_an_object")
                    continue
                for side in ("left", "right"):
                    source_id = pair.get(side)
                    if not isinstance(source_id, str) or source_id not in ids:
                        problems.append(f"pairs[{index}]_{side}_source_not_in_evidence:{source_id}")
    return valid_entries, problems


def _receipt_matches(
    entry: Mapping[str, Any], receipt: Mapping[str, Any], *, accepted_work_ids: Sequence[str] = ()
) -> tuple[bool, str | None]:
    expected_id = str(entry.get("work_id", ""))
    receipt_id = receipt.get("work_id", receipt.get("action_work_id"))
    accepted_ids = {expected_id, *(str(value) for value in accepted_work_ids if value)}
    if receipt_id is None or str(receipt_id) not in accepted_ids:
        return False, "receipt_work_id_mismatch"
    if str(receipt.get("status", "")).upper() != "SUCCEEDED":
        return False, "receipt_not_succeeded"
    if receipt.get("hidden_test_opened") is not False:
        return False, "receipt_hidden_test_status_missing_or_open"
    if entry.get("evidence_kind") == "independent_rescore":
        metrics = receipt.get("metrics")
    else:
        metrics = receipt.get("validation_metrics")
    if not isinstance(metrics, Mapping) or not metrics:
        return False, "receipt_validation_metrics_missing"
    for key in ("configuration_id", "seed"):
        expected = entry.get(key)
        observed = receipt.get(key)
        if observed is None and isinstance(receipt.get("effective_config"), Mapping):
            observed = receipt["effective_config"].get(key)
        if expected is not None and observed is not None and str(expected) != str(observed):
            return False, f"receipt_{key}_mismatch"
    if entry.get("require_formal") is True and receipt.get("formal_score_eligible") is not True:
        return False, "receipt_not_formal_score_eligible"
    return True, None


def inspect_analysis_prerequisites(
    binding: Mapping[str, Any],
    *,
    registered_actions: Sequence[Mapping[str, Any]] = (),
    study_work_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    supervisor_work_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    receipt_loader: Callable[[str], Mapping[str, Any] | None] = _load_receipt,
) -> dict[str, Any] | None:
    """Classify missing ``select``/``compare`` source receipts without effects.

    ``None`` means all source receipts are present and structurally source-bound;
    callers may then invoke their existing scientific selector/comparator.
    Otherwise a ``MISSING_PREREQUISITES`` result describes exact registered work
    IDs, configs, seeds, observed states, and the next safe operation.

    Any incomplete analysis action asks for a fresh agent decision so its
    diagnostic reaches the ordinary action loop. The suggested next operation
    distinguishes new registered work, waiting on existing work, and
    reconciliation. This helper never recommends resubmitting an existing ID.
    """
    operation = str(binding.get("analysis_operation", ""))
    if operation not in _ANALYSIS_OPERATIONS:
        return None
    entries, binding_problems = _source_ids(binding)
    registered = {str(row.get("work_id")): row for row in registered_actions
                  if isinstance(row, Mapping) and row.get("work_id") is not None}
    sources: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for entry in entries:
        work_id = str(entry["work_id"])
        receipt_path = str(entry["receipt_path"])
        registered_action = registered.get(work_id)
        study = study_work_lookup(work_id) if study_work_lookup else None
        study_state = _state(study, "state")
        supervisor_id = str((study or {}).get("supervisor_work_id") or work_id)
        supervisor = supervisor_work_lookup(supervisor_id) if supervisor_work_lookup else None
        supervisor_state = _state(supervisor, "status", "state")
        inference_spec = (registered_action or {}).get("inference_spec")
        if not isinstance(inference_spec, Mapping):
            inference_spec = entry.get("inference_spec")
        if not isinstance(inference_spec, Mapping):
            inference_spec = {}
        config_reference = ((registered_action or {}).get("configuration_path")
                or (registered_action or {}).get("config_path")
                or inference_spec.get("config_path")
                or entry.get("config_reference"))
        record = {"work_id": work_id,
            "kind": (registered_action or {}).get("kind") or entry.get("kind"),
            "action_name": (registered_action or {}).get("action_name") or entry.get("action_name"),
            "configuration_id": entry.get("configuration_id", (registered_action or {}).get("configuration_id")),
            "seed": entry.get("seed", (registered_action or {}).get("seed")),
            "config_reference": config_reference,
            "receipt_path": receipt_path,
            "study_state": study_state,
            "supervisor_work_id": supervisor_id if supervisor is not None or study else None,
            "supervisor_state": supervisor_state}
        try:
            receipt = receipt_loader(receipt_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            record.update({"classification": "receipt_conflict", "detail": f"{type(exc).__name__}: {exc}"})
            reconciliation.append(record)
            sources.append(record)
            continue
        if receipt is not None:
            # A retried logical fit can be executed under a physical
            # supervisor work ID. Accept only IDs explicitly recorded by the
            # existing logical-to-physical mapping; never trust an arbitrary
            # receipt's self-reported alias.
            mapped_work_ids = []
            if study is not None:
                mapped_work_ids.append(study.get("supervisor_work_id"))
                mapped_work_ids.extend(study.get("supervisor_work_ids", ())
                    if isinstance(study.get("supervisor_work_ids"), (list, tuple)) else ())
            matches, reason = _receipt_matches(entry, receipt, accepted_work_ids=mapped_work_ids)
            if study_state in _FAILED_STATES or supervisor_state in _FAILED_STATES:
                record.update({"classification": "receipt_execution_conflict",
                               "detail": "A success receipt conflicts with a failed/cancelled execution record."})
                reconciliation.append(record)
            elif not matches:
                record.update({"classification": "receipt_conflict", "detail": reason})
                reconciliation.append(record)
            elif study_state in _COMPLETED_STATES or supervisor_state in _COMPLETED_STATES or (
                    study is None and supervisor is None):
                record.update({"classification": "receipt_valid", "receipt_status": receipt.get("status")})
            else:
                record.update({"classification": "receipt_execution_conflict",
                               "detail": "Receipt exists but registered execution has not settled successfully."})
                reconciliation.append(record)
            sources.append(record)
            continue

        if study_state in _FAILED_STATES or supervisor_state in _FAILED_STATES:
            record.update({"classification": "source_failed",
                           "detail": (study or {}).get("error") or (supervisor or {}).get("error")})
            failed.append(record)
        elif study_state in _COMPLETED_STATES or supervisor_state in _COMPLETED_STATES or supervisor_state == "reconciliation":
            record.update({"classification": "reconciliation_required",
                           "detail": "Execution is accepted or completed but its expected receipt is absent."})
            reconciliation.append(record)
        elif supervisor_state in _ACTIVE_SUPERVISOR_STATES:
            record.update({"classification": "source_in_progress"})
            missing.append(record)
        elif study_state in {"claimed", "running", "awaiting_reconciliation"}:
            record.update({"classification": "source_in_progress"})
            missing.append(record)
        elif study_state in {"queued", "retryable"}:
            record.update({"classification": "source_queued"})
            missing.append(record)
        elif registered_action is not None and study is None and supervisor is None:
            record.update({"classification": "source_not_submitted"})
            missing.append(record)
        else:
            record.update({"classification": "source_unregistered",
                           "detail": "No registered action, ledger work, supervisor record, or receipt identifies this source."})
            reconciliation.append(record)
        sources.append(record)

    valid_ids = {row["work_id"] for row in sources if row["classification"] == "receipt_valid"}
    issue_ids = {row["work_id"] for row in (*missing, *reconciliation, *failed)}
    for problem in binding_problems:
        issue_ids.add(problem)
    if not issue_ids:
        return None

    if binding_problems:
        next_operations = [{"operation": "repair_registered_analysis_binding", "detail": problem}
                           for problem in binding_problems]
        classification = "invalid_analysis_binding"
    elif reconciliation:
        next_operations = [{"operation": "reconcile_existing_execution", "work_id": row["work_id"],
            "supervisor_work_id": row.get("supervisor_work_id"),
            "receipt_path": row.get("receipt_path"), "configuration_id": row.get("configuration_id"),
            "seed": row.get("seed"), "state": row.get("classification")} for row in reconciliation]
        classification = "source_reconciliation_required"
    elif failed:
        next_operations = [{"operation": "choose_another_registered_source_or_reconcile_failure",
            "work_id": row["work_id"], "configuration_id": row.get("configuration_id"),
            "seed": row.get("seed"), "state": row.get("study_state") or row.get("supervisor_state"),
            "detail": row.get("detail")} for row in failed]
        classification = "source_failed"
    else:
        next_operations = []
        for row in missing:
            if row["classification"] == "source_not_submitted":
                operation_name = row.get("action_name") or row.get("kind")
            else:
                operation_name = "wait_for_registered_source_work"
            next_operations.append({"operation": operation_name, "work_id": row["work_id"],
                "kind": row.get("kind"), "configuration_id": row.get("configuration_id"),
                "seed": row.get("seed"), "config_reference": row.get("config_reference"),
                "receipt_path": row.get("receipt_path"), "state": row.get("classification"),
                "study_state": row.get("study_state"), "supervisor_state": row.get("supervisor_state")})
        classification = "source_work_incomplete"

    request_new_decision = True
    return {"status": "MISSING_PREREQUISITES", "valid": False,
        "operation": operation, "classification": classification,
        "reason": "Analysis source receipts are missing, unsettled, failed, or inconsistent.",
        "missing_work_ids": sorted(row["work_id"] for row in (*missing, *failed, *reconciliation)),
        "binding_problems": list(binding_problems),
        "valid_source_work_ids": sorted(valid_ids),
        "missing_prerequisites": [*missing, *failed, *reconciliation],
        "next_executable_operations": next_operations,
        "next_executable_operation": next_operations[0] if next_operations else None,
        "request_new_decision": request_new_decision,
        # This helper never authorizes replaying an existing work ID. A new
        # registered operation is a model decision handled by the caller.
        "restart_permitted": False,
        "recheckable": True}

