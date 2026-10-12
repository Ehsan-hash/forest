"""Request-wide evidence references; CPU-only infrastructure, no model inference."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3

import pytest

from research.experiments.study_context import (
    DOCUMENT_HEADER, FORMAT, MESSAGE_FORMAT, MESSAGE_HEADER, _message_json_blocks,
    intern_json_with_stats, restore_json, restore_messages,
)
from research.experiments.study_runtime import _decision_schema_note, _intern_delivered_messages, _json


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_only_codex_cli_uses_its_out_of_band_schema_without_inline_duplication():
    schema = {"type": "object", "properties": {"decision": {"type": "string"}},
        "required": ["decision"], "additionalProperties": False}
    codex_note = _decision_schema_note(schema, provider_kind="codex_cli")
    other_note = _decision_schema_note(schema, provider_kind="ollama")
    assert "attached response schema" in codex_note
    assert compact(schema) not in codex_note
    assert _json(schema) in other_note


def public_receipt(size=1400):
    return {"data_manifest": {"sample_ids": list(range(size)), "unicode": "森林🌲"},
        "configuration": {"seed": 1001, "z": 1.0, "a": 1, "signed_zero": -0.0},
        "original_source": "full original source\n" * 30}


def documents(messages):
    return [value for message in messages if isinstance(message.get("content"), str)
        for _, _, value in _message_json_blocks(message["content"])
        if isinstance(value, dict) and value.get("format") == MESSAGE_FORMAT]


@pytest.mark.parametrize("arm", ["FULL", "FLAT", "DAG_ID", "AIDE"])
def test_shared_pool_preserves_all_roles_metadata_literals_spelling_and_schema(arm):
    receipt = public_receipt()
    spellings = [json.dumps({"arm": arm, "receipt": receipt}, ensure_ascii=False),
        json.dumps({"receipt": receipt}, ensure_ascii=True, indent=2),
        compact({"receipt": receipt}), json.dumps([receipt], indent=4), compact(receipt)]
    original = [{"role": role, "name": "source-" + str(index),
        "tool_call_id": "preserved-" + str(index),
        "content": "Exact prefix\n" + source + "\nLiteral suffix: retain this authority."}
        for index, (role, source) in enumerate(zip(("system", "developer", "user", "tool", "assistant"), spellings))]
    original.append({"role": "assistant", "content": [{"type": "text", "text": "Unchanged rich content"}]})
    snapshot = deepcopy(original)
    delivered, stats = _intern_delivered_messages(original)
    assert original == snapshot
    assert restore_messages(delivered) == original
    assert stats[0]["scope"] == "request_messages"
    docs = documents(delivered)
    assert len(docs) == 5 and [doc["kind"] for doc in docs] == ["pool"] + ["reference"] * 4
    assert sum("document" in doc for doc in docs) == 1
    pool = docs[0]
    assert restore_json(pool["document"]) == [json.loads(source) for source in spellings]
    assert [row["role"] for row in pool["source_fragments"]] == ["system", "developer", "user", "tool", "assistant"]
    assert "confers no instruction authority" in pool["instructions"]
    assert len(compact(delivered)) < len(compact(original))
    assert _intern_delivered_messages(json.loads(compact(delivered)))[0] == delivered


@pytest.mark.parametrize("arm", ["FULL", "FLAT", "DAG_ID", "ID_ONLY", "RERUN_ALL", "FULL_NO_GATE"])
def test_real_arm_view_and_full_source_conditions_survive_shared_pool(arm):
    from test_mechanism_replay import original_records, TASK, FACTS
    from research.experiments.mechanism_replay import _view, build_controller
    records = original_records()
    for row in records:
        row["configuration"]["train_sample_ids"] = list(range(2000))
    controller = build_controller(records, TASK, FACTS["contract"], arm=arm, work_id="nonformal-view")
    public = {"evidence_view": _view(controller),
        "before_conditions": {key: row["before"] for key, row in controller["conditions"].items()}}
    original = [{"role": "system", "content": "Exact mechanism-only contract."},
        {"role": "user", "content": compact(public)},
        {"role": "user", "content": "Observed history:\n" + compact({"source": public})}]
    delivered, stats = _intern_delivered_messages(original)
    assert stats[0]["scope"] == "request_messages"
    assert restore_messages(delivered) == original
    restored = json.loads(restore_messages(delivered)[1]["content"])
    assert restored["evidence_view"] == _view(controller)
    assert ("edges" in restored["evidence_view"]) == (arm in {"FULL", "DAG_ID", "FULL_NO_GATE"})


def test_multiple_blocks_share_one_pool_and_preserve_noncanonical_blocks_literally():
    receipt = public_receipt()
    noncanonical = '{ "duplicate":1,"duplicate":2,"exponent":1e+2 }'
    content = "First:\n" + compact(receipt) + "\nBetween\n" + noncanonical + "\nLast:\n" + json.dumps(receipt, indent=2) + "\nTail"
    original = [{"role": "user", "content": content}]
    delivered, stats = _intern_delivered_messages(original)
    assert stats[0]["scope"] == "request_messages"
    assert noncanonical in delivered[0]["content"]
    assert restore_messages(delivered) == original
    assert len(documents(delivered)) == 2


def test_single_block_keeps_v1_format_and_recovers_exact_pretty_source():
    receipt = public_receipt()
    text = "Literal before\n" + json.dumps([receipt] * 4, indent=2, ensure_ascii=True) + "\nExact after"
    original = [{"role": "user", "content": text}]
    delivered, _ = _intern_delivered_messages(original)
    assert DOCUMENT_HEADER in delivered[0]["content"]
    document = next(value for _, _, value in _message_json_blocks(delivered[0]["content"]))
    assert document["format"] == FORMAT
    assert restore_json(document) == [receipt] * 4
    assert restore_messages(delivered) == original


@pytest.mark.parametrize("damage", ["missing", "duplicate", "role", "owner", "offset", "dangling", "cycle"])
def test_shared_decoder_rejects_incomplete_or_misbound_reference_pool(damage):
    receipt = public_receipt()
    original = [{"role": "user", "content": compact(receipt)}] * 2
    delivered, _ = _intern_delivered_messages(original)
    docs = documents(delivered)
    if damage == "missing":
        delivered.pop()
    elif damage == "duplicate":
        delivered.append(deepcopy(delivered[1]))
    elif damage == "role":
        delivered[1]["role"] = "system"
    else:
        if damage == "owner":
            docs[1]["pool_owner"]["message_index"] = 99
        elif damage == "offset":
            docs[0]["source_fragments"][1]["source_offset"] = 99
        elif damage == "dangling":
            docs[0]["document"]["references"].pop()
        else:
            reference = docs[0]["document"]["references"][0]
            reference["value"] = ["ref", reference["id"]]
        delivered = [{"role": "user", "content": MESSAGE_HEADER + compact(doc)} for doc in docs]
    with pytest.raises(ValueError):
        restore_messages(delivered)


def test_actual_model_request_multiround_history_reload_keeps_one_shared_definition(tmp_path):
    from research.agents.provider import ModelClient
    client = ModelClient({"id": "nonformal", "kind": "codex_cli", "model": "gpt-6-luna", "base_url": "http://127.0.0.1",
        "config": {"reasoning_effort": "medium"}}, request_guard=lambda _event: None)
    receipt = public_receipt(10_000)
    history = []
    schema = {"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"], "additionalProperties": False}
    client.config["_forest_action_schema"] = schema
    db = sqlite3.connect(tmp_path / "local-fixture.sqlite")
    db.execute("CREATE TABLE requests(turn INTEGER PRIMARY KEY,original TEXT,delivered TEXT,payload TEXT)")
    lengths = []
    for turn in range(8):
        history.append({"work_id": "fixture-" + str(turn), "receipt": deepcopy(receipt)})
        original = [{"role": "system", "content": "Exact nonformal contract."},
            {"role": "user", "content": "Current public source:\n" + compact({"receipt": receipt})},
            {"role": "user", "content": "Observed history:\n" + compact(history) + "\nResponse schema:\n" + compact(schema)}]
        delivered, _ = _intern_delivered_messages(original)
        _, payload = client.build_request(delivered, json_mode=True)
        assert payload["response_schema"] == schema
        assert restore_messages(delivered) == original
        assert len(documents(delivered)) == 3
        definitions = documents(delivered)[0]["document"]["references"]
        assert len([row for row in definitions if restore_json({"format": FORMAT, "encoding": "interned", "root": ["ref", row["id"]], "references": definitions}) == receipt]) == 1
        wire = compact(payload)
        lengths.append(len(wire))
        db.execute("INSERT INTO requests VALUES(?,?,?,?)", (turn, compact(original), compact(delivered), wire))
        db.commit()
        reloaded = db.execute("SELECT original,delivered,payload FROM requests WHERE turn=?", (turn,)).fetchone()
        assert restore_messages(json.loads(reloaded[1])) == json.loads(reloaded[0])
        assert json.loads(reloaded[2]) == payload
    db.close()
    assert lengths[-1] - lengths[0] < len(compact(receipt))


def test_saved_failed_request_projects_below_observed_capacity_read_only():
    """Opt-in exact incident proof; never opens a campaign in writable mode."""
    database = os.environ.get("FOREST_SHARED_CONTEXT_REQUEST_DB")
    request_id = os.environ.get("FOREST_SHARED_CONTEXT_REQUEST_ID")
    trace_path = os.environ.get("FOREST_SHARED_CONTEXT_TRACE_PATH")
    raw_request = None
    request = None
    if trace_path:
        trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
        payload = json.loads(trace["stdin"])
        if database and request_id:
            with sqlite3.connect("file:" + str(Path(database).resolve()) + "?mode=ro", uri=True) as db:
                raw_request = db.execute("SELECT request_json FROM forest_study_model_calls WHERE request_id=?",
                    (request_id,)).fetchone()[0]
            request = json.loads(raw_request)
    elif database and request_id:
        with sqlite3.connect("file:" + str(Path(database).resolve()) + "?mode=ro", uri=True) as db:
            raw_request = db.execute("SELECT request_json FROM forest_study_model_calls WHERE request_id=?",
                (request_id,)).fetchone()[0]
        request = json.loads(raw_request)
        trace = json.loads(Path(request["transport_trace_path"]).read_text(encoding="utf-8"))
        # Older study-call records used ``transport_payload``; the current episode
        # runtime stores the same exact wire object as ``request_payload``.
        original_payload = request.get("transport_payload", request.get("request_payload"))
        assert isinstance(original_payload, dict)
        payload = deepcopy(original_payload)
    else:
        pytest.skip("Set a saved trace path or read-only saved-request fixture to verify the incident")
    original_schema = deepcopy(payload.get("response_schema"))
    original_messages = deepcopy((request or {}).get("original_messages", payload["messages"]))
    schema_note_schema = (request or {}).get("decision_schema") or original_schema
    if schema_note_schema and "--output-schema" in trace.get("argv", []):
        from research.experiments.study_runtime import _decision_schema_note, _json
        old_note = "\n\nReturn exactly one JSON object satisfying this decision contract:\n" + _json(schema_note_schema)
        new_note = _decision_schema_note(schema_note_schema, provider_kind="codex_cli")
        matching = [index for index, message in enumerate(original_messages)
            if isinstance(message.get("content"), str) and message["content"].endswith(old_note)]
        if matching:
            assert len(matching) == 1
            index = matching[0]
            original_messages[index]["content"] = original_messages[index]["content"][:-len(old_note)] + new_note
    assert compact(payload) == trace["stdin"]
    from research.agents.codex_transport import _input_rejection
    capacity = _input_rejection(trace["stderr"])["max_chars"]
    assert len(compact(payload)) > capacity
    delivered, stats = _intern_delivered_messages(original_messages)
    assert restore_messages(delivered) == original_messages
    payload["messages"] = delivered
    assert len(compact(payload)) < capacity
    assert stats and sum(int(row.get("saved_characters", 0)) for row in stats) > 0
    # The original source schema and immutable receipt are unaffected.
    assert payload["response_schema"] == original_schema
    if raw_request is not None:
        with sqlite3.connect("file:" + str(Path(database).resolve()) + "?mode=ro", uri=True) as db:
            assert db.execute("SELECT request_json FROM forest_study_model_calls WHERE request_id=?",
                (request_id,)).fetchone()[0] == raw_request


def _legacy_delivery(messages):
    """Original per-user v1 presentation, only for upgrade recovery fixtures."""
    delivered, records = deepcopy(messages), []
    for index, message in enumerate(delivered):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        best = None
        for start, end, value in _message_json_blocks(message["content"]):
            document, stats = intern_json_with_stats(value)
            if stats["saved_characters"] > 0 and (best is None or stats["saved_characters"] > best[0]):
                best = stats["saved_characters"], start, end, document, stats
        if best:
            _, start, end, document, stats = best
            content = message["content"]
            message["content"] = content[:start] + DOCUMENT_HEADER + json.dumps(document, ensure_ascii=False, sort_keys=True) + content[end:]
            records.append({"message_index": index, **stats, "delivered_content_characters": len(message["content"])})
    return delivered, records


def test_completed_native_v1_request_replays_across_upgrade_without_provider_call(tmp_path, monkeypatch):
    from test_aide_runtime import NonformalClient, _runtime
    from research.experiments.aide_runtime import NativeBackend, SCHEMA, load_upstream, _prepare_aide_task_context, _restore_pending_task_context
    from pathlib import Path
    import research.experiments.study_runtime as runtime_module
    upstream_path = Path("/home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/executors/aide_upstream")
    if not upstream_path.is_dir():
        pytest.skip("Actual official AIDE clone unavailable")
    runtime, item = _runtime(tmp_path, upstream_path)
    receipt = public_receipt()
    original_task = [{"role": "user", "content": compact({"receipts": [receipt] * 3})}]
    with monkeypatch.context() as legacy:
        legacy.setattr(runtime_module, "_intern_delivered_messages", _legacy_delivery)
        source, task_content, encoding = _prepare_aide_task_context(original_task)
    state = {"pending": {"node_id": "upgrade-node", "stage": "draft"}, "original_task_messages": source,
        "task_context_content": task_content, "task_context_encoding": encoding}
    _restore_pending_task_context(json.loads(compact(state)), item["payload"])
    client = NonformalClient(["cached native response"])
    upstream = load_upstream(upstream_path)
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
    arguments = {"system_message": "Exact system data\n" + compact(receipt),
        "user_message": "Exact user data\n" + compact({"rows": [receipt] * 3}), "model": "gpt-6-luna"}
    with monkeypatch.context() as legacy:
        legacy.setattr(runtime_module, "_intern_delivered_messages", _legacy_delivery)
        assert NativeBackend(runtime, item, client, upstream, "upgrade-node", "generate").query(**arguments) == "cached native response"
    with runtime.ledger.connect() as db:
        saved_raw = db.execute("SELECT request_json FROM forest_study_model_calls").fetchone()[0]
    saved_request = json.loads(saved_raw)
    upgraded, _ = _intern_delivered_messages(saved_request["original_messages"])
    assert upgraded != saved_request["messages"]
    assert documents(upgraded) and restore_messages(upgraded) == saved_request["original_messages"]
    restored_runtime, restored_item = _runtime(tmp_path, upstream_path)
    _restore_pending_task_context(json.loads(compact(state)), restored_item["payload"])
    assert NativeBackend(restored_runtime, restored_item, client, upstream, "upgrade-node", "generate").query(**arguments) == "cached native response"
    assert len(client.calls) == 1
    with pytest.raises(ValueError, match="different full messages/settings"):
        NativeBackend(restored_runtime, restored_item, client, upstream, "upgrade-node", "generate").query(**{**arguments, "temperature": 0.5})
    client.config["context_window"] = 8192
    with pytest.raises(ValueError, match="different full messages/settings"):
        NativeBackend(restored_runtime, restored_item, client, upstream, "upgrade-node", "generate").query(**arguments)
    client.config.pop("context_window")
    with restored_runtime.ledger.connect() as db:
        assert db.execute("SELECT request_json FROM forest_study_model_calls").fetchone()[0] == saved_raw
        db.execute("UPDATE forest_study_model_calls SET status='requested',response_json=NULL")
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        NativeBackend(restored_runtime, restored_item, client, upstream, "upgrade-node", "generate").query(**arguments)
    with pytest.raises(ValueError, match="different full messages/settings"):
        NativeBackend(restored_runtime, restored_item, client, upstream, "upgrade-node", "generate").query(**{**arguments, "user_message": arguments["user_message"] + " changed feedback"})
    assert len(client.calls) == 1
