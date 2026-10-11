"""Nonformal long-task infrastructure fixtures; no model/GPU work is run.

Synthetic source definitions and explicitly marked fixture receipts exercise
the actual builders, original SQLite ledger and scientific analysis code.
They are not campaign evidence and must never qualify a formal study cell.
"""
import json
from pathlib import Path

import pytest

from research.experiments.end_to_end_runtime import (
    _load, _side_constraint_passes, build_end_to_end_episode_request, execute_end_to_end_analysis,
    is_end_to_end, verify_end_to_end_submission,
)
from research.experiments import end_to_end_runtime
from research.experiments.study_runtime import StudyRuntime


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_absolute_drop_constraint_is_applied_in_active_objective_direction(tmp_path, monkeypatch):
    assert _side_constraint_passes({"allowed_direction": "no_more_than_absolute_drop",
        "comparison": "relative_to_anchor", "margin": 0.01}, 0.89, 0.90, "maximize")
    assert not _side_constraint_passes({"allowed_direction": "no_more_than_absolute_drop",
        "comparison": "relative_to_anchor", "margin": 0.01}, 0.889, 0.90, "maximize")
    assert _side_constraint_passes({"allowed_direction": "no_more_than_absolute_drop",
        "comparison": "relative_to_anchor", "margin": 0.02}, 0.92, 0.90, "minimize")

    rows = [
        {"work_id": "anchor", "configuration_id": "anchor", "fit_gpu_seconds": 10,
         "metrics": {"accuracy": 0.90, "macro_f1": 0.90}},
        {"work_id": "candidate-a", "configuration_id": "candidate-a", "fit_gpu_seconds": 10,
         "metrics": {"accuracy": 0.91, "macro_f1": 0.89}},
        {"work_id": "candidate-b", "configuration_id": "candidate-b", "fit_gpu_seconds": 10,
         "metrics": {"accuracy": 0.96, "macro_f1": 0.889}},
    ]
    monkeypatch.setattr(end_to_end_runtime, "_verified_analysis_rows", lambda _binding: rows)
    result = execute_end_to_end_analysis({"analysis_operation": "end_to_end_select", "metric": "accuracy",
        "direction": "maximize", "anchor_configuration_id": "anchor",
        "side_constraints": [{"metric": "macro_f1", "allowed_direction": "no_more_than_absolute_drop",
                              "comparison": "relative_to_anchor", "margin": 0.01}],
        "output_path": str(tmp_path / "selection.json")})
    assert result["selected_configuration_id"] == "candidate-a"
    assert result["constraint_satisfied"] is True


def fixture_runtime(root):
    source = root / "nonformal_source_spec"
    task = {"task_id": "EX97", "family_id": "darcy", "scientific_question": "Nonformal builder fixture",
        "primary_metric": "relative_l2", "primary_direction": "minimize", "side_constraints": [],
        "effect_threshold": {"kind": "relative_reduction", "value": 0.05},
        "required_answer": "Report the nonformal fixture result", "scope": "nonformal fixture only",
        "C1_revision": {"revised_primary_metric": "relative_gradient_l2", "revised_direction": "minimize", "revised_side_constraints": []},
        "long_from_scratch": {"enabled": True, "cartesian_axes": {"fourier_modes_per_axis": [4, 8, 16], "hidden_width": [16, 24, 32]},
            "anchor_overrides": {"fourier_modes_per_axis": 8, "hidden_width": 24}, "configuration_count": 9,
            "max_distinct_exploration_configurations_including_anchor": 4, "exploration_seed": 17,
            "confirmation_seeds": [1001, 1002, 1003], "side_constraints": []}}
    _write(source / "tasks.json", {"tasks": [task]})
    _write(source / "models.json", {"models": [{"model_id": "nonformal-fno", "family_id": "darcy",
        "architecture": {"fourier_modes_per_axis": 8, "hidden_width": 24, "spectral_layers": 3},
        "default_recipe": {"optimizer_updates": 200, "effective_batch_size": 16, "training_sample_count": 2, "gradient_loss_weight": 0.0},
        "allowed_pre_freeze_update_counts": [200]}]})
    _write(root / "nonformal-updates.json", {"source_validation": {"valid": True}, "formal_score_eligible": False,
                                            "family_update_counts": {"darcy": 200}, "nonformal_fixture": True})
    _write(root / "nonformal-data-ids.json", {"darcy": {"train_sample_ids": [1, 2], "validation_sample_ids": [3]}})
    _write(root / "nonformal-versions.json", {"darcy": "nonformal-fixture-data-v1"})
    payload = {"work_id": "nonformal-full-episode", "kind": "episode", "category": "LIVE_END_TO_END",
        "task_id": "EX97", "arm": "FULL", "repeat": 0, "source_spec_dir": str(source),
        "resolved_updates_path": str(root / "nonformal-updates.json"), "data_ids_reference": str(root / "nonformal-data-ids.json"),
        "dataset_versions_reference": str(root / "nonformal-versions.json"),
        "resource_policy": {"actual_gpu_seconds": 1200, "active_wall_seconds": 3600}}
    document = {"campaign_id": "nonformal-long-fixture", "manifest_id": "nonformal-manifest", "manifest_version": 1,
        "external_ledger_id": "nonformal-ledger", "tasks": [task], "execution_state": {"task_queue": [payload]}}
    _write(root / "study_execution_manifest.json", document)
    runtime = StudyRuntime(campaign=root, ledger=root / "study.sqlite", ledger_id="nonformal-ledger",
                           manifest=root / "study_execution_manifest.json")
    return runtime, payload


