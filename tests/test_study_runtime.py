import json
import sqlite3
from pathlib import Path

import pytest

from research.experiments.study_runtime import (StudyLedger, StudyRuntime,
    _manifest_work_items, _manifest_edges, _known_source_preflight_failure,
    numpy_score_predictions, _normalize_episode_payload, _episode_mutation_id,
    _c2_evaluation_overrides, _episode_metric_contract, verify_episode_submission,
    execute_registered_analysis, _codex_strict_schema, _restore_strict_optional_nulls,
    _transport_trace_reference, _validate_episode_schema)


def manifest(items, edges=(), *, version=1):
    return {
        "campaign_id": "campaign-test", "manifest_id": "manifest-test",
        "manifest_version": version, "external_ledger_id": "ledger-test",
        "execution_state": {"task_queue": items},
        "graph": {"edges": [dict(source=s, target=t, relation=r) for s, t, r in edges]},
    }


def item(work_id, **extra):
    return {"work_id": work_id, "phase": "formal", "kind": "fit",
            "configuration_path": f"/tmp/{work_id}.json", "fit_timeout_seconds": 10,
            "output_directory": f"/tmp/out-{work_id}", **extra}


def test_manifest_registration_versions_only_unlaunched_work_revisions(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    data = manifest([item("fit-1")])
    assert ledger.register(data) == {"inserted": 1, "unchanged": 0, "revised": 0, "work_items_seen": 1}
    assert ledger.register(data) == {"inserted": 0, "unchanged": 1, "revised": 0, "work_items_seen": 1}
    changed = manifest([item("fit-1", optimizer_updates=100)], version=2)
    assert ledger.register(changed) == {"inserted": 0, "unchanged": 0, "revised": 1, "work_items_seen": 1}
    with ledger.connect() as db:
        current = json.loads(db.execute("SELECT payload_json FROM forest_study_work WHERE work_id='fit-1'").fetchone()[0])
        prior = db.execute("SELECT manifest_version,old_payload_json,new_payload_json FROM forest_study_work_revisions WHERE work_id='fit-1'").fetchone()
        event = db.execute("SELECT count(*) FROM forest_study_events WHERE work_id='fit-1' AND event_type='planned_work_revised'").fetchone()[0]
    assert current["optimizer_updates"] == 100
    assert prior[0] == 2 and json.loads(prior[1]).get("optimizer_updates") is None
    assert json.loads(prior[2])["optimizer_updates"] == 100
    assert event == 1
    changed_again = manifest([item("fit-1", optimizer_updates=200)], version=2)
    with pytest.raises(ValueError, match="changed conditions"):
        ledger.register(changed_again)
    with ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_work").fetchone()[0] == 1


@pytest.mark.parametrize("external_evidence", ["claim", "released_claim", "supervisor", "model_call", "fit_attempt"])
def test_manifest_revision_rejected_after_claim_or_external_execution_evidence(tmp_path, external_evidence):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    ledger.register(manifest([item("fit-1")]))
    if external_evidence == "claim":
        claimed = ledger.claim("campaign-test", "worker")
        assert claimed["work_id"] == "fit-1"
    elif external_evidence == "released_claim":
        claimed = ledger.claim("campaign-test", "worker")
        ledger.release_claim("campaign-test", "fit-1", "worker")
    elif external_evidence == "supervisor":
        with ledger.connect() as db:
            db.execute("CREATE TABLE forest_compute_work(work_id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO forest_compute_work VALUES('fit-1')")
    elif external_evidence == "model_call":
        with ledger.connect() as db:
            db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at) VALUES('req','campaign-test','fit-1','{}','completed',1)")
    else:
        with ledger.connect() as db:
            db.execute("INSERT INTO forest_study_fit_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                ("campaign-test", "fit-1", 0, "fit-1", "/tmp/out", "prepared", None, 1.0, 1.0))
    changed = manifest([item("fit-1", optimizer_updates=100)], version=2)
    with pytest.raises(ValueError, match="changed conditions after claim/execution"):
        ledger.register(changed)


def test_runtime_manifest_reload_skips_unchanged_document_and_registers_new_revision(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    path = campaign / "manifest.json"
    data = manifest([item("fit-1")])
    path.write_text(json.dumps(data), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                          ledger_id="ledger-test", manifest=path)
    with runtime.ledger.connect() as db:
        events_before = db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='manifest_registered'").fetchone()[0]
    runtime.reload_manifest()
    with runtime.ledger.connect() as db:
        events_after = db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='manifest_registered'").fetchone()[0]
    assert events_after == events_before
    data["manifest_version"] = 2
    data["execution_state"]["task_queue"].append(item("fit-2"))
    path.write_text(json.dumps(data), encoding="utf-8")
    runtime.reload_manifest()
    assert runtime.ledger.counts("campaign-test")["queued"] == 2


def test_claim_waits_for_required_edge_but_derived_from_is_nonblocking(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    data = manifest([item("parent"), item("child-requires"), item("child-derived")], [
        ("parent", "child-requires", "requires_valid_execution"),
        ("parent", "child-derived", "derived_from"),
    ])
    ledger.register(data)
    first = ledger.claim("campaign-test", "worker")
    parent_id = first["work_id"]
    assert parent_id in {"parent", "child-derived"}
    ledger.set_state("campaign-test", parent_id, "running")
    # Derived lineage does not create a scheduling deadlock.
    next_item = ledger.claim("campaign-test", "worker")
    if parent_id == "parent":
        assert next_item["work_id"] == "child-derived"
        ledger.set_state("campaign-test", "child-derived", "completed")
        assert ledger.claim("campaign-test", "worker") is None
        ledger.set_state("campaign-test", "parent", "completed")
        assert ledger.claim("campaign-test", "worker")["work_id"] == "child-requires"
    else:
        assert next_item["work_id"] == "child-requires" or next_item["work_id"] == "child-derived"


def test_claim_heartbeat_and_release_preserve_retryable_work(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    ledger.register(manifest([item("fit-1")]))
    claimed = ledger.claim("campaign-test", "scheduler")
    assert claimed["state"] == "claimed" and claimed["lease_owner"] == "scheduler"
    ledger.heartbeat("campaign-test", "fit-1", "scheduler")
    ledger.release_claim("campaign-test", "fit-1", "scheduler", state="retryable")
    again = ledger.claim("campaign-test", "gpu-worker")
    assert again["work_id"] == "fit-1" and again["lease_owner"] == "gpu-worker"


def test_requires_settled_barrier_unlocks_after_failed_parent_without_calling_it_success(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    data = manifest([item("fit-1"), {"work_id": "calibrate", "kind": "calibration_decision"}], [
        ("fit-1", "calibrate", "requires_settled")])
    ledger.register(data)
    assert ledger.claim("campaign-test", "worker", work_ids=["calibrate"]) is None
    ledger.set_state("campaign-test", "fit-1", "failed", error="fit timeout")
    decision = ledger.claim("campaign-test", "worker", work_ids=["calibrate"])
    assert decision["work_id"] == "calibrate"
    with ledger.connect() as db:
        state = db.execute("SELECT state FROM forest_study_work WHERE work_id='fit-1'").fetchone()[0]
    assert state == "failed"


def test_episode_model_timeout_keeps_provider_request_timeout_without_cell_wall_cap(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                          ledger_id="ledger-test", manifest=path)
    with runtime.ledger.connect() as db:
        db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at,elapsed_seconds) VALUES(?,?,?,?,?,?,?)",
            ("req", "campaign-test", "ep-1", "{}", "completed", 1.0, 119.0))
    client = type("Client", (), {"timeout": 120.0, "config": {}})()
    item = {"work_id": "ep-1", "kind": "episode", "payload": {
        "resource_budgets": {"active_wall_seconds": 1800, "gpu_seconds": 480}}}
    bounded = runtime._budgeted_episode_client(item, client)
    assert bounded is not client and bounded.timeout == 120.0
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_model_calls SET elapsed_seconds=1799 WHERE request_id='req'")
    bounded = runtime._budgeted_episode_client(item, client)
    assert bounded.timeout == 120.0 and client.timeout == 120.0
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_model_calls SET elapsed_seconds=1800 WHERE request_id='req'")
    assert runtime._budgeted_episode_client(item, client).timeout == 120.0


def test_actual_manifest_episode_envelope_normalizes_without_losing_factual_or_source_fields():
    payload = {"episode": {"episode_id": "cell-1", "category": "LIVE_MAIN",
        "arm": "FULL", "stream": "EVAL_UPDATE", "task_id": "D01", "family": "darcy",
        "repeat": 0, "mutation": {"notice_fields": ["primary_metric", "validation_inputs"]}},
        "factual_changes": {"evaluation_contract": {"revised_primary_metric": "relative_gradient_l2",
            "revised_direction": "minimize"}, "validation_input_config": {
                "validation_noise_seed": 20261012, "validation_noise_std": 0.02}},
        "source_snapshot_plan": "/campaign/snapshot-plan.json",
        "source_snapshot_result_directory": "/campaign/runs/shared_snapshot",
        "source_data_root": "/campaign/data/researcher", "source_spec_dir": "/campaign/frozen/source_spec",
        "data_ids_reference": "/campaign/data-selection.json", "dataset_version_ids": {"darcy": "data-v1"},
        "resolved_updates_path": "/campaign/resolved-updates.json",
        "resource_policy": {"active_wall_seconds": 1800, "actual_gpu_seconds": 480, "replay_only": False}}
    normalized = _normalize_episode_payload(payload)
    assert (normalized["task_id"], normalized["family"], normalized["arm"], normalized["stream"]) == (
        "D01", "darcy", "FULL", "EVAL_UPDATE")
    assert normalized["snapshot_plan_path"] == payload["source_snapshot_plan"]
    assert normalized["dataset_root"] == payload["source_data_root"]
    assert _episode_mutation_id(normalized) == "C5"
    assert _c2_evaluation_overrides("darcy", normalized["factual_changes"]["validation_input_config"],
        normalized["factual_changes"], "C1") == {
            "validation_noise_std": 0.02, "validation_noise_seed": 20261012,
            "evaluation_revision_id": "C1"}


def test_arm_public_context_is_exactly_paged_and_reads_are_observational(tmp_path):
    from research.experiments.study_memory import StudyMemory
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    original_content = json.dumps({"source_rows": [{"id": f"row-{i}", "value": i} for i in range(31)]},
        separators=(",", ":"), ensure_ascii=False)
    prepared = {"work_id": "episode-read", "arm": "FULL", "stream": "EVAL_UPDATE",
        "messages": [{"role": "system", "content": "system contract"},
                     {"role": "user", "content": original_content}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
                "actions": {"type": "array", "items": {"type": "object", "required": ["kind", "action_work_id"],
                    "properties": {"kind": {"type": "string", "enum": ["inspect"]},
                        "action_work_id": {"type": "string", "enum": ["inspect-1"]}}}},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["inspect"], "action_bindings": [{"kind": "analysis", "action_name": "inspect",
            "work_id": "inspect-1", "analysis_operation": "inspect", "evidence": []}]}
    payload = runtime._attach_public_context(prepared, prepared)
    context_binding = next(row for row in payload["action_bindings"]
                           if row.get("action_name") == "read_context_segment")
    memory = StudyMemory(Path(context_binding["memory_workspace"]).parent)
    source_segment = payload["context_memory"]["segment_id"]
    assert source_segment in context_binding["available_segment_ids"]
    first = memory.read_json(source_segment, pointer="/messages/1/content", offset=0, limit=19)
    pieces = [first["content"]]
    cursor = first["next_offset"]
    while not first["complete"]:
        first = memory.read_json(source_segment, pointer="/messages/1/content", offset=cursor, limit=19)
        pieces.append(first["content"])
        cursor = first["next_offset"]
    encoded_source_string = json.dumps(original_content, ensure_ascii=False, separators=(",", ":"))
    assert "".join(pieces) == encoded_source_string
    assert json.loads("".join(pieces)) == original_content
    action = {"kind": "read_context_segment", "action_work_id": context_binding["work_id"],
        "context_segment_id": source_segment, "context_pointer": "/messages/1/content",
        "context_offset": 19, "context_limit": 37}
    result = runtime.execute_decision_actions({"work_id": "episode-read", "payload": payload}, [action])
    assert result["state"] == "completed"
    page = result["actions"][0]["result"]
    assert page["operation"] == "read_context_segment" and page["scientific_progress_eligible"] is False
    assert page["content"] == encoded_source_string[19:56]
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_work WHERE work_id=?", (context_binding["work_id"],)).fetchone()[0] == 0
    history = [{"actions": result["actions"]}]
    repeated_payload = runtime._attach_public_context(prepared, payload, history=history)
    assert repeated_payload["context_memory"]["segment_id"] == source_segment
    with_reference_note = json.loads(json.dumps(payload))
    with_reference_note["messages"].insert(0, {"role": "system",
        "content": "Previously retrieved exact source pages (references only): " +
            json.dumps([{"segment_id": source_segment, "pointer": "/messages/1/content",
                         "offset": 0, "next_offset": 19}])})
    same_public_source = runtime._attach_public_context(with_reference_note, with_reference_note)
    assert same_public_source["context_memory"]["segment_id"] == source_segment
    rejected = verify_episode_submission(payload, history, {"claim": "read only"}, runtime=runtime)
    assert rejected["valid"] is False and rejected["reason"] == "no_completed_source_bound_action"
    from research.experiments.study_runtime import _episode_source_frontier, _treatment_history_view
    assert _episode_source_frontier(payload, history)["completed_source_actions"] == {}
    view = _treatment_history_view("FULL", history)
    assert view["nodes"] == [] and len(view["observational_context_reads"]) == 1


def test_context_retrieval_is_attached_to_replay_and_aide_public_builders(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    for arm, category in (("AIDE", "LIVE_MAIN"), ("ID_ONLY", "REPLAY")):
        prepared = {"work_id": f"episode-{category}", "arm": arm, "category": category,
            "messages": [{"role": "user", "content": '{"arm":"' + arm + '"}'}],
            "decision_schema": {"type": "object", "properties": {"actions": {"type": "array",
                "items": {"type": "object", "properties": {"kind": {"enum": ["inspect"]}}}}}},
            "allowed_decisions": ["inspect"], "action_bindings": [{"kind": "analysis", "action_name": "inspect",
                "work_id": "inspect-" + category, "analysis_operation": "inspect", "evidence": []}]}
        payload = runtime._attach_public_context(prepared, prepared)
        assert payload["context_memory"]["read_operation"] == "read_context_segment"
        context_binding = next(row for row in payload["action_bindings"]
            if row.get("action_name") == "read_context_segment")
        item_schema = payload["decision_schema"]["properties"]["actions"]["items"]["properties"]
        assert "read_context_segment" in item_schema["kind"]["enum"]
        assert context_binding["work_id"] in item_schema["action_work_id"]["enum"]
        action = {"kind": "read_context_segment", "action_work_id": context_binding["work_id"],
            "context_segment_id": payload["context_memory"]["segment_id"], "context_pointer": "/messages/0/content",
            "context_offset": 0, "context_limit": 100}
        read = runtime.execute_decision_actions({"work_id": prepared["work_id"], "payload": payload}, [action])
        assert read["actions"][0]["result"]["operation"] == "read_context_segment"


def test_context_page_body_is_only_current_turn_while_prior_pages_remain_referenced():
    from research.experiments.study_runtime import _treatment_history_view

    page = {"operation": "read_context_segment", "segment_id": "source-segment",
        "pointer": "/messages/0/content", "offset": 0, "next_offset": 12,
        "total_characters": 24, "returned_characters": 12, "requested_limit": 12,
        "complete": False, "content": "exact-page-1"}
    history = [{"actions": [{"action_work_id": "read", "kind": "analysis",
        "state": "completed", "result": page}]}, {"actions": [{"action_work_id": "inspect",
        "kind": "analysis", "state": "completed", "result": {"status": "not_run"}}]}]
    reads = _treatment_history_view("FULL", history)["observational_context_reads"]
    assert len(reads) == 1
    assert "content" not in reads[0]
    assert reads[0]["segment_id"] == "source-segment" and reads[0]["next_offset"] == 12


def test_refit_does_not_advance_a_historical_context_page_cursor(tmp_path):
    import copy

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    runtime._context_response_capacity = lambda _client: 1_048_576
    historical_page = {"operation": "read_context_segment", "segment_id": "source-segment",
        "pointer": "/messages/0/content", "offset": 0, "next_offset": 123,
        "total_characters": 500, "returned_characters": 123, "requested_limit": 500,
        "complete": False, "content": "x" * 123, "delivery_status": "delivered"}
    history = [{"actions": [{"action_work_id": "read", "kind": "analysis",
        "state": "completed", "result": historical_page}]},
        {"actions": [{"action_work_id": "inspect", "kind": "analysis",
            "state": "completed", "result": {"status": "not_run"}}]}]
    original = copy.deepcopy(history)
    payload = {"action_bindings": [{"action_name": "read_context_segment",
        "work_id": "read", "memory_workspace": str(campaign)}]}

    actual = runtime._refit_latest_context_page(payload, history, client=object())

    assert actual == original
    assert actual[0]["actions"][0]["result"]["next_offset"] == 123
    assert actual[0]["actions"][0]["result"]["returned_characters"] == 123


def test_unfit_final_character_page_does_not_claim_source_complete(tmp_path):
    from research.experiments.study_memory import StudyMemory

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    prepared = {"work_id": "episode-read-tail", "arm": "FULL", "stream": "EVAL_UPDATE",
        "messages": [{"role": "user", "content": "remaining-source-character"}],
        "action_bindings": [{"kind": "analysis", "action_name": "inspect", "work_id": "inspect-1",
            "analysis_operation": "inspect"}]}
    payload = runtime._attach_public_context(prepared, prepared)
    binding = next(row for row in payload["action_bindings"] if row.get("action_name") == "read_context_segment")
    memory_workspace = Path(binding["memory_workspace"])
    memory = StudyMemory(memory_workspace.parent if memory_workspace.name == ".forest-context"
                         else memory_workspace)
    segment_id = payload["context_memory"]["segment_id"]
    original = memory.read_json(segment_id, pointer="/messages/0/content", limit=1000)
    tail_offset = original["total_characters"] - 1

    action = runtime.execute_decision_action({"payload": payload}, {
        "kind": "read_context_segment", "action_work_id": binding["work_id"],
        "context_segment_id": segment_id, "context_pointer": "/messages/0/content",
        "context_offset": tail_offset, "context_limit": 1},
        context_response_capacity=1, context_read_measure=lambda _page: 2)

    page = action["result"]
    assert page["delivery_status"] == "not_delivered"
    assert page["offset"] == tail_offset == page["next_offset"]
    assert page["returned_characters"] == 0 and page["content"] is None
    assert page["complete"] is False


def test_context_response_capacity_uses_minimum_declared_and_new_observed_trace(tmp_path, monkeypatch):
    from types import SimpleNamespace

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    executable = campaign / "codex-fixture"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("FOREST_CODEX_EXECUTABLE", str(executable))
    client = SimpleNamespace(api="codex_cli", provider={"id": "codex", "model": "fixture",
        "kind": "codex_cli"}, config={"max_input_chars": 2_000_000})

    def record_rejection(request_id, maximum, trace_executable=executable):
        trace = campaign / f"{request_id}.json"
        trace.write_text(json.dumps({"stderr":
            "Error: turn/start: turn/start failed: invalid params data: " +
            json.dumps({"input_error_code": "input_too_large", "max_chars": maximum,
                        "actual_chars": maximum + 1}),
            "argv": [str(trace_executable)]}), encoding="utf-8")
        request = {"provider_api": "codex_cli", "transport_trace_path": str(trace)}
        response = {"observed_cli_error": "input_too_large", "transport_trace_path": str(trace)}
        with runtime.ledger.connect() as db:
            db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,response_json,status,recorded_at) "
                       "VALUES(?,?,?,?,?,?,?)", (request_id, "campaign-test", "fixture-work",
                           json.dumps(request), json.dumps(response), "failed", 1.0))

    assert runtime._context_response_capacity(client) == 2_000_000
    record_rejection("observed-first", 1_048_576)
    assert runtime._context_response_capacity(client) == 1_048_576
    record_rejection("observed-smaller", 900_000)
    assert runtime._context_response_capacity(client) == 900_000
    record_rejection("different-cli", 700_000, campaign / "another-codex")
    assert runtime._context_response_capacity(client) == 900_000
    sources = runtime._context_capacity_sources
    assert {row.get("request_id") for row in sources if row.get("request_id")} == {
        "observed-first", "observed-smaller"}


def test_page_fitting_uses_full_next_request_with_same_batch_inspection_results(tmp_path):
    from research.agents.provider import ModelClient
    from research.experiments.study_memory import StudyMemory

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    prepared = {"work_id": "episode-mixed-read", "arm": "FULL", "messages": [
        {"role": "system", "content": "registered public task"},
        {"role": "user", "content": json.dumps({"public_notice": "source is stored in an arm-public artifact"})}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
                "actions": {"type": "array", "items": {"type": "object",
                    "required": ["kind", "action_work_id"], "properties": {
                        "kind": {"type": "string", "enum": ["inspect"]},
                        "action_work_id": {"type": "string", "enum": ["inspect-1"]}}}},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["inspect"], "action_bindings": [{"kind": "analysis",
            "action_name": "inspect", "work_id": "inspect-1", "analysis_operation": "inspect",
            "evidence": []}]}
    payload = runtime._attach_public_context(prepared, prepared)
    read_binding = next(row for row in payload["action_bindings"]
        if row.get("action_name") == "read_context_segment")
    client = ModelClient({"id": "nonformal-wire-fixture", "kind": "codex_cli",
        "base_url": "http://127.0.0.1", "model": "gpt-6-luna",
        "config": {"reasoning_effort": "medium"}}, request_guard=lambda _: None)
    measure = runtime._public_page_measure_callback(client, payload, [], read_binding["work_id"])
    inspect = {"action_work_id": "inspect-1", "kind": "analysis", "state": "completed",
        "result": {"operation": "inspect", "status": "not_run",
            "public_observation": [f"observed-{i:05d}" for i in range(1800)]}}
    memory = StudyMemory(Path(read_binding["memory_workspace"]).parent)
    large_text = "repeated-public-artifact-row;" * 60000
    segment_id = memory.save_json("large-public-artifact", {"text": large_text})["segment_id"]
    full = memory.read_json(segment_id, pointer="/text", limit=10_000_000)
    delivered = memory.read_json(segment_id, pointer="/text", limit=10_000_000,
        response_capacity=1_048_576,
        measure_response=lambda page: measure(page, prefix_results=[inspect]))
    assert delivered["next_offset"] > 0 and delivered["next_offset"] < full["total_characters"]
    assert measure(delivered, prefix_results=[inspect]) <= 1_048_576
    assert delivered["content"] == full["content"][:delivered["next_offset"]]


def test_manifest_replay_rejects_incomplete_snapshot_and_allows_zero_gpu_budget(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    doc = manifest([])
    doc["tasks"] = [{"task_id": "D01", "family_id": "darcy", "anchor_configuration_id": "anchor",
        "C1_revision": {"revised_primary_metric": "relative_l2", "revised_direction": "minimize"}}]
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    monkeypatch.setattr(runtime, "_episode_candidate_rows", lambda *_args: [{
        "config": {"work_id": "snapshot-fit", "task_id": "D01", "configuration_id": "anchor", "seed": 1001},
        "receipt_path": None, "receipt": None, "state": "not_run", "output_directory": campaign / "snapshot"}])
    # This adapter-only fixture does not represent completed snapshots. Keep it
    # focused on envelope normalization and zero-GPU replay budgeting.
    monkeypatch.setattr(runtime, "_select_original_pair", lambda _task, rows, _episode: {
        "rows": [], "record": {"status": "fixture_only", "task_id": "D01",
            "anchor_configuration_id": "anchor", "selected_non_anchor_configuration_id": None,
            "paired_seeds": []}})
    payload = {"work_id": "replay-1", "episode": {"episode_id": "replay-1", "category": "REPLAY",
        "arm": "ID_ONLY", "task_id": "D01", "family": "darcy", "case_id": "case-c2",
        "repeat": 0, "controller_mutation_id": "C2"},
        "factual_changes": {"validation_input_config": {"validation_noise_seed": 7, "validation_noise_std": 0.02}},
        "resource_policy": {"active_wall_seconds": 300, "actual_gpu_seconds": 0, "replay_only": True}}
    with pytest.raises(ValueError, match="incomplete or uncertified original record"):
        runtime.build_episode_request(payload)
    budget = runtime.episode_budget_status({"work_id": "replay-1", "kind": "replay", "payload": payload})
    assert budget["remaining"]["gpu_seconds"] == 0 and budget["state"] == "within_budget"
    assert budget["remaining"]["active_wall_seconds"] is None
    second = {**payload, "episode": {**payload["episode"], "category": "SECOND_MODEL_REPLAY"},
              "model_provider": {"id": "saved-secondary", "model": "gpt-6.1-sol",
                                 "config": {"reasoning_effort": "low"}}}
    normalized_second = _normalize_episode_payload(second)
    assert normalized_second["kind"] == "replay"
    assert normalized_second["category"] == "SECOND_MODEL_REPLAY"
    assert normalized_second["model_provider"]["id"] == "saved-secondary"
    second_budget = runtime.episode_budget_status(
        {"work_id": "replay-secondary", "kind": "episode", "payload": second})
    assert second_budget["limits"]["gpu_seconds"] == 0
    assert second_budget["limits"]["active_wall_seconds"] is None
    assert second_budget["limits"]["model_seconds"] is None


def test_aide_python_node_uses_common_bound_action_executor_and_caches_emission(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                          ledger_id="ledger-test", manifest=path)
    episode = {"work_id": "ep-aide", "kind": "episode", "payload": {
        "arm": "AIDE", "resource_policy": {"active_wall_seconds": 1800, "actual_gpu_seconds": 480},
        "task_contract": {"primary_metric": "mae", "primary_direction": "minimize"},
        "allowed_decisions": ["select"],
        "decision_schema": {"required": ["mode", "actions", "submission", "rationale"]},
        "action_bindings": [{"kind": "analysis", "action_name": "select", "work_id": "select-1",
            "configuration_id": "task-1", "seed": 7,
            "evidence": [{"configuration_id": "cfg-1", "receipt_path": str(campaign / "receipt.json")}]}]}}
    with runtime.ledger.connect() as db:
        db.execute("CREATE TABLE forest_study_aide_sessions(campaign_id TEXT,work_id TEXT,state_json TEXT,updated_at REAL,PRIMARY KEY(campaign_id,work_id))")
        db.execute("INSERT INTO forest_study_aide_sessions VALUES(?,?,?,?)",
            ("campaign-test", "ep-aide", json.dumps({"action_history": []}), 1.0))
    calls = []
    def action_runner(_episode, actions, **kwargs):
        calls.append((actions, kwargs))
        return {"state": "completed", "actions": [{"action_work_id": "select-1", "kind": "analysis",
            "action_name": "select", "configuration_id": "task-1", "seed": 7, "state": "completed",
            "result": {"operation": "select", "metric": "mae", "selected_configuration_id": "cfg-1",
                "selected_value": 0.4}}], "submission": {}}
    monkeypatch.setattr(runtime, "execute_decision_actions", action_runner)
    code = ('import json\n'
            'decision = {"mode":"action","actions":[{"kind":"select"}],"submission":{},"rationale":"Use the registered selector."}\n'
            'print(json.dumps(decision))\n')
    first = runtime.execute_aide_node(episode, node_id="node-1", code=code)
    second = runtime.execute_aide_node(episode, node_id="node-1", code=code)
    assert first["state"] == second["state"] == "completed"
    assert len(calls) == 2
    # The selector result is useful as a controller action, but this fixture
    # deliberately has no source-bound receipt. It must not be reported to the
    # native journal as an independently verified scientific metric.
    assert first["verified_metrics"] == []
    invalid_code = ('import json\n'
        'decision = {"mode":"action","actions":[{"kind":"select","action_work_id":"unknown-id"}],"submission":{},"rationale":"Use an unregistered ID."}\n'
        'print(json.dumps(decision))\n')
    invalid = runtime.execute_aide_node(episode, node_id="node-invalid", code=invalid_code)
    assert invalid["exc_type"] == "DecisionValidationError"
    assert "exactly one registered binding" in invalid["exc_info"]
    assert len(calls) == 2
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_aide_programs WHERE node_id='node-1'").fetchone()[0] == 1
    with pytest.raises(ValueError, match="reused with different Python source"):
        runtime.execute_aide_node(episode, node_id="node-1", code=code.replace("registered selector", "another action"))


def test_aide_registered_episode_uses_native_adapter_branch(tmp_path, monkeypatch):
    import research.experiments.aide_runtime as aide_runtime
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    episode = {"work_id": "ep-aide", "kind": "episode", "phase": "LIVE_MAIN",
        "arm": "AIDE", "resource_policy": {"active_wall_seconds": 1800, "actual_gpu_seconds": 480},
        "task_id": "task-1", "stream": "EVAL_UPDATE"}
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([episode])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                          ledger_id="ledger-test", manifest=path)
    dispatched = []
    def native_adapter(_runtime, item, *, client, gpu_devices, defer_gpu):
        dispatched.append((item["work_id"], client.timeout, tuple(gpu_devices), defer_gpu))
        return {"state": "retryable", "native_completed_steps": 1, "pending_work_ids": []}
    monkeypatch.setattr(aide_runtime, "run_aide_episode", native_adapter)
    client = type("Client", (), {"timeout": 30.0, "config": {},
        "provider": {"id": "local", "model": "gpt-6-luna"}})()
    outcome = runtime.run_one(owner="aide-test", client=client, defer_gpu=True)
    assert dispatched == [("ep-aide", 30.0, (), True)]
    assert outcome["state"] == "retryable"
    assert runtime.ledger.counts("campaign-test")["retryable"] == 1


@pytest.mark.parametrize("supervisor_state,expected_valid", [("completed", True), ("failed", False)])
def test_calibration_resolution_persists_valid_gate_and_requires_completed_supervisor(
        tmp_path, monkeypatch, supervisor_state, expected_valid):
    import research.experiments.study_runtime as module
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    config = {"work_id": "cal-fit", "family": "darcy", "calibration_role": "anchor",
        "optimizer_updates": 100, "configuration_id": "cfg-anchor", "seed": 17,
        "phase": "calibration", "formal_score_eligible": False}
    config_path = campaign / "cal.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = campaign / "cal-output"
    output.mkdir()
    (output / "predictions.npz").write_bytes(b"fixture only")
    receipt_path = output / "execution_receipt.json"
    receipt_path.write_text(json.dumps({"status": "SUCCEEDED", "effective_config": config,
        "hidden_test_opened": False, "formal_score_eligible": False,
        "validation_prediction_file": "predictions.npz"}), encoding="utf-8")
    resolved_path = campaign / "resolved.json"
    fit = {"work_id": "cal-fit", "kind": "fit", "phase": "calibration",
        "configuration_path": str(config_path), "output_directory": str(output), "fit_timeout_seconds": 60}
    decision = {"work_id": "cal-decision", "kind": "calibration_decision",
        "allowed_update_counts": {"darcy": [100]}, "required_roles_by_family": {"darcy": ["anchor"]},
        "primary_metric_by_family": {"darcy": {"metric": "relative_l2", "direction": "minimize"}},
        "resolved_updates_path": str(resolved_path)}
    doc = manifest([fit, decision])
    path = campaign / "manifest.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                          ledger_id="ledger-test", manifest=path)

    class FakeSupervisor:
        def __init__(self, *_args): pass
        def status(self, _work_id): return {"status": supervisor_state}

    monkeypatch.setattr(module, "_supervisor_has_work", lambda *_args: True)
    from research.experiments import gpu_supervisor
    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", FakeSupervisor)
    monkeypatch.setattr(module, "rescore_binding", lambda _binding: {"metrics": {"relative_l2": 0.25}})
    monkeypatch.setattr(module, "_calibration_decision_messages", lambda *_args: [{"role": "user", "content": "fixture"}])
    def candidates(rows, *_args):
        return {"darcy": [{"updates": 100}]} if rows[0]["state"] == "completed" else {}
    monkeypatch.setattr(module, "calibration_update_candidates", candidates)
    monkeypatch.setattr(module, "verify_calibration_choice", lambda choice, candidate_rows, *_args:
        (bool(candidate_rows), {"observed_candidate_count": sum(map(len, candidate_rows.values()))}))
    class ScriptedClient:
        provider = {"id": "fixture", "kind": "codex_cli", "model": "gpt-6-luna"}
        config = {"reasoning_effort": "low"}
        seen_trace_paths = []
        def complete(self, *_args, **_kwargs):
            self.seen_trace_paths.append(self.config.get("_forest_transport_trace_path"))
            return {"text": json.dumps({"family_updates": {"darcy": 100}, "readiness": "continue", "rationale": "fixture"}),
                    "usage": {"input_tokens": 1, "output_tokens": 1, "cost": None}}
    result = runtime.execute_calibration_decision({"work_id": "cal-decision", "payload": decision},
                                                  client=ScriptedClient())
    with runtime.ledger.connect() as db:
        saved_request = json.loads(db.execute("SELECT request_json FROM forest_study_model_calls").fetchone()[0])
    assert saved_request["transport_trace_path"] == ScriptedClient.seen_trace_paths[-1]
    assert result["source_validation"]["valid"] is expected_valid
    if expected_valid:
        saved = json.loads(resolved_path.read_text(encoding="utf-8"))
        assert saved["source_validation"]["valid"] is True
        assert saved["formal_score_eligible"] is False
        assert result["observed_rows"][0]["state"] == "completed"
    else:
        assert result["observed_rows"][0]["state"] == "invalid_receipt"


def test_calibration_transport_failure_preserves_provider_diagnostics_and_usage(tmp_path, monkeypatch):
    import research.experiments.study_runtime as module
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    config = {"work_id": "cal-fit-failed", "family": "darcy", "calibration_role": "anchor",
        "optimizer_updates": 100, "configuration_id": "cfg-anchor", "seed": 17,
        "phase": "calibration", "formal_score_eligible": False}
    config_path = campaign / "cal.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fit = {"work_id": "cal-fit-failed", "kind": "fit", "phase": "calibration",
        "configuration_path": str(config_path), "output_directory": str(campaign / "output"), "fit_timeout_seconds": 60}
    decision = {"work_id": "cal-decision-failed", "kind": "calibration_decision",
        "allowed_update_counts": {"darcy": [100]}, "required_roles_by_family": {"darcy": ["anchor"]},
        "primary_metric_by_family": {"darcy": {"metric": "relative_l2", "direction": "minimize"}},
        "resolved_updates_path": str(campaign / "resolved.json")}
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([fit, decision])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=path)

    class FailedSupervisor:
        def __init__(self, *_args): pass
        def status(self, _work_id): return {"status": "failed"}

    monkeypatch.setattr(module, "_supervisor_has_work", lambda *_args: True)
    from research.experiments import gpu_supervisor
    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", FailedSupervisor)
    monkeypatch.setattr(module, "_calibration_decision_messages", lambda *_args: [{"role": "user", "content": "fixture"}])

    class ProviderFailure(Exception):
        code = "input_too_large"
        partial_output = "thread.started"
        transport_diagnostics = {"actual_chars": 1_200_000, "max_chars": 1_048_576}
        usage = {"input_tokens": 7, "output_tokens": 0, "cost": None}

    class FailingClient:
        provider = {"id": "fixture", "kind": "codex_cli", "model": "gpt-6-luna", "allow_paid": False}
        config = {"reasoning_effort": "medium"}
        def complete(self, *_args, **_kwargs): raise ProviderFailure("fixture transport rejection")

    with pytest.raises(ProviderFailure):
        runtime.execute_calibration_decision({"work_id": "cal-decision-failed", "payload": decision},
            client=FailingClient())
    with runtime.ledger.connect() as db:
        call = db.execute("SELECT request_json,response_json,usage_json,status,finished_at,elapsed_seconds FROM forest_study_model_calls").fetchone()
    request, response, usage = json.loads(call[0]), json.loads(call[1]), json.loads(call[2])
    assert call[3] == "failed" and call[4] is not None and call[5] is not None
    assert usage == ProviderFailure.usage
    assert response["transport_error"]["code"] == "input_too_large"
    assert response["transport_error"]["partial_output"] == "thread.started"
    assert response["transport_error"]["transport_diagnostics"]["actual_chars"] == 1_200_000
    assert request["transport_trace_path"].endswith(f"{request['request_id']}.json")


def test_abstract_cells_are_not_promoted_to_runnable_evidence():
    doc = manifest([])
    doc["cells"] = [{"category": "LIVE_MAIN", "status": "pending", "task_id": "D01"}]
    assert _manifest_work_items(doc) == []
    assert _manifest_edges(doc) == []


def test_registered_selector_excludes_anchor_aggregates_seeds_and_uses_lexical_ties(tmp_path):
    evidence = []
    values = {"anchor": (0.05, 0.06), "zeta": (0.2, 0.2), "alpha": (0.2, 0.2)}
    for config, seed_values in values.items():
        for seed, score in zip((1001, 1002), seed_values):
            receipt = tmp_path / f"{config}-{seed}.json"
            receipt.write_text(json.dumps({"status": "SUCCEEDED", "formal_score_eligible": True,
                "hidden_test_opened": False, "validation_metrics": {"mae": score}}), encoding="utf-8")
            evidence.append({"work_id": f"{config}-{seed}", "configuration_id": config,
                "seed": seed, "receipt_path": str(receipt)})
    result = execute_registered_analysis({"analysis_operation": "select", "metric": "mae",
        "direction": "minimize", "anchor_configuration_id": "anchor", "exclude_anchor": True,
        "evidence": evidence})
    assert result["selected_configuration_id"] == "alpha"
    assert result["selected_configuration_ids_tied"] == ["alpha", "zeta"]
    assert result["seed_values"] == {"1001": 0.2, "1002": 0.2}


def test_live_verifier_rejects_inspect_only_and_incomplete_pairwise_work():
    episode = {"kind": "episode", "stream": "EVAL_UPDATE", "mutation": "C2",
        "action_bindings": [
            {"kind": "infer", "action_name": "infer", "work_id": f"infer-{i}"} for i in range(4)] + [
            {"kind": "analysis", "action_name": name, "work_id": name} for name in ("select", "compare")],
        "task_id": "D01"}
    inspect = [{"actions": [{"action_work_id": "inspect", "kind": "analysis", "state": "completed",
        "result": {"operation": "inspect"}}]}]
    result = verify_episode_submission(episode, inspect, {"selected_configuration_id": "candidate"})
    assert result["valid"] is False
    assert result["reason"] in {"selected_configuration_not_independently_recomputed",
        "required_pairwise_scientific_operations_incomplete"}


def test_full_no_gate_preserves_invalid_gate_result_as_advisory():
    episode = {"kind": "episode", "stream": "EVAL_UPDATE", "arm": "FULL_NO_GATE",
        "action_bindings": [], "task_id": "D01"}
    assert verify_episode_submission(episode, [], {})["valid"] is False


def test_legacy_ledger_rows_are_left_intact(tmp_path):
    path = tmp_path / "study.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE original_rows (id TEXT PRIMARY KEY, note TEXT)")
        db.execute("INSERT INTO original_rows VALUES('old-1','preserve')")
    StudyLedger(path, "ledger-test").register(manifest([item("fit-1")]))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT note FROM original_rows WHERE id='old-1'").fetchone()[0] == "preserve"


def test_fit_retry_after_proven_terminal_failure_uses_new_physical_attempt(monkeypatch, tmp_path):
    """Infrastructure fixture: no fitter or optimizer is launched."""
    from research.experiments import gpu_supervisor

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    output = campaign / "runs" / "fit-1"
    output.mkdir(parents=True)
    config_path = campaign / "fit.json"
    config = {"work_id": "fit-1", "phase": "pilot", "configuration_id": "cfg-1",
              "seed": 7, "optimizer_updates": 80, "formal_score_eligible": False}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path = campaign / "study_execution_manifest.json"
    document = manifest([item("fit-1", phase="pilot", configuration_path=str(config_path),
                              output_directory=str(output), fit_timeout_seconds=20)])
    document["commands"] = {"source_root": str(tmp_path), "fitter_python": "python"}
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    ledger_path = campaign / "study.sqlite"
    with sqlite3.connect(ledger_path) as db:
        db.execute("CREATE TABLE forest_compute_work(work_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO forest_compute_work VALUES('fit-1')")

    class FakeSupervisor:
        states = {"fit-1": "failed"}
        submitted = []
        directory = campaign / ".forest-compute"
        def __init__(self, _ledger, _ledger_id):
            pass
        def status(self, work_id):
            error = ("Command source/configuration changed before execution" if work_id == "fit-1"
                     else "fixture terminal failure")
            return {"work_id": work_id, "status": self.states[work_id], "error": error}
        def submit(self, command, workspace, *, work_id, **kwargs):
            self.submitted.append((work_id, list(command), str(workspace), kwargs))
            self.states[work_id] = "failed"
            with sqlite3.connect(ledger_path) as db:
                db.execute("INSERT INTO forest_compute_work VALUES(?)", (work_id,))
            return {"work_id": work_id, "status": "failed", "error": "fixture terminal failure"}
        def wait(self, work_id, timeout):
            return self.status(work_id)

    FakeSupervisor.submitted = []
    preflight = FakeSupervisor.directory / "fit-1" / "processes" / "fit-1"
    preflight.mkdir(parents=True)
    (preflight / "completion.json").write_text(json.dumps({"status": "failed", "exit_code": 1}), encoding="utf-8")
    (preflight / "stderr.log").write_text(
        "RuntimeError: Command source/configuration changed before execution: source.py", encoding="utf-8")
    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", FakeSupervisor)
    runtime = StudyRuntime(campaign=campaign, ledger=ledger_path, ledger_id="ledger-test", manifest=manifest_path)
    fit_item = {"work_id": "fit-1", "payload": document["execution_state"]["task_queue"][0]}
    first = runtime.execute_fit(fit_item, gpu_devices=["0"])
    assert first["supervisor_work_id"] == "fit-1--attempt-01"
    assert first["attempt_number"] == 1
    second = runtime.execute_fit(fit_item, gpu_devices=["0"])
    assert second["supervisor_work_id"] == "fit-1--attempt-01"
    assert second["attempt_number"] == 1
    assert len(FakeSupervisor.submitted) == 1
    first_id = FakeSupervisor.submitted[0][0]
    assert first_id == "fit-1--attempt-01"
    first_command = FakeSupervisor.submitted[0][1]
    assert first_command[first_command.index("--work-id") + 1] == "fit-1"
    with runtime.ledger.connect() as db:
        attempts = db.execute("SELECT attempt_number,supervisor_work_id,state FROM forest_study_fit_attempts ORDER BY attempt_number").fetchall()
        assert [tuple(row) for row in attempts] == [
            (0, "fit-1", "failed"), (1, "fit-1--attempt-01", "failed")]


def test_episode_candidate_resolves_latest_attempt_receipt(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    base = campaign / "runs" / "fit-1"
    attempted = base / "attempts" / "fit-1--attempt-01"
    attempted.mkdir(parents=True)
    config = {"work_id": "fit-1", "task_id": "D01", "family": "darcy",
        "configuration_id": "cfg-1", "seed": 3, "output_reference": str(base),
        "formal_score_eligible": True}
    config_path = campaign / "fit-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (attempted / "effective_config.json").write_text(json.dumps(config), encoding="utf-8")
    receipt = {"status": "SUCCEEDED", "work_id": "fit-1", "hidden_test_opened": False,
        "formal_score_eligible": True, "effective_config": config,
        "validation_metrics": {"relative_l2": 0.2}, "validation_prediction_file": "pred.npz"}
    (attempted / "execution_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (attempted / "pred.npz").write_bytes(b"fixture artifact")
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    with runtime.ledger.connect() as db:
        db.execute("INSERT INTO forest_study_fit_attempts VALUES(?,?,?,?,?,?,?,?,?)",
            ("campaign-test", "fit-1", 1, "fit-1--attempt-01", str(attempted),
             "completed", None, 1.0, 1.0))
    registered_config = {**config, "configuration_path": str(config_path)}
    rows = runtime._episode_candidate_rows({"task_id": "D01", "candidate_configs": [registered_config]}, [])
    assert len(rows) == 1 and rows[0]["state"] == "completed"
    assert rows[0]["receipt_path"] == attempted / "execution_receipt.json"
    config_path.write_text(json.dumps({**config, "training_data_revision": "changed"}), encoding="utf-8")
    refreshed = runtime._episode_candidate_rows({"task_id": "D01", "candidate_configs": [registered_config]}, [])
    assert refreshed[0]["state"] == "invalid_receipt"


def test_replay_budget_counts_physical_provider_attempts_and_actual_usage(tmp_path):
    from types import SimpleNamespace
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=path)
    external_events = []
    client = SimpleNamespace(request_guard=lambda event: external_events.append(dict(event)) or "external-token")
    guarded = runtime._replay_request_client({"work_id": "replay-budget"}, client)
    for index in range(6):
        reservation = guarded.request_guard({"phase": "before", "input_bytes": 100,
            "max_output_tokens": 1000, "attempt": 1, "api": "codex_cli", "model": "fixture"})
        guarded.request_guard({"phase": "after", "reservation": reservation,
            "usage": {"input_tokens": 100, "output_tokens": 500}, "request_id": f"request-{index}"})
    assert runtime._replay_token_budget("replay-budget") == {
        "physical_calls": 6, "input_tokens": 600, "output_tokens": 3000,
        "usage_unknown": False, "limits": None, "within_budget": True}
    assert runtime._replay_output_allowance("replay-budget") is None
    reservation = guarded.request_guard({"phase": "before", "input_bytes": 100,
        "max_output_tokens": 1000, "attempt": 1, "api": "codex_cli", "model": "fixture"})
    assert reservation["forest_study_request_id"]
    assert [event["phase"] for event in external_events].count("before") == 7


def test_preflight_retry_requires_failed_completion_without_child_identity(tmp_path):
    class Supervisor:
        directory = tmp_path / ".forest-compute"
    work_id = "fit-technical"
    folder = Supervisor.directory / work_id / "processes" / work_id
    folder.mkdir(parents=True)
    (folder / "completion.json").write_text(json.dumps({"status": "failed", "exit_code": 1}), encoding="utf-8")
    (folder / "stderr.log").write_text(
        "RuntimeError: Command source/configuration changed before execution: source.py", encoding="utf-8")
    status = {"status": "failed", "error": "Process exited without a bound completion receipt"}
    assert _known_source_preflight_failure(Supervisor(), work_id) is True
    (folder / "identity.json").write_text('{"pid": 123}', encoding="utf-8")
    assert _known_source_preflight_failure(Supervisor(), work_id) is False


def test_snapshot_stage_reports_failed_family_and_preserves_valid_branch(monkeypatch, tmp_path):
    from research.experiments import gpu_supervisor
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    configs = []
    for work_id, family in (("snap-ok", "darcy"), ("snap-fail", "etth1")):
        output = campaign / "runs" / work_id
        output.mkdir(parents=True)
        config = {"work_id": work_id, "family": family, "formal_score_eligible": True}
        config_path = campaign / f"{work_id}.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        configs.append({**config, "configuration_path": str(config_path), "output_directory": str(output)})
        if work_id == "snap-ok":
            (output / "effective_config.json").write_text(json.dumps(config), encoding="utf-8")
            (output / "execution_receipt.json").write_text(json.dumps({
                "status": "SUCCEEDED", "effective_config": config,
                "hidden_test_opened": False, "formal_score_eligible": True}), encoding="utf-8")
    plan = campaign / "snapshot.json"
    plan.write_text(json.dumps({"configs": configs}), encoding="utf-8")
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([{"work_id": "snap-stage", "kind": "snapshot_stage",
        "snapshot_plan_path": str(plan)}])), encoding="utf-8")
    ledger_path = campaign / "study.sqlite"
    with sqlite3.connect(ledger_path) as db:
        db.execute("CREATE TABLE forest_compute_work(work_id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO forest_compute_work VALUES(?)", [("snap-ok",), ("snap-fail",)])

    class FakeSupervisor:
        def __init__(self, _ledger, _ledger_id): pass
        def status(self, work_id):
            return {"work_id": work_id, "status": "completed" if work_id == "snap-ok" else "failed",
                    "error": None if work_id == "snap-ok" else "GPU process failed"}

    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", FakeSupervisor)
    runtime = StudyRuntime(campaign=campaign, ledger=ledger_path, ledger_id="ledger-test", manifest=manifest_path)
    result = runtime.execute_snapshot_stage({"payload": {"snapshot_plan_path": str(plan)}})
    assert result["stage_state"] == "failed"
    assert result["valid_work_ids"] == ["snap-ok"]
    assert result["hard_failures_by_family"]["etth1"][0]["work_id"] == "snap-fail"


