"""Focused pure-CPU tests for source-bound analysis prerequisite handling.

Every receipt in this file is an explicitly nonformal temporary fixture. These
tests exercise orchestration behavior, not scientific results.
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from research.experiments.study_prerequisites import inspect_analysis_prerequisites


ARMS = ("FULL", "FLAT", "DAG_ID", "AIDE", "FULL_NO_GATE", "ID_ONLY", "RERUN_ALL")


def _episode_sources(tmp_path):
    evidence = []
    registered = []
    for configuration_id, seed, metric in (
        ("anchor", 1001, 0.21), ("anchor", 1002, 0.22),
        ("candidate-a", 1001, 0.15), ("candidate-a", 1002, 0.16),
        ("candidate-b", 1001, 0.18), ("candidate-b", 1002, 0.17),
    ):
        work_id = f"infer-{configuration_id}-{seed}"
        receipt_path = tmp_path / work_id / "inference_receipt.json"
        evidence.append({"work_id": work_id, "receipt_path": str(receipt_path),
            "configuration_id": configuration_id, "seed": seed})
        registered.append({"work_id": work_id, "kind": "infer", "action_name": "infer",
            "configuration_id": configuration_id, "seed": seed,
            "inference_spec": {"config_path": str(tmp_path / "configs" / f"{configuration_id}-{seed}.json"),
                "output_directory": str(receipt_path.parent)}})
    selection = {"analysis_operation": "select", "metric": "mae", "direction": "minimize",
        "anchor_configuration_id": "anchor", "exclude_anchor": True,
        "evidence": evidence}
    return selection, registered


def _write_nonformal_receipts(selection):
    for index, entry in enumerate(selection["evidence"]):
        entry_path = Path(entry["receipt_path"])
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        value = (0.21, 0.22, 0.15, 0.16, 0.18, 0.17)[index]
        entry_path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": entry["work_id"],
            "kind": "inference", "configuration_id": entry["configuration_id"], "seed": entry["seed"],
            "effective_config": {"configuration_id": entry["configuration_id"], "seed": entry["seed"]},
            "validation_metrics": {"mae": value}, "hidden_test_opened": False,
            "formal_score_eligible": False, "test_fixture": "nonformal"}), encoding="utf-8")


def _convert_first_source_to_retry_fit(selection, registered):
    source = selection["evidence"][0]
    binding = registered[0]
    source["work_id"] = "fit-logical-work"
    binding.update({"work_id": source["work_id"], "kind": "fit", "action_name": "train",
        "configuration_path": str(Path(binding["inference_spec"]["config_path"]))})
    binding.pop("inference_spec")
    return source


@pytest.mark.parametrize("arm", ARMS)
def test_missing_unsubmitted_inference_returns_actionable_replan_for_every_arm(tmp_path, arm):
    selection, registered = _episode_sources(tmp_path)
    selection["arm"] = arm
    # The first inference has not been submitted. The classifier must report
    # its registered ID/config/seed instead of opening a nonexistent receipt.
    missing = selection["evidence"][0]
    result = inspect_analysis_prerequisites(selection, registered_actions=registered)

    assert result["status"] == "MISSING_PREREQUISITES"
    assert result["classification"] == "source_work_incomplete"
    assert result["request_new_decision"] is True
    assert missing["work_id"] in result["missing_work_ids"]
    operation = next(row for row in result["next_executable_operations"]
        if row.get("work_id") == missing["work_id"])
    assert operation == {
        "operation": "infer", "work_id": missing["work_id"], "kind": "infer",
        "configuration_id": missing["configuration_id"], "seed": missing["seed"],
        "config_reference": registered[0]["inference_spec"]["config_path"],
        "receipt_path": missing["receipt_path"], "state": "source_not_submitted",
        "study_state": None, "supervisor_state": None,
    }
    assert result["restart_permitted"] is False


@pytest.mark.parametrize(("study_state", "supervisor_state", "classification", "next_operation"), [
    ("running", "accepted", "source_in_progress", "wait_for_registered_source_work"),
    ("failed", "failed", "source_failed", "choose_another_registered_source_or_reconcile_failure"),
    ("completed", "completed", "reconciliation_required", "reconcile_existing_execution"),
])
def test_existing_work_is_classified_without_duplicate_submission(
    tmp_path, study_state, supervisor_state, classification, next_operation
):
    selection, registered = _episode_sources(tmp_path)
    source = selection["evidence"][0]
    selection["evidence"] = [source]
    result = inspect_analysis_prerequisites(selection, registered_actions=registered,
        study_work_lookup=lambda work_id: ({"state": study_state, "supervisor_work_id": f"physical-{work_id}"}
            if work_id == source["work_id"] else None),
        supervisor_work_lookup=lambda work_id: ({"status": supervisor_state}
            if work_id == f"physical-{source['work_id']}" else None))

    classified = next(row for row in result["missing_prerequisites"]
        if row["work_id"] == source["work_id"])
    assert classified["classification"] == classification
    assert result["restart_permitted"] is False
    assert result["request_new_decision"] is True
    source_operations = [row for row in result["next_executable_operations"]
        if row.get("work_id") == source["work_id"]]
    assert len(source_operations) == 1
    assert source_operations[0]["operation"] == next_operation


def test_completed_missing_receipt_requires_reconciliation_not_reexecution(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    source_id = selection["evidence"][0]["work_id"]
    result = inspect_analysis_prerequisites(selection, registered_actions=registered,
        study_work_lookup=lambda _work_id: {"state": "completed", "supervisor_work_id": "physical-1"},
        supervisor_work_lookup=lambda _work_id: {"status": "completed"})

    assert result["classification"] == "source_reconciliation_required"
    assert result["request_new_decision"] is True
    assert result["restart_permitted"] is False
    assert source_id in result["missing_work_ids"]
    assert result["next_executable_operation"]["operation"] == "reconcile_existing_execution"


def test_six_source_receipts_unlock_existing_selector_and_comparator(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    _write_nonformal_receipts(selection)

    assert inspect_analysis_prerequisites(selection, registered_actions=registered) is None

    # Exercise the existing deterministic analysis implementation against the
    # same six complete nonformal fixtures. No source operation is rerun.
    from research.experiments.study_runtime import execute_registered_analysis

    selected = execute_registered_analysis(selection)
    assert selected["status"] == "SUCCEEDED"
    assert selected["selected_configuration_id"] == "candidate-a"
    assert selected["seed_values"] == {"1001": 0.15, "1002": 0.16}

    by_id = {row["configuration_id"] + ":" + str(row["seed"]): row["work_id"]
        for row in selection["evidence"]}
    comparison = {"analysis_operation": "compare", "metric": "mae",
        "evidence": selection["evidence"], "pairs": [
            {"left": by_id["anchor:1001"], "right": by_id["candidate-a:1001"]},
            {"left": by_id["anchor:1002"], "right": by_id["candidate-a:1002"]},
        ]}
    assert inspect_analysis_prerequisites(comparison, registered_actions=registered) is None
    compared = execute_registered_analysis(comparison)
    assert compared["status"] == "SUCCEEDED"
    assert compared["pair_count"] == 2
    assert compared["paired_differences_right_minus_left"] == pytest.approx([-0.06, -0.06])


def test_receipt_conflicting_with_completed_execution_is_not_accepted(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    _write_nonformal_receipts(selection)
    first = selection["evidence"][0]
    # A completed receipt with a wrong work identity must enter reconciliation.
    path = Path(first["receipt_path"])
    wrong = json.loads(path.read_text(encoding="utf-8"))
    wrong["work_id"] = "different-work"
    path.write_text(json.dumps(wrong), encoding="utf-8")
    result = inspect_analysis_prerequisites(selection, registered_actions=registered,
        study_work_lookup=lambda work_id: {"state": "completed", "supervisor_work_id": work_id},
        supervisor_work_lookup=lambda work_id: {"status": "completed"})

    assert result["classification"] == "source_reconciliation_required"
    assert result["request_new_decision"] is True
    assert any(row.get("detail") == "receipt_work_id_mismatch"
        for row in result["missing_prerequisites"])


def test_inference_config_reference_uses_registered_nested_spec(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    result = inspect_analysis_prerequisites(selection, registered_actions=registered)

    first = selection["evidence"][0]
    operation = next(row for row in result["next_executable_operations"]
        if row.get("work_id") == first["work_id"])
    assert operation["config_reference"] == registered[0]["inference_spec"]["config_path"]


def test_retry_receipt_accepts_only_recorded_physical_supervisor_identity(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    first = _convert_first_source_to_retry_fit(selection, registered)
    physical_id = "physical-fit-attempt-2"
    path = Path(first["receipt_path"])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": physical_id,
        "kind": "fit", "configuration_id": first["configuration_id"], "seed": first["seed"],
        "effective_config": {"configuration_id": first["configuration_id"], "seed": first["seed"]},
        "validation_metrics": {"mae": 0.21}, "hidden_test_opened": False,
        "formal_score_eligible": False, "test_fixture": "nonformal"}), encoding="utf-8")

    result = inspect_analysis_prerequisites(selection, registered_actions=registered,
        study_work_lookup=lambda work_id: ({"state": "completed", "supervisor_work_id": physical_id}
            if work_id == first["work_id"] else None),
        supervisor_work_lookup=lambda work_id: ({"status": "completed"}
            if work_id == physical_id else None))

    assert first["work_id"] in result["valid_source_work_ids"]
    assert not any(row["work_id"] == first["work_id"]
        for row in result["missing_prerequisites"])


def test_retry_receipt_does_not_accept_unmapped_physical_identity(tmp_path):
    selection, registered = _episode_sources(tmp_path)
    first = _convert_first_source_to_retry_fit(selection, registered)
    path = Path(first["receipt_path"])
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": "unmapped-physical-id",
        "kind": "fit", "configuration_id": first["configuration_id"], "seed": first["seed"],
        "validation_metrics": {"mae": 0.21}, "hidden_test_opened": False,
        "test_fixture": "nonformal"}), encoding="utf-8")

    result = inspect_analysis_prerequisites(selection, registered_actions=registered,
        study_work_lookup=lambda work_id: ({"state": "completed", "supervisor_work_id": "physical-fit-attempt-2"}
            if work_id == first["work_id"] else None),
        supervisor_work_lookup=lambda work_id: ({"status": "completed"}
            if work_id == "physical-fit-attempt-2" else None))

    assert result["classification"] == "source_reconciliation_required"
    assert any(row.get("detail") == "receipt_work_id_mismatch"
        for row in result["missing_prerequisites"])


def test_runtime_agent_feedback_inference_select_and_compare_dev_chain(tmp_path):
    """DEV integration fixture; inference receipts are nonformal, local fixtures."""
    from research.experiments.study_runtime import StudyRuntime

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    evidence, registered_infers = [], []
    for configuration_id, seed, value in (
        ("anchor", 1001, 0.21), ("anchor", 1002, 0.22),
        ("candidate-a", 1001, 0.15), ("candidate-a", 1002, 0.16),
        ("candidate-b", 1001, 0.18), ("candidate-b", 1002, 0.17),
    ):
        source_id = f"infer-{configuration_id}-{seed}"
        receipt_path = campaign / "inference" / source_id / "inference_receipt.json"
        evidence.append({"work_id": source_id, "configuration_id": configuration_id,
            "seed": seed, "receipt_path": str(receipt_path), "test_metric": value})
        registered_infers.append({"work_id": source_id, "kind": "infer", "action_name": "infer",
            "configuration_id": configuration_id, "seed": seed,
            "inference_spec": {"config_path": str(campaign / f"{configuration_id}-{seed}.json"),
                "checkpoint_path": str(campaign / f"{configuration_id}-{seed}.pt"),
                "output_directory": str(receipt_path.parent)}})
    selector = {"work_id": "select-candidate", "kind": "analysis", "action_name": "select",
        "analysis_operation": "select", "metric": "mae", "direction": "minimize",
        "anchor_configuration_id": "anchor", "exclude_anchor": True, "evidence": [
            {key: value for key, value in row.items() if key != "test_metric"} for row in evidence]}
    compare_pairs = [{"left": f"infer-anchor-{seed}", "right": f"infer-candidate-a-{seed}"}
        for seed in (1001, 1002)]
    comparator = {"work_id": "compare-candidate", "kind": "analysis", "action_name": "compare",
        "analysis_operation": "compare", "metric": "mae", "selection_work_id": "select-candidate",
        "compared_configuration_ids": ["anchor", "candidate-a"], "compared_seeds": [1001, 1002],
        "evidence": selector["evidence"], "pairs": compare_pairs}
    payload = {"arm": "FULL", "stage": "DEV_PREREQUISITE_TEST",
        "resource_policy": {"actual_gpu_seconds": 1200},
        "messages": [{"role": "user", "content": "Use current evidence and registered actions."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string"}, "actions": {"type": "array"},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["select", "infer", "compare"],
        "action_bindings": [*registered_infers, selector, comparator]}
    episode = {"work_id": "episode-prerequisite-dev", "kind": "episode", "phase": "DEV_PREREQUISITE_TEST",
        "payload": payload}
    document = {"campaign_id": "campaign-test", "manifest_id": "prerequisite-dev",
        "manifest_version": 1, "external_ledger_id": "ledger-test",
        "execution_state": {"task_queue": [episode]}, "graph": {"edges": []}}
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)

    class ScriptedAgentClient:
        provider = {"id": "fixture-provider", "model": "fixture-model"}
        config = {"reasoning_effort": "low"}

        def __init__(self):
            self.requests = []

        def complete(self, messages, **_kwargs):
            self.requests.append(messages)
            if len(self.requests) == 1:
                text = ('{"mode":"action","actions":[{"kind":"select",'
                    '"action_work_id":"select-candidate"}],"submission":{},'
                    '"rationale":"Run the registered selector."}')
            elif 2 <= len(self.requests) <= 7:
                binding = registered_infers[len(self.requests) - 2]
                text = json.dumps({"mode": "action", "actions": [{"kind": "infer",
                    "action_work_id": binding["work_id"]}], "submission": {},
                    "rationale": "The missing-prerequisite feedback names this registered inference."})
            elif len(self.requests) == 8:
                text = ('{"mode":"action","actions":[{"kind":"select",'
                    '"action_work_id":"select-candidate"}],"submission":{},'
                    '"rationale":"All six current inference receipts are now available."}')
            else:
                text = ('{"mode":"action","actions":[{"kind":"compare",'
                    '"action_work_id":"compare-candidate"}],"submission":{},'
                    '"rationale":"Compare the selector result against the paired anchor receipts."}')
            return {"text": text, "usage": {"input_tokens": 20, "output_tokens": 12, "cost": None}}

    client = ScriptedAgentClient()
    first = runtime.run_one(owner="dev-fixture", client=client)
    first_source_id = registered_infers[0]["work_id"]
    assert first["action_result"]["request_new_decision"] is True
    assert first["action_result"]["actions"][0]["result"]["status"] == "MISSING_PREREQUISITES"
    assert first_source_id in first["action_result"]["actions"][0]["result"]["missing_work_ids"]
    feedback = first["response_validation_feedback"]
    assert feedback["status"] == "MISSING_PREREQUISITES"
    assert any(row["work_id"] == first_source_id
        and row["config_reference"] == registered_infers[0]["inference_spec"]["config_path"]
        for row in feedback["next_executable_operations"])

    for index, binding in enumerate(registered_infers):
        inference_turn = runtime.run_one(owner="dev-fixture", client=client, defer_gpu=True)
        assert inference_turn["decision"]["actions"][0]["kind"] == "infer"
        assert inference_turn["decision"]["actions"][0]["action_work_id"] == binding["work_id"]
        if index == 0:
            delivered = "\n".join(str(message.get("content", ""))
                for message in client.requests[1])
            assert "MISSING_PREREQUISITES" in delivered
            assert first_source_id in delivered
            assert registered_infers[0]["inference_spec"]["config_path"] in delivered
        deferred = inference_turn["action_result"]["actions"][0]
        assert deferred["result"]["deferred_to_gpu_queue"] is True

        # These temporary receipts model already-settled DEV source work for
        # the orchestration chain; no inference process or scientific result
        # is claimed by this test.
        entry = evidence[index]
        receipt_path = Path(entry["receipt_path"])
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"status": "SUCCEEDED", "work_id": binding["work_id"],
            "kind": "inference", "configuration_id": binding["configuration_id"],
            "seed": binding["seed"], "effective_config": {"configuration_id": binding["configuration_id"],
                "seed": binding["seed"]}, "validation_metrics": {"mae": entry["test_metric"]},
            "hidden_test_opened": False, "formal_score_eligible": False,
            "test_fixture": "nonformal DEV orchestration only"}
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        runtime.ledger.set_state("campaign-test", binding["work_id"], "completed",
            result=receipt, supervisor_work_id=binding["work_id"])
        resumed = runtime.run_one(owner="dev-fixture", client=client, defer_gpu=True)
        assert resumed["resumed_saved_decision"] is True
        assert resumed["action_result"]["actions"][0]["state"] == "completed"

    selected = runtime.run_one(owner="dev-fixture", client=client, defer_gpu=True)
    assert selected["action_result"]["actions"][0]["result"]["selected_configuration_id"] == "candidate-a"
    compared = runtime.run_one(owner="dev-fixture", client=client, defer_gpu=True)
    assert compared["action_result"]["actions"][0]["result"]["pair_count"] == 2
    assert compared["action_result"]["actions"][0]["result"]["paired_differences_right_minus_left"] == pytest.approx([-0.06, -0.06])
    assert len(client.requests) == 9
    with runtime.ledger.connect() as db:
        states = [db.execute("SELECT state FROM forest_study_work WHERE work_id=?", (row["work_id"],)).fetchone()[0]
            for row in registered_infers]
        compute_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_compute_work'").fetchone()
        compute_rows = (db.execute("SELECT count(*) FROM forest_compute_work").fetchone()[0]
            if compute_table else 0)
    assert states == ["completed"] * 6
    assert compute_rows == 0


def test_existing_failed_selector_gets_missing_feedback_then_remains_cached(tmp_path):
    """A replan can request missing evidence without silently rerunning a failed selector."""
    from research.experiments.study_runtime import StudyRuntime

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    source_id = "infer-candidate-1001"
    receipt_path = campaign / "inference" / source_id / "inference_receipt.json"
    infer = {"work_id": source_id, "kind": "infer", "action_name": "infer",
        "configuration_id": "candidate", "seed": 1001,
        "inference_spec": {"config_path": str(campaign / "candidate-1001.json"),
            "checkpoint_path": str(campaign / "candidate.pt"),
            "output_directory": str(receipt_path.parent)}}
    selector = {"work_id": "failed-selector", "kind": "analysis", "action_name": "select",
        "analysis_operation": "select", "metric": "mae", "direction": "minimize",
        "evidence": [{"work_id": source_id, "configuration_id": "candidate",
            "seed": 1001, "receipt_path": str(receipt_path)}]}
    payload = {"arm": "FLAT", "stage": "DEV_FAILED_SELECTOR_TEST",
        "resource_policy": {"actual_gpu_seconds": 1200},
        "messages": [{"role": "user", "content": "Use current evidence and registered actions."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string"}, "actions": {"type": "array"},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["select", "infer"], "action_bindings": [selector, infer]}
    episode_id = "episode-failed-selector"
    document = {"campaign_id": "campaign-test", "manifest_id": "failed-selector-dev",
        "manifest_version": 1, "external_ledger_id": "ledger-test",
        "execution_state": {"task_queue": [{"work_id": episode_id, "kind": "episode",
            "phase": "DEV_FAILED_SELECTOR_TEST", "payload": payload}]}, "graph": {"edges": []}}
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    runtime.ledger.register_action("campaign-test", selector["work_id"], selector,
        parent_id=episode_id)
    original_result = {"reason": "original selector execution failed", "detail": "preserved"}
    runtime.ledger.set_state("campaign-test", selector["work_id"], "failed",
        result=original_result, error="prior selector failure")

    class ReplanningClient:
        provider = {"id": "fixture-provider", "model": "fixture-model"}
        config = {"reasoning_effort": "low"}

        def __init__(self):
            self.requests = []

        def complete(self, messages, **_kwargs):
            self.requests.append(messages)
            if len(self.requests) == 2:
                decision = {"mode": "action", "actions": [{"kind": "infer",
                    "action_work_id": source_id}], "submission": {},
                    "rationale": "The feedback identifies the missing registered inference."}
            else:
                decision = {"mode": "action", "actions": [{"kind": "select",
                    "action_work_id": selector["work_id"]}], "submission": {},
                    "rationale": "Retry selection after inspecting the current source state."}
            return {"text": json.dumps(decision),
                "usage": {"input_tokens": 12, "output_tokens": 5, "cost": None}}

    client = ReplanningClient()
    missing = runtime.run_one(owner="failed-selector-test", client=client)
    assert missing["action_result"]["request_new_decision"] is True
    assert missing["action_result"]["actions"][0]["result"]["status"] == "MISSING_PREREQUISITES"
    assert source_id in missing["action_result"]["actions"][0]["result"]["missing_work_ids"]
    with runtime.ledger.connect() as db:
        row = db.execute("SELECT state,result_json,error FROM forest_study_work WHERE work_id=?",
            (selector["work_id"],)).fetchone()
    assert tuple(row) == ("failed", json.dumps(original_result, sort_keys=True), "prior selector failure")

    deferred = runtime.run_one(owner="failed-selector-test", client=client, defer_gpu=True)
    assert deferred["decision"]["actions"][0]["action_work_id"] == source_id
    assert deferred["action_result"]["actions"][0]["result"]["deferred_to_gpu_queue"] is True
    receipt = {"status": "SUCCEEDED", "work_id": source_id, "kind": "inference",
        "configuration_id": "candidate", "seed": 1001,
        "effective_config": {"configuration_id": "candidate", "seed": 1001},
        "validation_metrics": {"mae": 0.4}, "hidden_test_opened": False,
        "formal_score_eligible": False, "test_fixture": "nonformal orchestration only"}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    runtime.ledger.set_state("campaign-test", source_id, "completed", result=receipt,
        supervisor_work_id=source_id)
    resumed = runtime.run_one(owner="failed-selector-test", client=client, defer_gpu=True)
    assert resumed["resumed_saved_decision"] is True
    assert resumed["action_result"]["actions"][0]["state"] == "completed"

    cached_failure = runtime.run_one(owner="failed-selector-test", client=client, defer_gpu=True)
    assert cached_failure["action_result"]["actions"][0]["state"] == "failed"
    assert cached_failure["action_result"]["actions"][0]["result"] == original_result
    with runtime.ledger.connect() as db:
        row_after = db.execute("SELECT state,result_json,error FROM forest_study_work WHERE work_id=?",
            (selector["work_id"],)).fetchone()
        children = db.execute("SELECT w.work_id,w.kind,w.state FROM forest_study_work AS w "
            "JOIN forest_study_edges AS e ON e.target_id=w.work_id "
            "WHERE e.source_id=? AND e.relation='derived_from' ORDER BY w.work_id",
            (episode_id,)).fetchall()
    assert tuple(row_after) == tuple(row)
    assert [child[0] for child in children].count(selector["work_id"]) == 1
    assert len(client.requests) == 3