def _complete_fixture_fit(runtime, binding, original=2.0, revised=1.0):
    """Explicit synthetic receipt fixture; never launches training."""
    runtime.ledger.register_action(runtime.campaign_id, binding["work_id"], binding, parent_id="nonformal-full-episode")
    config = binding["effective_config"]
    output = Path(binding["output_directory"])
    metrics = {"relative_l2": float(original), "relative_gradient_l2": float(revised)}
    _write(output / "effective_config.json", config)
    _write(output / "execution_receipt.json", {"status": "SUCCEEDED", "work_id": binding["work_id"],
        "effective_config": config, "hidden_test_opened": False, "formal_score_eligible": True,
        "validation_metrics": metrics, "validation_prediction_file": "nonformal_fixture_predictions.npz",
        "nonformal_fixture": True})
    runtime.ledger.set_state(runtime.campaign_id, binding["work_id"], "completed", result={"nonformal_fixture": True})


def _complete_fixture_score(runtime, binding):
    """Fixture already independently calculated values, scoped to this test."""
    receipt = json.loads(Path(binding["reference_receipt_path"]).read_text(encoding="utf-8"))
    result = {"status": "SUCCEEDED", "metrics": receipt["validation_metrics"], "nonformal_fixture": True}
    _write(binding["output_path"], result)
    runtime.ledger.register_action(runtime.campaign_id, binding["work_id"], binding, parent_id="nonformal-full-episode")
    runtime.ledger.set_state(runtime.campaign_id, binding["work_id"], "completed", result=result)


def _frontier(request, action):
    current = set(request["current_action_work_ids"])
    return [value for value in request["action_bindings"] if value["work_id"] in current and value["action_name"] == action]


def test_normalizes_nested_end_to_end_category():
    assert is_end_to_end({"episode": {"category": "LIVE_END_TO_END"}})
    assert not is_end_to_end({"category": "LIVE_MAIN"})


