"""Current guard feedback with real nonformal CPU analysis, no model/GPU calls."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from research.experiments.study_context import restore_messages
from research.experiments.study_runtime import StudyRuntime, _treatment_history_view


def _fixture(tmp_path, *, arm="FLAT"):
    from test_study_runtime import manifest
    evidence, pairs, originals = [], {"other": [], "candidate": []}, {}
    for seed in (11, 12):
        ids = {}
        for configuration_id, value in (("anchor", 1.0), ("other", .9), ("candidate", .8)):
            work_id = f"nonformal-{configuration_id}-{seed}"
            path = tmp_path / (work_id + ".json")
            path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": work_id,
                "configuration_id": configuration_id, "seed": seed,
                "validation_metrics": {"fixture_mae": value}, "hidden_test_opened": False,
                "formal_score_eligible": False, "test_fixture": "explicit nonformal input; no fit occurred"}), encoding="utf-8")
            originals[path] = path.read_bytes()
            evidence.append({"work_id": work_id, "configuration_id": configuration_id,
                "seed": seed, "receipt_path": str(path)})
            ids[configuration_id] = work_id
        for target in pairs:
            pairs[target].append({"left": ids["anchor"], "right": ids[target]})
    selection = {"work_id": "nonformal-selector", "kind": "analysis", "action_name": "select",
        "configuration_id": "task", "seed": None, "analysis_operation": "select",
        "metric": "fixture_mae", "direction": "minimize", "exclude_anchor": True,
        "anchor_configuration_id": "anchor", "evidence": evidence,
        "output_path": str(tmp_path / "selection.json")}
    comparisons = [{"work_id": f"nonformal-compare-{target}", "kind": "analysis", "action_name": "compare",
        "configuration_id": "task", "seed": None, "analysis_operation": "compare",
        "selection_work_id": selection["work_id"], "metric": "fixture_mae",
        "evidence": [entry for entry in evidence if entry["configuration_id"] in ("anchor", target)],
        "pairs": pairs[target], "compared_configuration_ids": ["anchor", target],
        "compared_seeds": [11, 12], "output_path": str(tmp_path / (target + "-comparison.json"))}
        for target in pairs]
    bindings = [selection, *comparisons]
    payload = {"arm": arm, "allowed_decisions": ["select", "compare"], "action_bindings": bindings,
        "resource_policy": {"active_wall_seconds": None, "actual_gpu_seconds": 480},
        "messages": [{"role": "user", "content": "Nonformal registered original source:\n" + json.dumps({
            "action_bindings": [{key: binding[key] for key in
                ("action_name", "configuration_id", "seed", "kind", "work_id")} for binding in bindings]})}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
                "actions": {"type": "array", "items": {"type": "object", "required": ["kind", "action_work_id"],
                    "properties": {"kind": {"type": "string", "enum": ["select", "compare"]},
                        "action_work_id": {"type": "string", "enum": [binding["work_id"] for binding in bindings]},
                        "configuration_id": {"type": ["string", "null"]}, "seed": {"type": ["integer", "null"]}}}},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest([{"work_id": "episode", "kind": "episode", "payload": payload}])), encoding="utf-8")
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite", ledger_id="ledger-test", manifest=path)
    return runtime, {"work_id": "episode", "kind": "episode", "payload": payload}, originals


def _action(kind, work_id):
    return {"kind": kind, "action_work_id": work_id, "configuration_id": "task", "seed": None}


def _decision(actions):
    return {"mode": "action", "actions": actions, "submission": {},
        "rationale": "Nonformal source-bound CPU guard fixture."}


def _complete_selection(runtime, episode):
    result = runtime.execute_decision_actions(episode, [_action("select", "nonformal-selector")])
    assert result["state"] == "completed"
    assert result["actions"][0]["result"]["selected_configuration_id"] == "candidate"
    with runtime.ledger.connect() as db:
        return tuple(db.execute("SELECT payload_json,result_json,updated_at FROM forest_study_work WHERE work_id='nonformal-selector'").fetchone())


def test_current_selected_configuration_feedback_preserves_batch_stop_and_original_selector(tmp_path):
    runtime, episode, originals = _fixture(tmp_path)
    selector = _complete_selection(runtime, episode)
    before = deepcopy(episode)
    rejected = runtime.execute_decision_actions(episode, [
        _action("compare", "nonformal-compare-other"),
        _action("compare", "nonformal-compare-candidate"), _action("select", "nonformal-selector")])
    assert rejected["state"] == "retryable" and rejected["request_new_decision"] is True
    assert len(rejected["actions"]) == 1
    feedback = rejected["response_validation_feedback"]
    assert feedback == rejected["actions"][0]["response_validation_feedback"]
    assert feedback["source_selection"] == {"work_id": "nonformal-selector", "state": "completed", "result": json.loads(selector[1])}
    assert feedback["registered_next_actions"] == [_action("compare", "nonformal-compare-candidate")]
    assert feedback["proposed_comparison_configuration_id"] == "other"
    assert "cached original result" in feedback["instruction"]
    assert "later actions in this batch were not executed" in feedback["instruction"]
    assert not (tmp_path / "candidate-comparison.json").exists()
    assert not (tmp_path / "other-comparison.json").exists()
    assert episode == before and {path: path.read_bytes() for path in originals} == originals
    with runtime.ledger.connect() as db:
        assert tuple(db.execute("SELECT payload_json,result_json,updated_at FROM forest_study_work WHERE work_id='nonformal-selector'").fetchone()) == selector
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE work_id LIKE 'nonformal-compare-%'").fetchone()[0] == 0


def test_completed_prefix_remains_evidence_and_guard_does_not_execute_suffix(tmp_path):
    runtime, episode, _ = _fixture(tmp_path)
    selector = _complete_selection(runtime, episode)
    result = runtime.execute_decision_actions(episode, [
        _action("compare", "nonformal-compare-candidate"),
        _action("compare", "nonformal-compare-other"), _action("select", "nonformal-selector")])
    assert [row["state"] for row in result["actions"]] == ["completed", "retryable"]
    assert result["state"] == "retryable" and result["request_new_decision"] is True
    assert result["actions"][0]["result"]["paired_differences_right_minus_left"] == pytest.approx([-.2, -.2])
    assert result["response_validation_feedback"] == result["actions"][1]["response_validation_feedback"]
    assert (tmp_path / "candidate-comparison.json").exists()
    assert not (tmp_path / "other-comparison.json").exists()
    with runtime.ledger.connect() as db:
        assert tuple(db.execute("SELECT payload_json,result_json,updated_at FROM forest_study_work WHERE work_id='nonformal-selector'").fetchone()) == selector


@pytest.mark.parametrize("state", [None, "running", "failed"])
def test_missing_selection_feedback_identifies_current_state_without_claiming_completion(tmp_path, state):
    runtime, episode, _ = _fixture(tmp_path)
    if state is not None:
        binding = episode["payload"]["action_bindings"][0]
        runtime.ledger.register_action(runtime.campaign_id, binding["work_id"], binding, parent_id="episode")
        runtime.ledger.set_state(runtime.campaign_id, binding["work_id"], state)
    rejected = runtime.execute_decision_actions(episode, [_action("compare", "nonformal-compare-candidate")])
    feedback = rejected["response_validation_feedback"]
    assert feedback["reason"] == "source_selection_must_complete_before_comparison"
    assert feedback["source_selection"] == {"work_id": "nonformal-selector", "state": state or "not_registered", "result": {}}
    assert feedback["registered_next_actions"] == [_action("select", "nonformal-selector")]
    assert "when eligible" in feedback["instruction"]
    assert "pending or failed selection is not completed evidence" in feedback["instruction"]
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE work_id LIKE 'nonformal-compare-%'").fetchone()[0] == 0


def test_missing_matching_binding_does_not_invent_an_action_id(tmp_path):
    runtime, episode, _ = _fixture(tmp_path)
    _complete_selection(runtime, episode)
    episode["payload"]["action_bindings"] = episode["payload"]["action_bindings"][:-1]
    result = runtime.execute_decision_actions(episode, [_action("compare", "nonformal-compare-other")])
    assert result["response_validation_feedback"]["registered_next_actions"] == []
    assert "if none is available" in result["response_validation_feedback"]["instruction"]


@pytest.mark.parametrize("arm", ["FULL", "FULL_NO_GATE", "FLAT", "DAG_ID", "ID_ONLY", "RERUN_ALL", "AIDE"])
def test_every_history_arm_keeps_current_guard_feedback(tmp_path, arm):
    runtime, episode, _ = _fixture(tmp_path, arm=arm)
    _complete_selection(runtime, episode)
    result = runtime.execute_decision_actions(episode, [_action("compare", "nonformal-compare-other")])
    delivered = json.dumps(_treatment_history_view(arm, [{"actions": result["actions"]}]))
    assert json.dumps(result["response_validation_feedback"]) in delivered


def test_fresh_followup_after_reload_receives_exact_feedback_and_computes_chosen_cpu_comparison(tmp_path):
    from test_aide_runtime import NonformalClient
    runtime, episode, originals = _fixture(tmp_path)
    selector = _complete_selection(runtime, episode)
    client = NonformalClient([json.dumps(_decision([
        _action("compare", "nonformal-compare-other"), _action("compare", "nonformal-compare-candidate")])),
        json.dumps(_decision([_action("compare", "nonformal-compare-candidate")]))])
    first = runtime.run_one(owner="nonformal-first", client=client)
    assert first["response_validation_feedback"]["registered_next_actions"] == [_action("compare", "nonformal-compare-candidate")]
    assert not (tmp_path / "candidate-comparison.json").exists()
    with runtime.ledger.connect() as db:
        old_call = tuple(db.execute("SELECT request_json,response_json FROM forest_study_model_calls").fetchone())
    restored = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite", ledger_id="ledger-test", manifest=tmp_path / "manifest.json")
    second = restored.run_one(owner="nonformal-second", client=client)
    assert len(client.calls) == 2 and second.get("resumed_saved_decision", False) is False
    result = second["action_result"]["actions"][0]
    assert result["state"] == "completed"
    assert result["result"]["paired_differences_right_minus_left"] == pytest.approx([-.2, -.2])
    assert {path: path.read_bytes() for path in originals} == originals
    with restored.ledger.connect() as db:
        calls = db.execute("SELECT request_json,response_json FROM forest_study_model_calls ORDER BY recorded_at").fetchall()
        assert tuple(calls[0]) == old_call
        request = json.loads(calls[1][0])
        delivered = restore_messages(client.calls[1][0])
        assert delivered == request["original_messages"]
        feedback_messages = [message for message in delivered if "Validation feedback: " in message.get("content", "")]
        assert len(feedback_messages) == 1
        assert json.dumps(first["response_validation_feedback"], sort_keys=True) in feedback_messages[0]["content"]
        assert "before any action was executed" not in feedback_messages[0]["content"]
        assert tuple(db.execute("SELECT payload_json,result_json,updated_at FROM forest_study_work WHERE work_id='nonformal-selector'").fetchone()) == selector
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind IN ('fit','infer')").fetchone()[0] == 0


def test_native_guard_feedback_survives_journal_reload_and_fresh_chosen_cpu_comparison(tmp_path):
    from test_aide_runtime import NonformalClient, _runtime, _review
    from research.experiments.aide_runtime import load_upstream, run_aide_episode
    native_upstream = Path(os.environ.get("FOREST_AIDE_UPSTREAM", "/home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/executors/aide_upstream"))
    if not native_upstream.is_dir():
        pytest.skip("Actual official AIDE clone unavailable")
    _, episode, originals = _fixture(tmp_path)
    runtime, item = _runtime(tmp_path, native_upstream)
    item["payload"].update(episode["payload"], arm="AIDE")
    selector = _complete_selection(runtime, item)
    def program(actions):
        return "Nonformal guard fixture.\n```python\nimport json\ndecision = " + repr(_decision(actions)) + "\nprint(json.dumps(decision))\n```"
    client = NonformalClient([program([_action("compare", "nonformal-compare-other"), _action("compare", "nonformal-compare-candidate")]),
        _review(buggy=True), program([_action("compare", "nonformal-compare-candidate")]), _review(metric=None, buggy=True)])
    first = run_aide_episode(runtime, item, client=client)
    execution = first["action_result"]
    assert execution["exc_type"] == "ScientificActionNeedsReplan"
    assert execution["pending_work_ids"] == [] and len(execution["action_results"]) == 1
    feedback = execution["response_validation_feedback"]
    assert feedback["registered_next_actions"] == [_action("compare", "nonformal-compare-candidate")]
    assert not (tmp_path / "candidate-comparison.json").exists()
    assert json.dumps(feedback, sort_keys=True) in execution["stdout"]
    assert "nonformal-compare-candidate" in "\n".join(message["content"] for message in restore_messages(client.calls[1][0]))
    with runtime.ledger.connect() as db:
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
        old_calls = [tuple(row) for row in db.execute("SELECT request_json,response_json FROM forest_study_model_calls ORDER BY recorded_at")]
    upstream = load_upstream(native_upstream)
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    assert isinstance(journal.nodes[0].exc_info, dict)
    restored, restored_item = _runtime(tmp_path, native_upstream)
    restored_item["payload"] = deepcopy(item["payload"])
    second = run_aide_episode(restored, restored_item, client=client)
    assert len(client.calls) == 4 and second["native_completed_steps"] == 2
    assert second["action_result"]["action_results"][0]["result"]["paired_differences_right_minus_left"] == pytest.approx([-.2, -.2])
    assert {path: path.read_bytes() for path in originals} == originals
    with restored.ledger.connect() as db:
        calls = [tuple(row) for row in db.execute("SELECT request_json,response_json FROM forest_study_model_calls ORDER BY recorded_at")]
        assert calls[:2] == old_calls
        assert tuple(db.execute("SELECT payload_json,result_json,updated_at FROM forest_study_work WHERE work_id='nonformal-selector'").fetchone()) == selector
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE kind IN ('fit','infer')").fetchone()[0] == 0