def test_numpy_metric_implementation_returns_actual_multiclass_scores():
    import numpy as np
    logits = np.eye(8, dtype=float) * 5
    metrics = numpy_score_predictions("bloodmnist", logits, np.arange(8), {})
    assert metrics["accuracy"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["nll"] > 0


def test_numpy_darcy_metric_keeps_the_two_spatial_gradient_axes_separate():
    import numpy as np
    target = np.arange(2 * 1 * 4 * 5, dtype=np.float32).reshape(2, 1, 4, 5)
    metrics = numpy_score_predictions("darcy", target.copy(), target, {"target_mean": 0.0, "target_std": 1.0})
    assert metrics == {"relative_l2": 0.0, "relative_gradient_l2": 0.0}


def test_scripted_client_infrastructure_dispatch_runs_manifest_bound_analysis(tmp_path):
    """Infrastructure test only; a scripted client is not scientific model evidence."""
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    left = campaign / "left.json"
    right = campaign / "right.json"
    left.write_text('{"status":"SUCCEEDED","work_id":"left","formal_score_eligible":true,"hidden_test_opened":false,"configuration_id":"left","validation_metrics":{"mae":2.0}}\n', encoding="utf-8")
    right.write_text('{"status":"SUCCEEDED","work_id":"right","formal_score_eligible":true,"hidden_test_opened":false,"configuration_id":"right","validation_metrics":{"mae":1.0}}\n', encoding="utf-8")
    analysis = {"work_id": "analysis-child", "kind": "analysis", "analysis_operation": "select",
        "metric": "mae", "direction": "minimize", "require_formal": True,
        "evidence": [{"work_id": "left", "receipt_path": str(left)}, {"work_id": "right", "receipt_path": str(right)}]}
    episode = {"work_id": "episode-1", "phase": "INFRA_TEST", "kind": "episode", "stream": "INFRA_TEST", "arm": "FULL",
        "messages": [{"role": "user", "content": "Make a decision from the supplied evidence."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"]},
        "allowed_decisions": ["select"], "action_bindings": [{**analysis, "action_name": "select"}]}
    document = manifest([episode])
    manifest_path = campaign / "study_execution_manifest.json"
    import json
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                           ledger_id="ledger-test", manifest=manifest_path)

    class ScriptedClient:
        provider = {"id": "fixture-provider", "model": "fixture"}
        config = {}
        def __init__(self): self.calls = [0]
        def complete(self, *_args, **_kwargs):
            self.calls[0] += 1
            text = ('{"mode":"action","actions":[{"kind":"select","action_work_id":"analysis-child"}],"submission":{},"rationale":"Run the registered selector."}'
                if self.calls[0] == 1 else
                '{"mode":"submit","actions":[],"submission":{"selected_configuration_id":"right","claim":"The receipt-backed selector chose right."},"rationale":"The action result supports this submission."}')
            return {"text": text, "usage": {"input_tokens": 10, "output_tokens": 4, "cost": None}}

    client = ScriptedClient()
    first = runtime.run_one(owner="fixture", client=client)
    assert first["action_result"]["state"] == "completed"
    assert runtime.ledger.counts("campaign-test")["retryable"] == 1
    result = runtime.run_one(owner="fixture", client=client)
    assert result["action_result"]["valid"] is True
    assert result["action_result"]["selected_work_ids"] == ["right"]
    assert runtime.ledger.counts("campaign-test")["completed"] == 2
    assert client.calls == [2]
    with runtime.ledger.connect() as db:
        calls = db.execute("SELECT status,started_at,finished_at,elapsed_seconds FROM forest_study_model_calls").fetchall()
        assert len(calls) == 2 and all(row[0] == "completed" and row[1] and row[2] and row[3] is not None for row in calls)


def test_repeated_cached_action_returns_semantic_no_progress_feedback(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    left = campaign / "left.json"
    right = campaign / "right.json"
    left.write_text('{"status":"SUCCEEDED","work_id":"left","formal_score_eligible":true,"hidden_test_opened":false,"configuration_id":"left","validation_metrics":{"mae":2.0}}\n', encoding="utf-8")
    right.write_text('{"status":"SUCCEEDED","work_id":"right","formal_score_eligible":true,"hidden_test_opened":false,"configuration_id":"right","validation_metrics":{"mae":1.0}}\n', encoding="utf-8")
    analysis = {"work_id": "analysis-child", "kind": "analysis", "analysis_operation": "select",
        "metric": "mae", "direction": "minimize", "require_formal": True,
        "evidence": [{"work_id": "left", "receipt_path": str(left)}, {"work_id": "right", "receipt_path": str(right)}]}
    episode = {"work_id": "episode-repeat", "phase": "INFRA_TEST", "kind": "episode", "stream": "INFRA_TEST", "arm": "FULL",
        "messages": [{"role": "user", "content": "Decide from the evidence."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"]},
        "allowed_decisions": ["select"], "action_bindings": [{**analysis, "action_name": "select"}]}
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(manifest([episode])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
                           ledger_id="ledger-test", manifest=manifest_path)

    class RepeatingClient:
        provider = {"id": "fixture-provider", "model": "fixture"}
        config = {"reasoning_effort": "low"}
        def __init__(self): self.calls = []
        def complete(self, *_args, **_kwargs):
            self.calls.append(dict(self.config))
            return {"text": '{"mode":"action","actions":[{"kind":"select","action_work_id":"analysis-child"}],"submission":{},"rationale":"Select from the registered evidence."}',
                    "usage": {"input_tokens": 10, "output_tokens": 4}}

    client = RepeatingClient()
    first = runtime.run_one(owner="fixture", client=client)
    assert first["action_result"]["state"] == "completed"
    second = runtime.run_one(owner="fixture", client=client)
    assert second["action_result"]["state"] == "retryable"
    assert second["action_result"]["no_progress_observation"]["same_source_bound_results"] is True
    assert "produced no new evidence" in second["action_result"]["response_validation_feedback"]
    third = runtime.run_one(owner="fixture", client=client)
    assert third["state"] == "paused_no_progress"
    assert runtime.ledger.counts("campaign-test")["paused_no_progress"] == 1
    assert len(client.calls) == 3
    schema = episode["decision_schema"]
    assert all(call["_forest_action_schema"] == schema for call in client.calls)
    assert client.config == {"reasoning_effort": "low"}
    with runtime.ledger.connect() as db:
        request = json.loads(db.execute("SELECT request_json FROM forest_study_model_calls ORDER BY started_at LIMIT 1").fetchone()[0])
    assert request["decision_schema"] == schema
    assert request["provider_snapshot"]["id"] == "fixture-provider"
    assert request["reasoning_effort"] == "low"


def _context_read_episode_fixture(tmp_path, offsets):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    work_id = "episode-context-repeat"
    prepared = {"work_id": work_id, "kind": "episode", "arm": "FULL",
        "resource_policy": {"actual_gpu_seconds": None, "active_wall_seconds": None},
        "messages": [{"role": "user", "content": "Read the exact registered source page."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
                "actions": {"type": "array", "items": {"type": "object",
                    "required": ["kind", "action_work_id"], "properties": {
                        "kind": {"type": "string", "enum": ["inspect"]},
                        "action_work_id": {"type": "string", "enum": ["inspect-1"]}}}},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["inspect"], "action_bindings": [{"kind": "analysis",
            "action_name": "inspect", "work_id": "inspect-1", "analysis_operation": "inspect"}]}
    payload = runtime._attach_public_context(prepared, prepared)
    episode = item(work_id, kind="episode", payload=payload)
    manifest_path.write_text(json.dumps(manifest([episode], version=2)), encoding="utf-8")
    runtime.reload_manifest()
    read_binding = next(row for row in payload["action_bindings"]
        if row.get("action_name") == "read_context_segment")
    segment_id = payload["context_memory"]["segment_id"]

    class PageClient:
        provider = {"id": "fixture-provider", "model": "fixture", "kind": "fixture"}
        config = {"reasoning_effort": "low"}
        def __init__(self):
            self.offsets = list(offsets)
            self.calls = []
        def complete(self, *_args, **_kwargs):
            offset = self.offsets[len(self.calls)]
            self.calls.append(offset)
            return {"text": json.dumps({"mode": "action", "actions": [{
                "kind": "read_context_segment", "action_work_id": read_binding["work_id"],
                "context_segment_id": segment_id, "context_pointer": "/messages/0/content",
                "context_offset": offset, "context_limit": 16}], "submission": {},
                "rationale": "Nonformal fixture page request."}),
                "usage": {"input_tokens": 9, "output_tokens": 5}}
    return runtime, manifest_path, episode, PageClient()


def test_repeated_context_page_pauses_after_one_diagnostic_across_runtime_reload(tmp_path):
    runtime, manifest_path, episode, client = _context_read_episode_fixture(tmp_path, [0, 0, 0])
    first = runtime.run_one(owner="fixture", work_id=episode["work_id"], client=client)
    assert first["state"] == "retryable"
    first_page = first["action_result"]["actions"][0]["result"]
    assert first_page["offset"] == 0 and first_page["next_offset"] > 0

    second = runtime.run_one(owner="fixture", work_id=episode["work_id"], client=client)
    assert second["state"] == "retryable"
    observation = second["action_result"]["no_progress_observation"]
    assert observation["new_evidence_added"] is False
    assert observation["diagnostic_previously_delivered"] is False
    assert second["action_result"]["actions"][0]["result"]["segment_id"] == first_page["segment_id"]
    assert "already delivered" in second["action_result"]["response_validation_feedback"]

    resumed = StudyRuntime(campaign=runtime.campaign, ledger=runtime.ledger_path,
        ledger_id="ledger-test", manifest=manifest_path)
    third = resumed.run_one(owner="fixture-resumed", work_id=episode["work_id"], client=client)
    assert third["state"] == "paused_no_progress"
    assert third["action_result"]["no_progress_observation"]["diagnostic_previously_delivered"] is True
    assert client.calls == [0, 0, 0]


def test_context_page_continuation_and_new_evidence_frontier_are_not_repeats(tmp_path):
    from research.experiments.study_runtime import (_context_page_no_progress,
        _episode_source_frontier)

    frontier = {"messages": [{"role": "user", "content": "question"}],
        "completed_source_actions": {}}
    def read(offset, end, segment="segment-v1", requested_limit=None):
        return {"kind": "analysis", "state": "completed", "result": {
            "operation": "read_context_segment", "segment_id": segment, "pointer": "/source",
            "offset": offset, "next_offset": end, "returned_characters": end - offset,
            "requested_limit": requested_limit if requested_limit is not None else end - offset,
            "content": "x" * (end - offset), "delivery_status": "delivered"}}
    prior = [{"source_frontier": frontier, "actions": [read(0, 16)]}]
    continuation = _context_page_no_progress([read(16, 32)], prior, frontier)
    assert continuation is None
    reread = _context_page_no_progress([read(0, 16)], prior, frontier)
    assert reread["state"] == "retryable"
    changed_frontier = {**frontier, "completed_source_actions": {"new-score": {"value": 2.0}}}
    reread_after_evidence = _context_page_no_progress([read(0, 16)], prior, changed_frontier)
    assert reread_after_evidence is None
    large_request_prior = [{"source_frontier": frontier,
        "actions": [read(0, 16, requested_limit=10_000_000)]}]
    first_repeat = _context_page_no_progress(
        [read(0, 12, requested_limit=10_000_000)], large_request_prior, frontier)
    assert first_repeat["state"] == "retryable"
    diagnosed_turn = {"source_frontier": frontier,
        "actions": [read(0, 12, requested_limit=10_000_000)],
        "no_progress_observation": first_repeat["no_progress_observation"]}
    second_repeat = _context_page_no_progress(
        [read(0, 10, requested_limit=10_000_000)], [*large_request_prior, diagnosed_turn], frontier)
    assert second_repeat["state"] == "paused_no_progress"
    context_note = {**frontier, "messages": [{"role": "system",
        "content": "Previously retrieved exact source pages (references only): [{\"segment_id\":\"segment-v1\"}]"},
        *frontier["messages"]]}
    assert _episode_source_frontier(context_note) == _episode_source_frontier(frontier)


def test_action_batch_is_fully_bound_before_first_operation(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    output = campaign / "inspect.json"
    binding = {"kind": "analysis", "action_name": "inspect", "work_id": "inspect-1",
        "analysis_operation": "inspect", "output_path": str(output), "evidence": []}
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    episode = {"payload": {"allowed_decisions": ["inspect"], "action_bindings": [binding]}}
    calls = []
    monkeypatch.setattr(runtime, "execute_decision_action", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="exactly one registered binding"):
        runtime.execute_decision_actions(episode, [
            {"kind": "inspect", "action_work_id": "inspect-1"},
            {"kind": "inspect", "action_work_id": "unknown"}])
    assert calls == []
    assert not output.exists()


def test_paused_no_progress_requires_explicit_resume_and_preserves_result(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "ledger-test")
    ledger.register(manifest([{"work_id": "paused-episode", "kind": "episode", "phase": "INFRA_TEST"}]))
    prior_result = {"state": "paused_no_progress", "no_progress_observation": {"reason": "same evidence"}}
    ledger.set_state("campaign-test", "paused-episode", "paused_no_progress", result=prior_result)
    with pytest.raises(ValueError, match="operator identity"):
        ledger.resume_no_progress("campaign-test", "paused-episode", operator=" ")
    ledger.resume_no_progress("campaign-test", "paused-episode", operator="reviewer")
    with ledger.connect() as db:
        row = db.execute("SELECT state,result_json FROM forest_study_work WHERE work_id='paused-episode'").fetchone()
    assert row["state"] == "retryable"
    assert json.loads(row["result_json"]) == prior_result


def test_codex_transport_schema_is_strict_without_changing_runtime_schema():
    schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
        "properties": {"mode": {"enum": ["action", "submit"]},
            "actions": {"type": "array", "items": {"type": "object",
                "required": ["kind", "action_work_id"], "properties": {
                    "kind": {"enum": ["inspect", "score"]},
                    "action_work_id": {"enum": ["a", "b"]},
                    "configuration_id": {"type": ["string", "null"]},
                    "seed": {"type": ["integer", "null"]}}}},
            "submission": {"type": "object", "properties": {
                "selected_configuration_id": {"type": ["string", "null"]},
                "claim": {"type": "string"}}},
            "rationale": {"type": "string"}}}
    strict = _codex_strict_schema(schema)
    assert schema["properties"]["mode"] == {"enum": ["action", "submit"]}
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == set(schema["properties"])
    action = strict["properties"]["actions"]["items"]
    assert action["additionalProperties"] is False
    assert set(action["required"]) == set(action["properties"])
    assert action["properties"]["kind"]["type"] == "string"
    assert action["properties"]["configuration_id"]["type"] == ["string", "null"]
    submission = strict["properties"]["submission"]
    assert submission["additionalProperties"] is False
    assert set(submission["required"]) == set(submission["properties"])
    assert submission["properties"]["claim"]["type"] == ["string", "null"]
    from research.agents.codex_transport import request_payload
    payload = request_payload([{"role": "user", "content": "Return an action."}], True, strict)
    assert payload["response_schema"] == strict
    decision = {"mode": "action", "actions": [{"kind": "inspect", "action_work_id": "a",
        "configuration_id": None, "seed": None}], "submission": {
            "selected_configuration_id": None, "claim": None}, "rationale": "Use registered evidence."}
    restored = _restore_strict_optional_nulls(decision, schema)
    assert "claim" not in restored["submission"]
    _validate_episode_schema(restored, schema)


def test_transport_trace_path_is_unique_and_codex_only(tmp_path):
    from uuid import uuid4
    first, second = str(uuid4()), str(uuid4())
    provider = {"kind": "codex_cli"}
    path1 = _transport_trace_reference(tmp_path, first, provider)
    path2 = _transport_trace_reference(tmp_path, second, provider)
    assert path1 and path1 != path2
    assert str(tmp_path.resolve() / "runs" / "transport_traces") in path1
    assert _transport_trace_reference(tmp_path, first, {"kind": "openai_compatible"}) is None


def test_codex_call_records_trace_reference_before_send(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
        "properties": {"mode": {"type": "string"}, "actions": {"type": "array"},
            "submission": {"type": "object"}, "rationale": {"type": "string"}}}
    payload = {"arm": "FULL", "resource_policy": {"active_wall_seconds": 60,
        "actual_gpu_seconds": 10}, "messages": [{"role": "user", "content": "Nonformal transport fixture."}],
        "decision_schema": schema, "allowed_decisions": [], "action_bindings": []}
    item = {"work_id": "trace-fixture", "kind": "episode", "payload": payload}

    class CodexFixture:
        provider = {"id": "codex-fixture", "kind": "codex_cli", "model": "fixture", "allow_paid": False}
        config = {"reasoning_effort": "low"}
        seen_trace_paths = []
        def build_request(self, messages, **kwargs):
            return "codex_cli", {"n_messages": len(messages), "schema": self.config.get("_forest_action_schema")}
        def complete(self, _messages, **_kwargs):
            self.seen_trace_paths.append(self.config.get("_forest_transport_trace_path"))
            return {"text": json.dumps({"mode": "submit", "actions": [],
                "submission": {}, "rationale": "nonformal fixture"}),
                "usage": {"input_tokens": 5, "output_tokens": 4}}

    monkeypatch.setattr("research.experiments.study_runtime.verify_episode_submission",
        lambda *_args, **_kwargs: {"state": "completed", "valid": True})
    client = CodexFixture()
    result = runtime.execute_model_episode(item, client=client)
    assert result["action_result"]["valid"] is True
    with runtime.ledger.connect() as db:
        saved = json.loads(db.execute("SELECT request_json FROM forest_study_model_calls").fetchone()[0])
    assert saved["transport_trace_path"] == client.seen_trace_paths[0]
    assert saved["transport_trace_path"].endswith(f"{saved['request_id']}.json")
    assert saved["decision_schema"] == schema
    assert saved["transport_decision_schema"]["additionalProperties"] is False
    assert saved["original_messages"] == saved["messages"]


def test_large_model_context_interning_preserves_exact_json_and_tail_text():
    from research.experiments.study_runtime import _intern_delivered_messages
    from research.experiments.study_context import restore_json
    source = {"observations": [{"ids": list(range(1200)), "record": {"metric": 0.25}}
                               for _ in range(8)]}
    text = json.dumps(source, separators=(",", ":"), ensure_ascii=False) + "\nSchema reminder: return JSON."
    actual, stats = _intern_delivered_messages([{"role": "user", "content": text}])
    assert stats and stats[0]["encoding"] == "interned"
    rewritten = actual[0]["content"]
    header = "COMPLETE LOSSLESS JSON CONTEXT DOCUMENT follows. Read its instructions and expand every reference that matters for your decision. All original keys, list order, values and source positions are preserved.\n"
    assert rewritten.startswith(header)
    document_text, tail = rewritten[len(header):].split("\nSchema reminder: return JSON.", 1)
    assert tail == ""
    document = json.loads(document_text)
    assert restore_json(document) == source
    assert stats[0]["output_characters"] < stats[0]["input_characters"]


def test_downstream_executor_error_preserves_actual_model_response(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
        "properties": {"mode": {"type": "string"}, "actions": {"type": "array", "items": {
            "type": "object", "required": ["kind", "action_work_id"], "properties": {
                "kind": {"type": "string"}, "action_work_id": {"type": "string"}}}},
            "submission": {"type": "object"}, "rationale": {"type": "string"}}}
    binding = {"work_id": "inspect-fixture", "action_name": "inspect", "kind": "analysis",
        "analysis_operation": "inspect", "evidence": []}
    payload = {"arm": "FULL", "resource_policy": {"active_wall_seconds": 60,
        "actual_gpu_seconds": 10}, "messages": [{"role": "user", "content": "Nonformal fixture."}],
        "decision_schema": schema, "allowed_decisions": ["inspect"], "action_bindings": [binding]}
    item = {"work_id": "post-response-error", "kind": "episode", "payload": payload}

    class ScriptedClient:
        provider = {"id": "fixture", "kind": "scripted", "model": "fixture"}
        config = {}
        def complete(self, *_args, **_kwargs):
            return {"text": json.dumps({"mode": "action", "actions": [{"kind": "inspect",
                "action_work_id": "inspect-fixture"}], "submission": {}, "rationale": "nonformal fixture"}),
                "usage": {"input_tokens": 7, "output_tokens": 6}}

    def fail_executor(*_args, **_kwargs):
        raise RuntimeError("nonformal executor fault")
    monkeypatch.setattr(runtime, "execute_decision_actions", fail_executor)
    with pytest.raises(RuntimeError, match="nonformal executor fault"):
        runtime.execute_model_episode(item, client=ScriptedClient())
    with runtime.ledger.connect() as db:
        row = db.execute("SELECT response_json,usage_json,status FROM forest_study_model_calls").fetchone()
        event = db.execute("SELECT payload_json FROM forest_study_events WHERE event_type='model_call_post_response_error'").fetchone()
    assert json.loads(row[0])["text"] == json.dumps({"mode": "action",
        "actions": [{"kind": "inspect", "action_work_id": "inspect-fixture"}],
        "submission": {}, "rationale": "nonformal fixture"})
    assert json.loads(row[1])["input_tokens"] == 7 and row[2] == "completed"
    assert json.loads(event[0])["error"]["message"] == "nonformal executor fault"


def test_invalid_actual_response_is_saved_and_gets_one_evidence_bound_repair_turn(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    analysis = {"work_id": "inspect-child", "kind": "analysis", "action_name": "inspect",
        "analysis_operation": "inspect", "evidence": [{"work_id": "future-fit",
            "configuration_id": "candidate", "seed": 7, "state": "not_run"}],
        "output_path": str(campaign / "inspect.json")}
    episode = {"work_id": "repair-episode", "kind": "episode", "phase": "INFRA_TEST",
        "arm": "FULL", "messages": [{"role": "user", "content": "Inspect the registered source record."}],
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"]},
        "allowed_decisions": ["inspect"], "action_bindings": [analysis],
        "resource_policy": {"active_wall_seconds": 300, "actual_gpu_seconds": 1}}
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([episode])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)

    class ActualResponseFixture:
        provider = {"id": "fixture", "model": "fixture"}
        config = {}
        def __init__(self): self.requests = []
        def complete(self, messages, **_kwargs):
            self.requests.append(messages)
            text = ('{"mode":"submit","actions":[{"kind":"inspect"}],"submission":{},"rationale":"invalid"}'
                    if len(self.requests) == 1 else
                    '{"mode":"action","actions":[{"kind":"inspect","action_work_id":"inspect-child"}],"submission":{},"rationale":"Run the registered inspection."}')
            return {"text": text, "model": "fixture", "response_id": f"response-{len(self.requests)}",
                    "usage": {"input_tokens": 11, "output_tokens": 7, "cost": None}}

    client = ActualResponseFixture()
    first = runtime.run_one(owner="response-repair", client=client)
    assert first["action_result"]["state"] == "retryable"
    assert first["action_result"]["raw_response_persisted"] is True
    with runtime.ledger.connect() as db:
        saved = db.execute("SELECT response_json,usage_json,status FROM forest_study_model_calls").fetchone()
        work = db.execute("SELECT decision_json,result_json,state FROM forest_study_work WHERE work_id='repair-episode'").fetchone()
    assert json.loads(saved[0])["text"].startswith('{"mode":"submit"')
    assert json.loads(saved[1])["input_tokens"] == 11 and saved[2] == "completed"
    assert work[0] is None and work[2] == "retryable"
    feedback = json.loads(work[1])["response_validation_feedback"]
    assert feedback["raw_response_persisted"] is True
    second = runtime.run_one(owner="response-repair", client=client)
    assert second["action_result"]["actions"][0]["state"] == "completed"
    delivered = "\n".join(str(message.get("content", "")) for message in client.requests[1])
    assert "previous actual response was saved" in delivered
    assert "A submit-mode response cannot add unexecuted operations" in delivered


@pytest.mark.parametrize("arm", ["FLAT", "DAG_ID"])
def test_source_precondition_replan_survives_restart_and_uses_fresh_model_decision(tmp_path, arm):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    anchor_receipt, candidate_receipt = campaign / "anchor.json", campaign / "candidate.json"
    for path, work_id, configuration_id, value in (
        (anchor_receipt, "anchor-evidence", "anchor", 1.0),
        (candidate_receipt, "candidate-evidence", "candidate", 0.8),
    ):
        path.write_text(json.dumps({"status": "SUCCEEDED", "work_id": work_id,
            "configuration_id": configuration_id, "seed": 11,
            "validation_metrics": {"mae": value}, "hidden_test_opened": False,
            "formal_score_eligible": False, "test_fixture": "nonformal"}), encoding="utf-8")
    compare = {"work_id": "compare-before-select", "kind": "analysis", "action_name": "compare",
        "analysis_operation": "compare", "selection_work_id": "selection-not-yet-run",
        "compared_configuration_ids": ["anchor", "candidate"], "evidence": [
            {"work_id": "anchor-evidence", "configuration_id": "anchor", "seed": 11,
             "receipt_path": str(anchor_receipt)},
            {"work_id": "candidate-evidence", "configuration_id": "candidate", "seed": 11,
             "receipt_path": str(candidate_receipt)}],
        "pairs": [{"left": "anchor-evidence", "right": "candidate-evidence"}]}
    inspect = {"work_id": "inspect-current-state", "kind": "analysis", "action_name": "inspect",
        "analysis_operation": "inspect", "evidence": [{"work_id": "candidate-evidence",
            "configuration_id": "candidate", "seed": 11, "state": "not_run", "metrics": {}}],
        "output_path": str(campaign / "inspect-current-state.json")}
    payload = {"arm": arm, "messages": [{"role": "user", "content": "Nonformal replan fixture."}],
        "resource_policy": {"active_wall_seconds": 300, "actual_gpu_seconds": 10},
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string"}, "actions": {"type": "array", "items": {
                "type": "object", "required": ["kind", "action_work_id"], "properties": {
                    "kind": {"type": "string"}, "action_work_id": {"type": "string"}}}},
                "submission": {"type": "object"}, "rationale": {"type": "string"}}},
        "allowed_decisions": ["compare", "inspect"], "action_bindings": [compare, inspect]}
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([{"work_id": "replan-episode", "kind": "episode",
        "payload": payload}])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    requests = []

    class ReplanningClient:
        provider = {"id": "fixture", "kind": "scripted", "model": "fixture"}
        config = {"reasoning_effort": "medium"}
        timeout = 10
        def complete(self, messages, **_kwargs):
            requests.append(messages)
            if len(requests) == 1:
                decision = {"mode": "action", "actions": [{"kind": "compare",
                    "action_work_id": "compare-before-select"}], "submission": {},
                    "rationale": "Nonformal fixture deliberately compares before source selection."}
            else:
                decision = {"mode": "action", "actions": [{"kind": "inspect",
                    "action_work_id": "inspect-current-state"}], "submission": {},
                    "rationale": "Nonformal fixture replans using the registered inspection."}
            return {"text": json.dumps(decision), "usage": {"input_tokens": 5, "output_tokens": 7},
                "model": "fixture", "status": "completed"}

    client = ReplanningClient()
    first = runtime.run_one(owner="replan-fixture", client=client)
    assert first["action_result"]["state"] == "retryable"
    assert first["action_result"]["request_new_decision"] is True
    nested = first["action_result"]["actions"][0]
    assert nested["reason"] == "source_selection_must_complete_before_comparison"
    assert nested["request_new_decision"] is True
    # Model rows created before the aggregate field was added have the flag
    # only on the child action. Recreate that legacy persisted shape.
    with runtime.ledger.connect() as db:
        saved = db.execute("SELECT result_json FROM forest_study_work WHERE work_id='replan-episode'").fetchone()[0]
        legacy_result = json.loads(saved)
        legacy_result["action_result"].pop("request_new_decision", None)
        db.execute("UPDATE forest_study_work SET result_json=? WHERE work_id='replan-episode'",
            (json.dumps(legacy_result),))
    # Reconstruct the runtime as a scheduler restart would, then verify it
    # chooses a fresh decision rather than replaying the blocked comparison.
    recovered = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    second = recovered.run_one(owner="replan-fixture-resumed", client=client)
    assert len(requests) == 2
    assert second["decision"]["actions"][0]["action_work_id"] == "inspect-current-state"
    assert second.get("resumed_saved_decision", False) is False
    delivered = "\n".join(str(message.get("content", "")) for message in requests[1])
    assert "source_selection_must_complete_before_comparison" in delivered
    with recovered.ledger.connect() as db:
        calls = db.execute("SELECT status FROM forest_study_model_calls WHERE work_id='replan-episode' ORDER BY recorded_at").fetchall()
        row = db.execute("SELECT decision_json,result_json,state FROM forest_study_work WHERE work_id='replan-episode'").fetchone()
        comparison = db.execute("SELECT state FROM forest_study_work WHERE work_id='compare-before-select'").fetchone()
    assert [call[0] for call in calls] == ["completed", "completed"]
    assert json.loads(row[0])["actions"][0]["action_work_id"] == "inspect-current-state"
    assert any(action["reason"] == "source_selection_must_complete_before_comparison"
        for action in json.loads(row[1])["action_history"][0]["actions"])
    assert comparison is None  # The blocked precondition never registered/executed a comparison.


def test_pending_gpu_action_still_reuses_saved_decision(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    fit = {"work_id": "pending-fit", "kind": "fit", "action_name": "train",
        "configuration_id": "candidate", "seed": 11, "configuration_path": str(campaign / "config.json"),
        "fit_timeout_seconds": 10, "output_directory": str(campaign / "fit-output"),
        "effective_config": {"work_id": "pending-fit", "configuration_id": "candidate", "seed": 11}}
    decision = {"mode": "action", "actions": [{"kind": "train", "action_work_id": "pending-fit",
        "configuration_id": "candidate", "seed": 11}], "submission": {}, "rationale": "Nonformal pending fit fixture."}
    payload = {"arm": "FULL", "messages": [{"role": "user", "content": "Nonformal pending fit."}],
        "resource_policy": {"active_wall_seconds": 300, "actual_gpu_seconds": 10},
        "decision_schema": {"type": "object", "required": ["mode", "actions", "submission", "rationale"]},
        "allowed_decisions": ["train"], "action_bindings": [fit]}
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([{"work_id": "pending-episode", "kind": "episode",
        "payload": payload}])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    runtime.ledger.register_action("campaign-test", "pending-fit", fit, parent_id="pending-episode")
    runtime.ledger.set_state("campaign-test", "pending-fit", "retryable")
    runtime.ledger.set_state("campaign-test", "pending-episode", "retryable", decision=decision,
        result={"decision": decision, "action_result": {"state": "retryable", "actions": [
            {"action_work_id": "pending-fit", "kind": "fit", "state": "retryable"}]},
            "action_history": []})
    calls = []
    class NoCallClient:
        provider = {"id": "fixture", "kind": "scripted", "model": "fixture"}
        config = {"reasoning_effort": "medium"}
        timeout = 10
        def complete(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Pending GPU reconciliation must reuse the saved model decision")
    result = runtime.run_one(owner="pending-gpu-fixture", work_id="pending-episode",
        client=NoCallClient(), defer_gpu=True)
    assert result["resumed_saved_decision"] is True
    assert result["action_result"]["actions"][0]["state"] == "retryable"
    assert calls == []


@pytest.mark.parametrize("arm,expected_view", [
    ("FULL", "typed_graph"), ("DAG_ID", "id_only_graph"), ("FLAT", "flat_artifact_index"),
])
def test_registered_episode_adapter_delivers_arm_specific_actual_receipt_context(tmp_path, arm, expected_view):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    configs = []
    for configuration_id, value in (("D01_c0", 0.2), ("D01_c1", 0.15), ("D01_c2", 0.12)):
        for seed in (1001, 1002):
            name = f"{configuration_id}-s{seed}"
            output = campaign / "runs" / name
            output.mkdir(parents=True)
            effective = {"work_id": name, "task_id": "D01", "family": "darcy",
                "configuration_id": configuration_id, "seed": seed, "output_directory": str(output),
                "phase": "formal", "formal_score_eligible": True,
                "dataset_version_id": "darcy-v1", "optimizer_updates": 100,
                "batch_size": 8, "train_sample_ids": list(range(1, 1001)),
                "validation_sample_ids": [1001, 1002],
                "optimizer": {"name": "AdamW", "lr": 0.001}}
            receipt = {"status": "SUCCEEDED", "work_id": name, "task_id": "D01", "configuration_id": configuration_id,
                "seed": seed, "formal_score_eligible": True, "hidden_test_opened": False,
                "validation_metrics": {"relative_l2": value}, "validation_prediction_file": "validation_predictions.npz",
                "effective_config": effective}
            (output / "effective_config.json").write_text(json.dumps(effective), encoding="utf-8")
            (output / "validation_predictions.npz").write_bytes(b"fixture path only; no action executed")
            (output / "execution_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            (output / "training.jsonl").write_text('{"update":25,"train_loss":0.1}\n', encoding="utf-8")
            configs.append(effective)
    document = manifest([])
    document["tasks"] = [{"task_id": "D01", "family_id": "darcy", "anchor_configuration_id": "D01_c0",
        "shared_snapshot_seeds": [1001, 1002],
        "C1_revision": {"revised_primary_metric": "relative_l2", "revised_direction": "minimize",
            "revised_side_constraints": []}}]
    document["execution_state"]["work_items"] = [{"work_id": "episode-1", "kind": "episode", "task_id": "D01",
        "arm": arm, "stream": "EVAL_UPDATE", "candidate_configs": configs,
        "factual_changes": {"evaluation_contract": {"revision_id": "C1",
            "revised_primary_metric": "relative_l2", "revised_direction": "minimize"}}}]
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    payload = runtime.build_episode_request({**document["execution_state"]["work_items"][0], "work_id": "episode-1"})
    user = payload["messages"][-1]["content"]
    request_context = json.loads(user)
    assert request_context["evidence_view"]["view"] == expected_view
    assert {x["configuration_id"] for x in request_context["evidence_view"]["observations"]} == {"D01_c0", "D01_c1", "D01_c2"}
    observations = request_context["evidence_view"]["observations"]
    assert all(row["status"] == "SUCCEEDED" and row["completed_updates"] == 100 for row in observations)
    assert all(len(row["effective_config"]["train_sample_ids"]) == 1000
               and row["effective_config"]["validation_sample_ids"] == [1001, 1002]
               for row in observations)
    assert all(row["receipt"]["hidden_test_opened"] is False for row in observations)
    if arm == "FLAT":
        assert request_context["evidence_view"]["raw_training_logs"]
        assert request_context["evidence_view"]["raw_training_logs"][0]["lines"] == ['{"update":25,"train_loss":0.1}']
    assert {binding["action_name"] for binding in payload["action_bindings"]} == {
        "score", "select", "compare", "read_context_segment"}
    scores = [binding for binding in payload["action_bindings"] if binding["action_name"] == "score"]
    comparisons = [binding for binding in payload["action_bindings"] if binding["action_name"] == "compare"]
    assert len(scores) == 6
    assert len(comparisons) == 2
    assert all(binding["selection_work_id"] for binding in comparisons)
    if arm == "DAG_ID":
        assert all(set(edge) == {"from", "to"} for edge in request_context["evidence_view"]["edges"])
    if arm == "FLAT":
        assert request_context["evidence_view"]["raw_training_logs"][0]["lines"] == ['{"update":25,"train_loss":0.1}']


def test_eval_c2_binds_inference_for_all_three_configs_and_two_seeds(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    configs = []
    for configuration_id, value in (("anchor", 0.25), ("candidate-a", 0.18), ("candidate-b", 0.14)):
        for seed in (1001, 1002):
            work_id = f"{configuration_id}-seed-{seed}"
            output = campaign / "runs" / work_id
            output.mkdir(parents=True)
            config = {"work_id": work_id, "task_id": "D01", "family": "darcy",
                "configuration_id": configuration_id, "seed": seed, "phase": "formal",
                "output_directory": str(output), "formal_score_eligible": True}
            receipt = {"status": "SUCCEEDED", "work_id": work_id, "task_id": "D01",
                "configuration_id": configuration_id, "seed": seed, "formal_score_eligible": True,
                "hidden_test_opened": False, "validation_metrics": {"relative_l2": value},
                "effective_config": config}
            (output / "effective_config.json").write_text(json.dumps(config), encoding="utf-8")
            (output / "execution_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            (output / "checkpoint.pt").write_bytes(b"opaque source checkpoint fixture")
            configs.append(config)
    data_root = campaign / "data" / "researcher"
    document = manifest([])
    document["tasks"] = [{"task_id": "D01", "family_id": "darcy", "anchor_configuration_id": "anchor",
        "shared_snapshot_seeds": [1001, 1002], "primary_metric": "relative_l2", "primary_direction": "minimize",
        "C1_revision": {"revised_primary_metric": "relative_gradient_l2", "revised_direction": "minimize",
            "revised_side_constraints": []}}]
    document["experiment_state"] = {"data_root": str(data_root)}
    item = {"work_id": "episode-c2", "kind": "episode", "task_id": "D01", "family": "darcy",
        "arm": "FULL", "stream": "EVAL_UPDATE", "candidate_configs": configs,
        "factual_changes": {"evaluation_contract": {"revised_primary_metric": "relative_gradient_l2",
            "revised_direction": "minimize", "revised_side_constraints": []},
            "validation_input_config": {"validation_noise_seed": 20261012, "validation_noise_std": 0.02}}}
    document["execution_state"]["work_items"] = [item]
    manifest_path = campaign / "study_execution_manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    request = runtime.build_episode_request(item)
    inference = [binding for binding in request["action_bindings"] if binding["kind"] == "infer"]
    comparisons = [binding for binding in request["action_bindings"] if binding["action_name"] == "compare"]
    selection = next(binding for binding in request["action_bindings"] if binding["action_name"] == "select")
    assert {(binding["configuration_id"], binding["seed"]) for binding in inference} == {
        (configuration_id, seed) for configuration_id in ("anchor", "candidate-a", "candidate-b")
        for seed in (1001, 1002)}
    assert len(comparisons) == 2
    assert all(binding["selection_work_id"] == selection["work_id"] for binding in comparisons)
    assert {row["configuration_id"] for row in selection["evidence"]} == {"anchor", "candidate-a", "candidate-b"}


def test_training_mutations_keep_original_evaluation_objective():
    task = {"primary_metric": "relative_l2", "primary_direction": "minimize",
            "side_constraints": [{"metric": "runtime", "operator": "<=", "value": 20}],
            "C1_revision": {"revised_primary_metric": "relative_gradient_l2",
                            "revised_direction": "minimize", "revised_side_constraints": []}}
    assert _episode_metric_contract(task, {"factual_changes": {}}, "C3") == {
        "revised_primary_metric": "relative_l2", "revised_direction": "minimize",
        "revised_side_constraints": task["side_constraints"]}
    assert _episode_metric_contract(task, {"factual_changes": {}}, "C1") == task["C1_revision"]
    assert _episode_metric_contract(task, {"factual_changes": {"evaluation_contract": {
        "revised_primary_metric": "mae", "revised_direction": "minimize"}}}, "C2") == {
            "revised_primary_metric": "mae", "revised_direction": "minimize"}


def test_fresh_model_turn_rebuilds_dynamic_frontier_after_saved_preparation(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    document = manifest([{"work_id": "dynamic-episode", "kind": "episode",
                          "resource_policy": {"active_wall_seconds": 60, "actual_gpu_seconds": 480},
                          "payload": {}}])
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)

    class Client:
        provider = {"id": "fixture-provider", "model": "fixture-model"}
        config = {"reasoning_effort": "medium"}
        timeout = 60
        def complete(self, _messages, **_kwargs):
            return {"text": json.dumps({"mode": "submit", "actions": [], "submission": {}}),
                    "usage": {"input_tokens": 4, "output_tokens": 3}}

    builds = []
    def build(_payload):
        turn = len(builds) + 1
        builds.append(turn)
        return {"arm": "FULL", "messages": [{"role": "user", "content": f"frontier {turn}"}],
                "decision_schema": {"type": "object", "required": ["mode", "actions", "submission"],
                    "properties": {"mode": {"type": "string"}, "actions": {"type": "array"},
                                   "submission": {"type": "object"}}},
                "allowed_decisions": ["inspect"], "action_bindings": [],
                "frontier_version": turn}
    monkeypatch.setattr(runtime, "build_episode_request", build)
    monkeypatch.setattr("research.experiments.study_runtime.verify_episode_submission",
        lambda *_args, **_kwargs: {"state": "completed", "valid": True})
    item = {"work_id": "dynamic-episode", "kind": "episode",
            "payload": {"arm": "FULL",
                        "resource_policy": {"active_wall_seconds": 60, "actual_gpu_seconds": 480}}}
    first = runtime.execute_model_episode(item, client=Client())
    second = runtime.execute_model_episode(item, client=Client(), history=[{"actions": []}])
    assert first["prepared_episode_payload"]["frontier_version"] == 1
    assert second["prepared_episode_payload"]["frontier_version"] == 2
    assert builds == [1, 2]


def test_existing_inference_keeps_installed_gpu_spec_while_reconciling(tmp_path, monkeypatch):
    from research.experiments import gpu_supervisor

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    document = manifest([])
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    config_path = campaign / "config.json"
    checkpoint_path = campaign / "checkpoint.pt"
    output_path = campaign / "inference"
    config_path.write_text("{}", encoding="utf-8")
    checkpoint_path.write_bytes(b"opaque checkpoint")
    spec_path = campaign / "development" / "execution_plans" / "infer-1-inference.json"
    installed = {"config_path": str(config_path), "checkpoint_path": str(checkpoint_path),
        "output_directory": str(output_path), "fit_timeout_seconds": 30,
        "gpu_devices": ["GPU-A"], "campaign": str(campaign), "work_id": "infer-1"}
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(json.dumps(installed), encoding="utf-8")

    class Supervisor:
        def __init__(self, *_args): pass
        def status(self, _work_id):
            return {"status": "running", "configuration": {"gpu_devices": ["GPU-A"]}}
        def wait(self, _work_id, *, timeout):
            assert timeout == 1.0
            return {"status": "running", "configuration": {"gpu_devices": ["GPU-A"]}}

    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", Supervisor)
    monkeypatch.setattr("research.experiments.study_runtime._supervisor_has_work", lambda *_args: True)
    binding = {"work_id": "infer-1", "inference_spec": {
        "config_path": str(config_path), "checkpoint_path": str(checkpoint_path),
        "output_directory": str(output_path), "fit_timeout_seconds": 30}}
    outcome = runtime.execute_inference(binding, gpu_devices=["GPU-B"])
    assert outcome["supervisor"]["status"] == "running"
    assert json.loads(spec_path.read_text(encoding="utf-8")) == installed

    class MismatchSupervisor(Supervisor):
        def status(self, _work_id):
            return {"status": "running", "configuration": {"gpu_devices": ["GPU-B"]}}
    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", MismatchSupervisor)
    with pytest.raises(ValueError, match="GPU device differs"):
        runtime.execute_inference(binding, gpu_devices=["GPU-B"])
    assert json.loads(spec_path.read_text(encoding="utf-8")) == installed


def test_inference_recovery_matches_original_gpu_alias_not_resolved_uuid(tmp_path, monkeypatch):
    from research.experiments import gpu_supervisor
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    manifest_path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    config = campaign / "config.json"
    checkpoint = campaign / "checkpoint.pt"
    output = campaign / "inference"
    config.write_text("{}", encoding="utf-8")
    checkpoint.write_bytes(b"checkpoint-reference")
    work_id = "infer-alias"
    spec_path = campaign / "development" / "execution_plans" / f"{work_id}-inference.json"
    spec_path.parent.mkdir(parents=True)
    installed = {"config_path": str(config), "checkpoint_path": str(checkpoint),
        "output_directory": str(output), "fit_timeout_seconds": 30,
        "gpu_devices": ["0"], "campaign": str(campaign), "work_id": work_id}
    spec_path.write_text(json.dumps(installed), encoding="utf-8")
    class AliasSupervisor:
        def __init__(self, *_args): pass
        def status(self, _work_id):
            return {"status": "running", "configuration": {
                "gpu_devices": ["GPU-fixture-0000"], "requested_gpu_devices": ["0"]}}
        def wait(self, _work_id, *, timeout):
            return self.status(_work_id)
    monkeypatch.setattr(gpu_supervisor, "ComputeSupervisor", AliasSupervisor)
    monkeypatch.setattr("research.experiments.study_runtime._supervisor_has_work", lambda *_args: True)
    result = runtime.execute_inference({"work_id": work_id, "inference_spec": {
        "config_path": str(config), "checkpoint_path": str(checkpoint),
        "output_directory": str(output), "fit_timeout_seconds": 30}}, gpu_devices=["0"])
    assert result["supervisor"]["status"] == "running"
    assert json.loads(spec_path.read_text(encoding="utf-8")) == installed


def test_cli_leaves_mixed_campaign_provider_resolution_to_each_work_item(tmp_path, monkeypatch, capsys):
    from research.experiments import study_runtime as module
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest_path = campaign / "manifest.json"
    document = manifest([])
    document["model_provider"] = {"id": "primary", "model": "gpt-6-luna",
        "config": {"reasoning_effort": "medium"}}
    document["execution_state"]["work_items"] = [
        {"work_id": "secondary-replay", "kind": "replay", "model_provider": {
            "id": "secondary", "model": "gpt-6.1-sol", "config": {"reasoning_effort": "low"}}}]
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    captured = []
    monkeypatch.setattr(StudyRuntime, "run_one", lambda self, **kwargs: captured.append(kwargs) or None)
    monkeypatch.setattr(module, "load_saved_codex_client", lambda *a, **k: pytest.fail("CLI eagerly loaded a campaign-wide client"))
    result = module.main(["run", "--campaign", str(campaign), "--ledger", str(campaign / "study.sqlite"),
        "--ledger-id", "ledger-test", "--manifest", str(manifest_path)])
    assert result == 0
    assert captured and captured[0]["client"] is None
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["idle"] is True
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=manifest_path)
    assert runtime._model_provider_spec(document["execution_state"]["work_items"][0]) == document["execution_state"]["work_items"][0]["model_provider"]


def test_required_controller_restart_final_gate_checks_the_source_receipt(tmp_path, monkeypatch):
    from research.experiments.study_runtime import _verify_required_controller_restart
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    path = campaign / "manifest.json"
    path.write_text(json.dumps(manifest([])), encoding="utf-8")
    runtime = StudyRuntime(campaign=campaign, ledger=campaign / "study.sqlite",
        ledger_id="ledger-test", manifest=path)
    policy = {"required": True, "milestone": "after_c1_checkpoint_before_confirmation"}
    episode = {"work_id": "restart-cell", "controller_restart": policy}
    valid_result = {"state": "completed", "valid": True}
    missing = _verify_required_controller_restart(runtime, episode, valid_result)
    assert missing["valid"] is False
    checkpoint = {"work": {"result_json": json.dumps({"action_history": []})},
        "model_calls": [], "ordinary_state": {}}
    checkpoint_path = campaign / "controller_checkpoint.json"
    artifact = {"original_path": str(checkpoint_path), "size": 1, "mtime_ns": 1}
    publication = {"checkpoint": checkpoint, "policy": policy, "request_id": "request",
        "dispatch_id": "dispatch-a", "worker": {"pid": 101, "process_created": 1.0}}
    checkpoint_path.write_text(json.dumps(publication), encoding="utf-8")
    monkeypatch.setattr("research.agents.processes.artifact_matches", lambda *_args: True)
    with runtime.ledger.connect() as db:
        db.execute("""CREATE TABLE forest_study_controller_restarts (
            campaign_id TEXT, work_id TEXT, request_id TEXT, policy_json TEXT, state TEXT,
            checkpoint_json TEXT, request_dispatch_id TEXT, request_pid INTEGER,
            request_process_created REAL, resume_dispatch_id TEXT, resume_pid INTEGER,
            resume_process_created REAL, receipt_json TEXT, requested_at REAL,
            resumed_at REAL, PRIMARY KEY(campaign_id, work_id))""")
        receipt = {"milestone": policy["milestone"], "previous_exit": {
            "status": "completed", "pid": 101, "process_created": 1.0,
            "completion_receipt": {"exit_code": 0}, "artifacts": [artifact]},
            "previous_worker": {"pid": 101, "process_created": 1.0},
            "resumed_worker": {"pid": 202, "process_created": 2.0},
            "checkpoint_reloaded": True,
            "request_id": "request", "resume_dispatch_id": "dispatch-b",
            "reload_proof": {"arm": "FULL", "action_history": [], "model_request_ids": []},
            "checkpoint_receipt": {"path": str(checkpoint_path), "artifact": artifact,
                **publication}}
        db.execute("INSERT INTO forest_study_controller_restarts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (runtime.campaign_id, "restart-cell", "request", json.dumps(policy), "resumed", json.dumps(checkpoint),
             "dispatch-a", 101, 1.0, "dispatch-b", 202, 2.0, json.dumps(receipt), 1.0, 2.0))
    checked = _verify_required_controller_restart(runtime, episode, valid_result)
    assert checked["valid"] is True
    assert checked["controller_restart"]["previous_worker"]["pid"] != checked["controller_restart"]["resumed_worker"]["pid"]


def test_aide_verified_metrics_accepts_actual_common_rescore_artifact_shape(tmp_path):
    source_path = tmp_path / "execution_receipt.json"
    score_path = tmp_path / "independent_score.json"
    source_path.write_text(json.dumps({"status": "SUCCEEDED", "hidden_test_opened": False,
        "configuration_id": "candidate", "seed": 1001,
        "validation_metrics": {"relative_l2": 0.125}}), encoding="utf-8")
    score_path.write_text(json.dumps({"status": "SUCCEEDED", "hidden_test_opened": False,
        "metrics": {"relative_l2": 0.125}, "prediction_path": "/researcher/validation_predictions.npz"}),
        encoding="utf-8")
    payload = {"task_contract": {"primary_metric": "relative_l2", "primary_direction": "minimize"},
        "action_bindings": [{"work_id": "score-1", "kind": "rescore",
            "configuration_id": "candidate", "seed": 1001,
            "reference_receipt_path": str(source_path), "output_path": str(score_path)}]}
    actual_actions = [{"state": "completed", "kind": "rescore", "action_work_id": "score-1",
        "configuration_id": "candidate", "seed": 1001,
        "result": {"status": "SUCCEEDED", "source_config_path": "/config/effective.json",
                   "prediction_path": "/researcher/validation_predictions.npz",
                   "metrics": {"relative_l2": 0.125}, "hidden_test_opened": False}}]
    assert StudyRuntime._aide_verified_metrics(payload, actual_actions) == [{
        "value": 0.125, "maximize": False, "configuration_id": "candidate", "seed": 1001,
        "receipt_path": str(source_path)}]


def test_aide_verified_metrics_rejects_rescore_without_matching_source_receipt(tmp_path):
    source_path = tmp_path / "execution_receipt.json"
    score_path = tmp_path / "independent_score.json"
    source_path.write_text(json.dumps({"status": "SUCCEEDED", "hidden_test_opened": False,
        "configuration_id": "candidate", "validation_metrics": {"mae": 2.0}}), encoding="utf-8")
    score_path.write_text(json.dumps({"status": "SUCCEEDED", "hidden_test_opened": False,
        "metrics": {"mae": 1.0}}), encoding="utf-8")
    payload = {"task_contract": {"primary_metric": "mae", "primary_direction": "minimize"},
        "action_bindings": [{"work_id": "score-1", "kind": "rescore",
            "configuration_id": "candidate", "reference_receipt_path": str(source_path),
            "output_path": str(score_path)}]}
    actions = [{"state": "completed", "kind": "rescore", "action_work_id": "score-1",
                "result": {"metrics": {"mae": 1.0}}}]
    assert StudyRuntime._aide_verified_metrics(payload, actions) == []