def test_independent_full_aide_start_same_contract_with_distinct_fit_ids(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    full = build_end_to_end_episode_request(runtime, payload)
    aide = build_end_to_end_episode_request(runtime, {**payload, "work_id": "nonformal-aide-episode", "arm": "AIDE"})
    full_state, aide_state = _load(runtime, payload["work_id"]), _load(runtime, "nonformal-aide-episode")
    assert len(full_state["fits"]) == 36 and len(aide_state["fits"]) == 36
    assert full_state["exploration_order"] == aide_state["exploration_order"]
    assert len(full_state["exploration_order"]) == 4 and full_state["confirmation_seeds"] == [1001, 1002, 1003]
    assert not ({value["fit_id"] for value in full_state["fits"]} & {value["fit_id"] for value in aide_state["fits"]})
    assert len(_frontier(full, "train")) == len(_frontier(aide, "train")) == 1
    assert _frontier(full, "train")[0]["seed"] == 17
    assert full["resource_policy"] == aide["resource_policy"] == {"actual_gpu_seconds": 1200, "active_wall_seconds": None}
    assert build_end_to_end_episode_request(runtime, payload)["current_action_work_ids"] == full["current_action_work_ids"]


def test_second_actual_fit_injects_c1_and_new_independent_score_identities(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    first = build_end_to_end_episode_request(runtime, payload)
    _complete_fixture_fit(runtime, _frontier(first, "train")[0])
    after_first = build_end_to_end_episode_request(runtime, payload)
    assert _load(runtime, payload["work_id"])["objective"]["version"] == 1
    old_score_id = _frontier(after_first, "score")[0]["work_id"]
    _complete_fixture_score(runtime, _frontier(after_first, "score")[0])
    _complete_fixture_fit(runtime, _frontier(after_first, "train")[0], original=3.0, revised=0.5)
    second = build_end_to_end_episode_request(runtime, payload)
    state = _load(runtime, payload["work_id"])
    assert state["objective"]["version"] == 2 and len(state["objective_history"]) == 1
    assert len(state["objective_history"][0]["actual_completed_exploration"]) == 2
    assert old_score_id not in {value["work_id"] for value in _frontier(second, "score")}
    assert not _frontier(second, "select")
    for binding in _frontier(second, "score"):
        _complete_fixture_score(runtime, binding)
    ready = build_end_to_end_episode_request(runtime, payload)
    selection = _frontier(ready, "select")[0]
    assert selection["metric"] == "relative_gradient_l2"
    result = execute_end_to_end_analysis(selection)
    assert result["selected_configuration_id"] != state["anchor_configuration_id"]


def test_historical_decision_transcript_is_retrievable_but_not_repeated_in_model_prompt(tmp_path):
    runtime, full_payload = fixture_runtime(tmp_path)
    original_history = {"actual_action_history": [{"original_response": "decision evidence " * 80_000}],
        "model_response_records": [{"request_id": "prior-request", "status": "completed"}]}
    for payload in (full_payload, {**full_payload, "work_id": "nonformal-aide-history", "arm": "AIDE"}):
        build_end_to_end_episode_request(runtime, payload)
        state = _load(runtime, payload["work_id"])
        state["objective"] = {**state["objective"], "version": 2}
        state["objective_history"] = [{"objective": {"version": 1},
            "actual_completed_exploration": [], "prior_comparison_records": [],
            "actual_decision_history": original_history}]
        end_to_end_runtime._save(runtime, payload["work_id"], state)

        request = runtime.build_episode_request(payload)
        public = json.loads(request["messages"][-1]["content"])
        reference = public["objective_history"][0]["actual_decision_history_reference"]
        assert "actual_decision_history" not in public["objective_history"][0]
        assert len(json.dumps(request["messages"], ensure_ascii=False)) < 1_048_576
        catalog_ids = {row.get("segment_id", row.get("id"))
                       for row in request["context_memory"]["catalog"]["segments"]}
        assert reference["segment_id"] in catalog_ids

        memory = runtime._public_memory(payload)
        chunks, offset = [], 0
        while True:
            page = memory.read_json(reference["segment_id"], pointer=reference["pointer"],
                                    offset=offset, limit=32_000)
            chunks.append(page["content"])
            if page["complete"]:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        assert json.loads("".join(chunks)) == original_history
        assert _load(runtime, payload["work_id"])["objective_history"][0]["actual_decision_history"] == original_history


def test_versioned_pair_precedes_all_registered_confirmation_and_final_requires_comparison(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    request = build_end_to_end_episode_request(runtime, payload)
    _complete_fixture_fit(runtime, _frontier(request, "train")[0])
    request = build_end_to_end_episode_request(runtime, payload)
    _complete_fixture_fit(runtime, _frontier(request, "train")[0], revised=0.5)
    request = build_end_to_end_episode_request(runtime, payload)
    for binding in _frontier(request, "score"):
        _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    selection = _frontier(request, "select")[0]
    runtime.ledger.register_action(runtime.campaign_id, selection["work_id"], selection, parent_id=payload["work_id"])
    result = execute_end_to_end_analysis(selection)
    runtime.ledger.set_state(runtime.campaign_id, selection["work_id"], "completed", result=result)
    request = build_end_to_end_episode_request(runtime, payload)
    state = _load(runtime, payload["work_id"])
    assert state["stage"] == "confirmation" and len(state["comparison_records"]) == 1
    selected = result["selected_configuration_id"]
    premature = verify_end_to_end_submission(runtime, payload, [], {"selected_configuration_id": selected, "claim": "nonformal fixture"})
    assert premature["valid"] is False and len(premature["missing"]) == 6
    for seed in [1001, 1002, 1003]:
        request = build_end_to_end_episode_request(runtime, payload)
        train = [value for value in _frontier(request, "train") if value["seed"] != 17]
        assert len(train) == 2 and {value["seed"] for value in train} == {seed}
        for binding in train:
            _complete_fixture_fit(runtime, binding, revised=0.5 if binding["configuration_id"] == selected else 1.0)
        request = build_end_to_end_episode_request(runtime, payload)
        for binding in _frontier(request, "score"):
            _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    compare = _frontier(request, "compare")[0]
    compared = execute_end_to_end_analysis(compare)
    assert compared["pair_count"] == 3
    runtime.ledger.register_action(runtime.campaign_id, compare["work_id"], compare, parent_id=payload["work_id"])
    runtime.ledger.set_state(runtime.campaign_id, compare["work_id"], "completed", result=compared)
    answer = verify_end_to_end_submission(runtime, payload, [], {"selected_configuration_id": selected, "claim": "Nonformal fixture only"})
    assert answer["valid"] is True and answer["confirmation_seeds"] == [1001, 1002, 1003]
    assert answer["hidden_test_opened"] is False


def test_completed_foreign_or_edited_receipt_cannot_advance_from_scratch(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    request = build_end_to_end_episode_request(runtime, payload)
    binding = _frontier(request, "train")[0]
    _complete_fixture_fit(runtime, binding)
    path = Path(binding["output_directory"]) / "execution_receipt.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["effective_config"]["seed"] = 1001
    _write(path, receipt)
    with pytest.raises(ValueError, match="exact independent episode fit"):
        build_end_to_end_episode_request(runtime, payload)


def test_unresolved_calibration_never_invents_update_count(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    _write(payload["resolved_updates_path"], {"source_validation": {"valid": False}, "formal_score_eligible": False,
                                           "family_update_counts": {"darcy": None}})
    with pytest.raises(ValueError, match="validated calibration"):
        build_end_to_end_episode_request(runtime, payload)


def test_episode_cannot_override_registered_seeds(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    with pytest.raises(ValueError, match="registered long-task seeds"):
        build_end_to_end_episode_request(runtime, {**payload, "confirmation_seeds": [1001, 1002]})


def test_comparison_remains_editable_and_retains_original_versions(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    request = build_end_to_end_episode_request(runtime, payload)
    for revised in [1.0, 0.7]:
        _complete_fixture_fit(runtime, [value for value in _frontier(request, "train") if value["seed"] == 17][0], revised=revised)
        request = build_end_to_end_episode_request(runtime, payload)
    for binding in _frontier(request, "score"):
        _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    selection = _frontier(request, "select")[0]
    runtime.ledger.register_action(runtime.campaign_id, selection["work_id"], selection, parent_id=payload["work_id"])
    runtime.ledger.set_state(runtime.campaign_id, selection["work_id"], "completed", result=execute_end_to_end_analysis(selection))
    request = build_end_to_end_episode_request(runtime, payload)
    original = _load(runtime, payload["work_id"])["comparison_records"][0]
    exploration = [value for value in _frontier(request, "train") if value["seed"] == 17]
    assert len(exploration) == 1 and not _frontier(request, "select")
    _complete_fixture_fit(runtime, exploration[0], revised=0.1)
    request = build_end_to_end_episode_request(runtime, payload)
    for binding in _frontier(request, "score"):
        _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    selection = _frontier(request, "select")[0]
    runtime.ledger.register_action(runtime.campaign_id, selection["work_id"], selection, parent_id=payload["work_id"])
    runtime.ledger.set_state(runtime.campaign_id, selection["work_id"], "completed", result=execute_end_to_end_analysis(selection))
    build_end_to_end_episode_request(runtime, payload)
    records = _load(runtime, payload["work_id"])["comparison_records"]
    assert records[0] == original and records[1]["version"] == 2
    assert records[1]["selected_configuration_id"] != original["selected_configuration_id"]
    assert records[1]["full_pair_configurations"] and records[1]["dataset_version_ids"]


def test_inspect_identity_updates_when_actual_receipt_settles(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    request = build_end_to_end_episode_request(runtime, payload)
    binding = _frontier(request, "train")[0]
    runtime.ledger.register_action(runtime.campaign_id, binding["work_id"], binding, parent_id=payload["work_id"])
    running = build_end_to_end_episode_request(runtime, payload)
    _complete_fixture_fit(runtime, binding)
    completed = build_end_to_end_episode_request(runtime, payload)
    assert _frontier(running, "inspect")[0]["work_id"] != _frontier(completed, "inspect")[0]["work_id"]


def _record_fixture_analysis(runtime, payload, binding):
    runtime.ledger.register_action(runtime.campaign_id, binding["work_id"], binding, parent_id=payload["work_id"])
    result = execute_end_to_end_analysis(binding)
    runtime.ledger.set_state(runtime.campaign_id, binding["work_id"], "completed", result=result)
    return result


def _finish_fixture_confirmation(runtime, payload):
    for seed in [1001, 1002, 1003]:
        request = build_end_to_end_episode_request(runtime, payload)
        for binding in [value for value in _frontier(request, "train") if value["seed"] == seed]:
            _complete_fixture_fit(runtime, binding)
        request = build_end_to_end_episode_request(runtime, payload)
        for binding in _frontier(request, "score"):
            _complete_fixture_score(runtime, binding)
    return build_end_to_end_episode_request(runtime, payload)


def test_changed_selection_requires_current_version_comparison_after_all_actual_pairs(tmp_path):
    runtime, payload = fixture_runtime(tmp_path)
    request = build_end_to_end_episode_request(runtime, payload)
    for revised in [1.0, 0.7]:
        _complete_fixture_fit(runtime, [value for value in _frontier(request, "train") if value["seed"] == 17][0], revised=revised)
        request = build_end_to_end_episode_request(runtime, payload)
    for binding in _frontier(request, "score"):
        _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    original = _record_fixture_analysis(runtime, payload, _frontier(request, "select")[0])
    request = _finish_fixture_confirmation(runtime, payload)
    _record_fixture_analysis(runtime, payload, _frontier(request, "compare")[0])
    assert verify_end_to_end_submission(runtime, payload, [], {"selected_configuration_id": original["selected_configuration_id"], "claim": "nonformal fixture"})["valid"]
    request = build_end_to_end_episode_request(runtime, payload)
    _complete_fixture_fit(runtime, [value for value in _frontier(request, "train") if value["seed"] == 17][0], revised=0.1)
    request = build_end_to_end_episode_request(runtime, payload)
    for binding in _frontier(request, "score"):
        _complete_fixture_score(runtime, binding)
    request = build_end_to_end_episode_request(runtime, payload)
    revised = _record_fixture_analysis(runtime, payload, _frontier(request, "select")[0])
    assert revised["selected_configuration_id"] != original["selected_configuration_id"]
    request = _finish_fixture_confirmation(runtime, payload)
    answer = verify_end_to_end_submission(runtime, payload, [], {"selected_configuration_id": revised["selected_configuration_id"], "claim": "nonformal fixture"})
    assert answer["valid"] is False and answer["reason"] == "missing_actual_paired_comparison_or_measured_claim"
    _record_fixture_analysis(runtime, payload, _frontier(request, "compare")[0])
    assert verify_end_to_end_submission(runtime, payload, [], {"selected_configuration_id": revised["selected_configuration_id"], "claim": "nonformal fixture"})["valid"]
