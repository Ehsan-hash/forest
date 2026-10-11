"""Actionable binding diagnostics; nonformal CPU fixtures, no model/GPU calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3

import pytest

from research.experiments.study_runtime import (
    StudyRuntime, _validate_action_bindings, _validate_episode_schema, _restore_strict_optional_nulls,
)


def binding():
    return {"work_id": "nonformal--compare--task--candidate", "kind": "analysis",
        "action_name": "compare", "configuration_id": "task", "seed": None}


@pytest.mark.parametrize("field,supplied,expected", [
    ("configuration_id", "candidate", "task"), ("seed", 11, None), ("kind", "select", "compare"),
])
def test_known_work_id_reports_exact_conflicting_selectors_without_repair(field, supplied, expected):
    registered = binding()
    action = {"kind": "compare", "action_work_id": registered["work_id"], "configuration_id": "task", "seed": None}
    action[field] = supplied
    snapshot = deepcopy(action)
    with pytest.raises(ValueError) as error:
        _validate_action_bindings({"action_bindings": [registered]}, [action])
    reason = str(error.value)
    assert registered["work_id"] in reason
    assert json.dumps(field) in reason
    assert json.dumps(supplied) in reason and json.dumps(expected) in reason
    assert '"supplied"' in reason and '"expected"' in reason
    assert "null/omitted" in reason
    assert action == snapshot and registered == binding()


def test_unknown_or_ambiguous_work_id_has_no_invented_expected_binding():
    for bindings, action_id in (([binding()], "unknown"), ([binding(), binding()], binding()["work_id"])):
        with pytest.raises(ValueError, match="must identify exactly one registered binding"):
            _validate_action_bindings({"action_bindings": bindings}, [
                {"kind": "compare", "action_work_id": action_id, "configuration_id": "candidate"}])


@pytest.mark.parametrize("selectors", [{}, {"configuration_id": None, "seed": None}, {"configuration_id": "task", "seed": None}])
def test_exact_or_null_optional_selectors_keep_existing_matching_behavior(selectors):
    _validate_action_bindings({"action_bindings": [binding()]}, [
        {"kind": "compare", "action_work_id": binding()["work_id"], **selectors}])


def _comparison_payload(tmp_path):
    """Explicit original numeric fixtures for real deterministic CPU arithmetic."""
    evidence, pairs, snapshots = [], [], {}
    for seed in (11, 12):
        pair = {}
        for configuration_id, value, side in (("anchor", 1.0, "left"), ("candidate", .8, "right")):
            work_id = f"nonformal-{configuration_id}-{seed}"
            path = tmp_path / (work_id + ".json")
            path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": work_id,
                "configuration_id": configuration_id, "seed": seed,
                "validation_metrics": {"fixture_mae": value}, "hidden_test_opened": False,
                "formal_score_eligible": False, "test_fixture": "explicit nonformal input; no fit occurred"}), encoding="utf-8")
            snapshots[path] = path.read_bytes()
            evidence.append({"work_id": work_id, "configuration_id": configuration_id,
                "seed": seed, "receipt_path": str(path)})
            pair[side] = work_id
        pairs.append(pair)
    compare = {**binding(), "analysis_operation": "compare", "metric": "fixture_mae",
        "evidence": evidence, "pairs": pairs, "compared_configuration_ids": ["anchor", "candidate"],
        "compared_seeds": [11, 12], "output_path": str(tmp_path / "nonformal-comparison.json")}
    schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
        "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
            "actions": {"type": "array", "items": {"type": "object", "required": ["kind", "action_work_id"],
                "properties": {"kind": {"type": "string", "enum": ["compare"]},
                    "action_work_id": {"type": "string", "enum": [compare["work_id"]]},
                    "configuration_id": {"type": ["string", "null"]}, "seed": {"type": ["integer", "null"]}}}},
            "submission": {"type": "object"}, "rationale": {"type": "string"}}}
    payload = {"arm": "FLAT", "allowed_decisions": ["compare"], "action_bindings": [compare],
        "resource_policy": {"active_wall_seconds": None, "actual_gpu_seconds": 480}, "decision_schema": schema,
        "messages": [{"role": "user", "content": "Nonformal registered source fixture:\n" + json.dumps({
            "action_bindings": [{key: compare[key] for key in ("action_name", "configuration_id", "seed", "kind", "work_id")} ]})}]}
    return payload, snapshots


def _decision(configuration_id):
    return {"mode": "action", "actions": [{"kind": "compare", "action_work_id": binding()["work_id"],
        "configuration_id": configuration_id, "seed": None}], "submission": {},
        "rationale": "Nonformal validator fixture; no scientific result claimed."}


def test_whole_batch_is_rejected_before_any_valid_prefix_operation(tmp_path, monkeypatch):
    from test_study_runtime import manifest
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite", ledger_id="ledger-test", manifest=path)
    payload, _ = _comparison_payload(tmp_path)
    valid = {"kind": "analysis", "action_name": "inspect", "work_id": "valid-prefix", "configuration_id": "task"}
    payload["action_bindings"].insert(0, valid)
    calls = []
    monkeypatch.setattr(runtime, "execute_decision_action", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="selectors conflict"):
        runtime.execute_decision_actions({"payload": payload}, [
            {"kind": "inspect", "action_work_id": "valid-prefix"}, _decision("candidate")["actions"][0]])
    assert calls == []
    assert not (tmp_path / "nonformal-comparison.json").exists()
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_work").fetchone()[0] == 0


def test_ordinary_feedback_survives_reload_and_fresh_corrected_decision_computes_cpu_comparison(tmp_path):
    from test_study_runtime import manifest
    from test_aide_runtime import NonformalClient
    payload, snapshots = _comparison_payload(tmp_path)
    original_payload = deepcopy(payload)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest([{"work_id": "episode", "kind": "episode", "payload": payload}])), encoding="utf-8")
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite", ledger_id="ledger-test", manifest=path)
    wrong, corrected = _decision("candidate"), _decision("task")
    _validate_episode_schema(wrong, payload["decision_schema"])
    client = NonformalClient([json.dumps(wrong), json.dumps(corrected)])
    first = runtime.run_one(owner="nonformal-first", client=client)
    assert first["action_result"]["state"] == "retryable"
    assert '"configuration_id"' in first["response_validation_feedback"]["reason"]
    assert '"expected": "task"' in first["response_validation_feedback"]["reason"]
    assert not (tmp_path / "nonformal-comparison.json").exists()
    with runtime.ledger.connect() as db:
        old_request, old_response = db.execute("SELECT request_json,response_json FROM forest_study_model_calls").fetchone()
        assert db.execute("SELECT 1 FROM forest_study_work WHERE work_id=?", (binding()["work_id"],)).fetchone() is None
    restored = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite", ledger_id="ledger-test", manifest=path)
    second = restored.run_one(owner="nonformal-second", client=client)
    assert len(client.calls) == 2 and second.get("resumed_saved_decision", False) is False
    assert second["decision"] == corrected
    delivered = "\n".join(message["content"] for message in client.calls[1][0])
    assert json.dumps(first["response_validation_feedback"]["reason"])[1:-1] in delivered
    assert "null/omitted" in delivered
    result = second["action_result"]["actions"][0]
    assert result["state"] == "completed"
    assert result["result"]["paired_differences_right_minus_left"] == pytest.approx([-.2, -.2])
    assert (tmp_path / "nonformal-comparison.json").exists()
    assert {file: file.read_bytes() for file in snapshots} == snapshots
    assert payload == original_payload
    with restored.ledger.connect() as db:
        rows = db.execute("SELECT request_json,response_json,status FROM forest_study_model_calls ORDER BY recorded_at").fetchall()
        assert len(rows) == 2 and rows[0][0] == old_request and rows[0][1] == old_response
        assert [row[2] for row in rows] == ["completed", "completed"]
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind IN ('fit','infer')").fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["configuration_id", "action_work_id"])
def test_native_binding_rejection_keeps_reloadable_journal_and_corrected_cpu_action(tmp_path, fault):
    from test_aide_runtime import NonformalClient, _runtime, _review
    from research.experiments.aide_runtime import load_upstream, run_aide_episode
    upstream_path = Path(os.environ.get("FOREST_AIDE_UPSTREAM", "/home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/executors/aide_upstream"))
    if not upstream_path.is_dir():
        pytest.skip("Actual official AIDE clone unavailable")
    runtime, item = _runtime(tmp_path, upstream_path)
    payload, snapshots = _comparison_payload(tmp_path)
    item["payload"].update(payload, arm="AIDE")
    wrong = _decision("candidate")
    if fault == "action_work_id":
        wrong["actions"][0].update(action_work_id="unknown-native-operation", configuration_id="task")
    def program(decision):
        return "Nonformal CPU action fixture.\n```python\nimport json\ndecision = " + repr(decision) + "\nprint(json.dumps(decision))\n```"
    client = NonformalClient([program(wrong), _review(buggy=True), program(_decision("task")), _review(metric=None, buggy=True)])
    first = run_aide_episode(runtime, item, client=client)
    execution = first["action_result"]
    assert execution["exc_type"] == "DecisionValidationError"
    assert isinstance(execution["exc_info"], dict)
    assert execution["exc_info"]["args"] == [execution["validation_reason"]]
    if fault == "configuration_id":
        assert '"expected": "task"' in execution["validation_reason"]
    else:
        assert "enum mismatch" in execution["validation_reason"]
    assert json.loads(execution["stdout"].splitlines()[-1])["decision_validation_error"] == execution["validation_reason"]
    assert execution["pending_work_ids"] == [] and execution["action_results"] == []
    assert not (tmp_path / "nonformal-comparison.json").exists()
    with runtime.ledger.connect() as db:
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
        original_calls = db.execute("SELECT request_json,response_json FROM forest_study_model_calls ORDER BY recorded_at").fetchall()
    upstream = load_upstream(upstream_path)
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    assert journal.nodes[0].exc_info == {"args": [execution["validation_reason"]]}
    assert journal.nodes[0].is_buggy is True
    # The actual upstream deserializer rejects the pre-fix plain-string field.
    journal.nodes[0].exc_info = execution["validation_reason"]
    with pytest.raises(AttributeError, match="keys"):
        upstream["serialize"].loads_json(upstream["serialize"].dumps_json(journal), upstream["journal"].Journal)
    restored, restored_item = _runtime(tmp_path, upstream_path)
    restored_item["payload"] = deepcopy(item["payload"])
    second = run_aide_episode(restored, restored_item, client=client)
    assert second["native_completed_steps"] == 2 and len(client.calls) == 4
    result = second["action_result"]["action_results"][0]
    assert result["state"] == "completed"
    assert result["result"]["paired_differences_right_minus_left"] == pytest.approx([-.2, -.2])
    assert {file: file.read_bytes() for file in snapshots} == snapshots
    with restored.ledger.connect() as db:
        current_calls = db.execute("SELECT request_json,response_json FROM forest_study_model_calls ORDER BY recorded_at").fetchall()
        assert current_calls[:2] == original_calls
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind IN ('fit','infer')").fetchone()[0] == 0


def test_saved_schema_valid_real_mismatches_receive_expected_binding_diagnostic_read_only():
    database = os.environ.get("FOREST_BINDING_FEEDBACK_REQUEST_DB")
    if not database:
        pytest.skip("Set an explicit read-only incident fixture database")
    with sqlite3.connect("file:" + str(Path(database).resolve()) + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT request_json,response_json FROM forest_study_model_calls WHERE request_id LIKE '37ccea69%' OR request_id LIKE 'c662ed91%' OR request_id LIKE 'cb2d6d31%' OR request_id LIKE '80097c55%'").fetchall()
    assert len(rows) == 4
    from research.experiments.study_context import _message_json_blocks, restore_messages
    for request_json, response_json in rows:
        request, response = json.loads(request_json), json.loads(response_json)
        decision = _restore_strict_optional_nulls(json.loads(response["text"]), request["decision_schema"])
        _validate_episode_schema(decision, request["decision_schema"])
        assert restore_messages(request["messages"]) == request["original_messages"]
        bindings = next(value["action_bindings"] for message in request["original_messages"]
            for _, _, value in _message_json_blocks(message["content"])
            if isinstance(value, dict) and isinstance(value.get("action_bindings"), list))
        action = decision["actions"][0]
        expected = next(row for row in bindings if row["work_id"] == action["action_work_id"])
        with pytest.raises(ValueError) as error:
            _validate_action_bindings({"action_bindings": bindings}, decision["actions"])
        reason = str(error.value)
        assert json.dumps(action["configuration_id"]) in reason
        assert json.dumps(expected["configuration_id"]) in reason
        assert action["action_work_id"] in reason and "null/omitted" in reason
        corrected = {**action, "configuration_id": expected["configuration_id"]}
        _validate_action_bindings({"action_bindings": bindings}, [corrected])
