"""Nonformal AIDE infrastructure fixtures; no model/GPU scientific results.

Native integration uses the actual WecoAI AIDE clone, scripted inference
responses and real CPU public-action subprocesses. These fixture numbers are
not scientific scores and cannot qualify any campaign cell.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import threading

import pytest

from research.experiments.aide_runtime import (
    NativeBackend, SCHEMA, _bound_backend, _query_router, _RandomRouter, load_upstream,
    run_action_program, run_aide_episode,
)
from research.experiments.study_runtime import StudyRuntime


def test_public_action_program_runs_real_cpu_child():
    result = run_action_program('import json\ndecision = {"mode": "action", "actions": [{"kind": "inspect"}], "submission": {}, "rationale": "nonformal CPU fixture"}\nprint(json.dumps(decision))\n')
    assert result["exc_type"] is None
    assert result["decision"]["actions"] == [{"kind": "inspect"}]
    assert result["exec_time"] > 0


@pytest.mark.parametrize("code", [
    'print(open("hidden-test.npy").read())',
    'import os\nprint(os.listdir("/"))',
    'import json\njson = {}\nprint("{}")',
    'import json\nprint(json.loads("{}"))',
    'import json\nprint(json.dumps({**{"mode": "submit"}}))',
    'import json\nprint(json.dumps({}))\nprint(json.dumps({}))',
    'while True:\n pass',
])
def test_public_action_program_rejects_unavailable_capabilities(code):
    result = run_action_program(code)
    assert result["decision"] is None
    assert result["exc_type"] == "ValueError"


def test_context_routes_independent_model_and_native_rng():
    barrier = threading.Barrier(2)

    class NonformalBackend:
        def __init__(self, label):
            self.label = label
        def query(self):
            return self.label

    def worker(label, seed):
        with _bound_backend(NonformalBackend(label), random.Random(seed)):
            barrier.wait(timeout=5)
            return _query_router(), _RandomRouter().random()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, "fixture-A", 3)
        second = pool.submit(worker, "fixture-B", 7)
        assert first.result() == ("fixture-A", random.Random(3).random())
        assert second.result() == ("fixture-B", random.Random(7).random())
    with pytest.raises(RuntimeError, match="bound FOREST"):
        _query_router()


@pytest.fixture
def native_upstream():
    root = Path(os.environ.get("FOREST_AIDE_UPSTREAM", "/home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/executors/aide_upstream"))
    if not root.is_dir():
        pytest.skip("Actual official AIDE clone not available; set FOREST_AIDE_UPSTREAM")
    return root



def test_native_import_preserves_host_warning_policy(native_upstream):
    script = """
import sys
import warnings
from pathlib import Path
from research.experiments.aide_runtime import load_upstream
load_upstream(Path(sys.argv[1]))
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always', RuntimeWarning)
    warnings.warn('Original host diagnostic', RuntimeWarning)
    assert len(caught) == 1
    assert str(caught[0].message) == 'Original host diagnostic'
with warnings.catch_warnings():
    warnings.simplefilter('error', RuntimeWarning)
    try:
        warnings.warn('Original host failure', RuntimeWarning)
    except RuntimeWarning:
        pass
    else:
        raise AssertionError('Native import disabled host warning errors')
"""
    subprocess.run([sys.executable, '-c', script, str(native_upstream)],
                   check=True, capture_output=True, text=True, timeout=30)


def _program(mode="action", action_work_id=None):
    decision = {"mode": mode, "actions": [{"kind": "inspect"}] if mode == "action" else [],
                "submission": {"selected_configuration_id": "fixture-cfg", "claim": "nonformal infrastructure fixture"},
                "rationale": "nonformal infrastructure fixture"}
    if mode == "action" and action_work_id is not None:
        decision["actions"][0]["action_work_id"] = action_work_id
    return "import json\ndecision = " + repr(decision) + "\nprint(json.dumps(decision))\n"


class NonformalClient:
    """Use real ModelClient request construction, with no live inference."""
    def __init__(self, responses):
        from research.agents.provider import ModelClient
        provider = {"id": "nonformal-fixture", "kind": "codex_cli", "base_url": "http://127.0.0.1",
                    "model": "gpt-6-luna", "allow_paid": False,
                    "config": {"reasoning_effort": "medium"}}
        self.real = ModelClient(provider, request_guard=lambda _event: None)
        self.provider = provider
        self.config = dict(provider["config"])
        self.responses, self.calls = list(responses), []
    def build_request(self, messages, **kwargs):
        self.real.config = dict(self.config)
        return self.real.build_request(messages, **kwargs)
    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"text": self.responses.pop(0), "usage": {"cost": None, "cost_source": "nonformal_scripted_fixture"},
                "model": "gpt-6-luna", "status": "completed", "response_id": None}


def _runtime(tmp_path, upstream):
    manifest = {"campaign_id": "nonformal-aide-fixture", "manifest_id": "nonformal-manifest",
                "manifest_version": 1, "external_ledger_id": "nonformal-ledger",
                "model_provider": NonformalClient([]).provider,
                "execution_state": {"task_queue": [{"work_id": "native-episode", "kind": "episode",
                    "arm": "AIDE", "aide_upstream_root": str(upstream),
                    "resource_policy": {"active_wall_seconds": 1800, "actual_gpu_seconds": 480},
                    "aide_search": {"num_drafts": 1, "debug_prob": 0.0, "max_debug_depth": 3},
                    "messages": [{"role": "user", "content": "Nonformal infrastructure fixture, no scientific results."}],
                    "action_bindings": [{"action_name": "inspect", "kind": "analysis"}]}]}}
    path = tmp_path / "study_execution_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "study.sqlite",
                           ledger_id="nonformal-ledger", manifest=path)
    item = {"work_id": "native-episode", "kind": "episode", "payload": manifest["execution_state"]["task_queue"][0]}
    return runtime, item


def _review(metric=1.25, buggy=False):
    return json.dumps({"is_bug": buggy, "summary": "Nonformal fixture review of actual CPU operation output.",
                       "metric": metric, "lower_is_better": True})


def test_native_pending_node_resumes_without_regeneration_then_improves(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient(["A nonformal action fixture.\n```python\n" + _program() + "```", _review(),
                             "A nonformal improvement fixture.\n```python\n" + _program("submit") + "```", _review()])
    ready = False
    cpu_runs = []
    saved_programs = {}
    calls = []

    def hook(_item, *, node_id, code, **_kwargs):
        calls.append(node_id)
        if node_id not in saved_programs:
            saved_programs[node_id] = run_action_program(code)
            cpu_runs.append(node_id)
        program = saved_programs[node_id]
        assert program["exc_type"] is None
        if not ready:
            return {"node_id": node_id, "state": "retryable", "action_work_ids": ["nonformal-cpu-child"],
                    "pending_work_ids": ["nonformal-cpu-child"], "stdout": program["stdout"]}
        receipt = tmp_path / "nonformal_fixture_receipt.json"
        receipt.write_text(json.dumps({"nonformal_fixture": True, "actual_process": "public Python action child"}), encoding="utf-8")
        outcome = {"node_id": node_id, "state": "completed", "action_work_ids": ["nonformal-cpu-child"],
                   "pending_work_ids": [], "action_results": [],
                   "stdout": "Nonformal CPU fixture outcome value=1.25; not a scientific score.\n",
                   "exec_time": program["exec_time"],
                   "verified_metrics": [{"value": 1.25, "maximize": False, "configuration_id": "fixture-cfg",
                                         "seed": 7, "receipt_path": str(receipt)}]}
        if program["decision"]["mode"] == "submit":
            outcome["submission_validation"] = {"valid": True, "nonformal_fixture": True}
        return outcome

    runtime.execute_aide_node = hook
    first = run_aide_episode(runtime, item, client=client)
    assert first["pending_action"] is True and first["native_completed_steps"] == 0
    node_id = first["node_id"]
    assert len(client.calls) == 1 and cpu_runs == [node_id]
    # Reconstruct the actual StudyRuntime from the same original SQLite file.
    reconstructed, resumed_item = _runtime(tmp_path, native_upstream)
    reconstructed.execute_aide_node = hook
    again = run_aide_episode(reconstructed, resumed_item, client=client)
    assert again["node_id"] == node_id and len(client.calls) == 1 and cpu_runs == [node_id]
    ready = True
    completed = run_aide_episode(reconstructed, resumed_item, client=client)
    assert completed["native_completed_steps"] == 1 and completed["pending_action"] is False
    final = run_aide_episode(reconstructed, resumed_item, client=client)
    assert final["state"] == "completed" and final["native_completed_steps"] == 2
    replay = run_aide_episode(reconstructed, resumed_item, client=client)
    assert replay == final and len(client.calls) == 4 and len(cpu_runs) == 2
    with sqlite3.connect(runtime.ledger_path) as db:
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
        events = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM forest_study_events WHERE event_type='aide_native_step_completed'")]
        records = db.execute("SELECT request_json,response_json,usage_json FROM forest_study_model_calls").fetchall()
    upstream = load_upstream(native_upstream)
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    assert journal.nodes[0].id == node_id
    assert journal.nodes[1].parent is journal.nodes[0] and journal.nodes[1].stage_name == "improve"
    assert journal.nodes[1] in journal.nodes[0].children
    assert [event["native_completed_steps"] for event in events] == [1, 2]
    assert len(records) == 4
    for raw_request, raw_response, raw_usage in records:
        request = json.loads(raw_request)
        assert request["request_payload"]["messages"] == request["messages"]
        assert json.loads(raw_response)["model"] == "gpt-6-luna"
        assert json.loads(raw_usage)["cost_source"] == "nonformal_scripted_fixture"


def test_native_feedback_unverified_metric_is_buggy_not_oracle_replaced(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient(["Nonformal draft.\n```python\n" + _program() + "```", _review(metric=99.0)])
    runtime.execute_aide_node = lambda _item, node_id, code, **kwargs: {
        "node_id": node_id, "state": "completed", "stdout": "Nonformal actual CPU fixture observation 1.25",
        "verified_metrics": [{"value": 1.25, "maximize": False, "receipt_path": "nonformal-fixture"}]}
    result = run_aide_episode(runtime, item, client=client)
    assert result["native_completed_steps"] == 1
    with runtime.ledger.connect() as db:
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
    upstream = load_upstream(native_upstream)
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    assert journal.nodes[0].is_buggy is True and journal.nodes[0].metric.is_worst
    assert journal.nodes[0].metric.value is None


def test_native_debugs_actual_failed_cpu_action_program(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    item["payload"]["aide_search"]["debug_prob"] = 1.0
    bad = 'print(open("unavailable-data.txt").read())'
    client = NonformalClient(["Nonformal broken draft.\n```python\n" + bad + "\n```", _review(metric=None, buggy=True),
                             "Nonformal native bugfix.\n```python\n" + _program() + "```", _review()])
    def hook(_item, *, node_id, code, **kwargs):
        program = run_action_program(code)
        return {"node_id": node_id, "state": "completed", **program,
                "verified_metrics": [{"value": 1.25, "maximize": False, "receipt_path": "nonformal-fixture"}]}
    runtime.execute_aide_node = hook
    assert run_aide_episode(runtime, item, client=client)["native_completed_steps"] == 1
    assert run_aide_episode(runtime, item, client=client)["native_completed_steps"] == 2
    with runtime.ledger.connect() as db:
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
    upstream = load_upstream(native_upstream)
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    assert journal.nodes[0].is_buggy is True
    assert journal.nodes[1].stage_name == "debug" and journal.nodes[1].parent is journal.nodes[0]


def test_completed_native_request_replays_and_unsettled_request_never_repeats(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    upstream = load_upstream(native_upstream)
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
    client = NonformalClient(["nonformal response"])
    first = NativeBackend(runtime, item, client, upstream, "fixture-native-node", "generate")
    assert first.query(system_message="Nonformal query fixture", model="gpt-6-luna") == "nonformal response"
    def exhausted(_item, _client):
        raise RuntimeError("nonformal remaining inference allowance exhausted")
    runtime._budgeted_episode_client = exhausted
    replay = NativeBackend(runtime, item, client, upstream, "fixture-native-node", "generate")
    assert replay.query(system_message="Nonformal query fixture", model="gpt-6-luna") == "nonformal response"
    assert len(client.calls) == 1
    with runtime.ledger.connect() as db:
        db.execute("UPDATE forest_study_model_calls SET status='requested',response_json=NULL")
    unsettled = NativeBackend(runtime, item, client, upstream, "fixture-native-node", "generate")
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        unsettled.query(system_message="Nonformal query fixture", model="gpt-6-luna")
    assert len(client.calls) == 1


def test_actual_study_dispatch_uses_native_agent_and_common_cpu_analysis(tmp_path, native_upstream):
    """Nonformal end-to-end infrastructure path, with actual CPU children."""
    runtime, item = _runtime(tmp_path, native_upstream)
    manifest_path = tmp_path / "study_execution_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = manifest["execution_state"]["task_queue"][0]
    payload["decision_schema"] = {"type": "object", "required": ["mode", "actions", "submission", "rationale"]}
    payload["allowed_decisions"] = ["inspect"]
    payload["task_contract"] = {"primary_metric": "nonformal-fixture-metric", "primary_direction": "minimize"}
    payload["action_bindings"] = [{"kind": "analysis", "action_name": "inspect",
        "work_id": "nonformal-common-inspection", "configuration_id": "fixture-cfg", "seed": 7,
        "analysis_operation": "inspect", "evidence": [{"work_id": "nonformal-unrun-fit",
            "configuration_id": "fixture-cfg", "seed": 7, "state": "not_run", "metrics": {}}]}]
    # Use a fresh ledger for this precise source-bound fixture manifest.
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    runtime = StudyRuntime(campaign=tmp_path, ledger=tmp_path / "dispatch.sqlite",
                           ledger_id="nonformal-ledger", manifest=manifest_path)
    client = NonformalClient(["Nonformal actual dispatch.\n```python\n" + _program(action_work_id="nonformal-common-inspection") + "```", _review(metric=None, buggy=True)])
    result = runtime.run_one(owner="nonformal-CPU-owner", work_id=item["work_id"],
                             client=client, defer_gpu=True)
    assert result["kind"] == "episode" and result["native_completed_steps"] == 1
    assert result["state"] == "retryable" and result["pending_action"] is False
    with runtime.ledger.connect() as db:
        child = db.execute("SELECT state,result_json FROM forest_study_work WHERE work_id='nonformal-common-inspection'").fetchone()
        assert child["state"] == "completed"
        inspection = json.loads(child["result_json"])
        assert inspection["status"] == "SUCCEEDED" and inspection["evidence"][0]["state"] == "not_run"
        assert db.execute("SELECT count(*) FROM forest_study_aide_programs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM forest_study_model_calls WHERE status='completed'").fetchone()[0] == 2


def test_common_budget_exhaustion_does_not_launch_native_feedback(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient(["Nonformal draft.\n```python\n" + _program() + "```"])
    runtime.execute_aide_node = lambda _item, node_id, code, **kwargs: {
        "node_id": node_id, "state": "budget_exhausted", "stdout": "Actual common allowance is exhausted",
        "action_work_ids": [], "pending_work_ids": [], "verified_metrics": []}
    result = run_aide_episode(runtime, item, client=client)
    assert result["state"] == "budget_exhausted" and result["native_completed_steps"] == 0
    assert len(client.calls) == 1
    assert run_aide_episode(runtime, item, client=client) == result
    assert len(client.calls) == 1
    with runtime.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='aide_native_step_completed'").fetchone()[0] == 0
        state = json.loads(db.execute("SELECT state_json FROM forest_study_aide_sessions").fetchone()[0])
        assert state["pending"]["node"]["code"]


def test_medium_reasoning_must_be_explicit_not_transport_default(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient([])
    client.config.pop("reasoning_effort")
    with pytest.raises(ValueError, match="explicit reasoning effort"):
        NativeBackend(runtime, item, client, load_upstream(native_upstream), "fixture-node", "generate")


def test_native_provider_follows_registered_model_and_effort(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient([])
    client.provider["model"] = "registered-local-model-fixture"
    client.config["reasoning_effort"] = "high"
    runtime.manifest["model_provider"] = {**client.provider, "config": dict(client.config)}
    NativeBackend(runtime, item, client, load_upstream(native_upstream), "fixture-node", "generate")
    client.config["reasoning_effort"] = "medium"
    with pytest.raises(ValueError, match="original registered"):
        NativeBackend(runtime, item, client, load_upstream(native_upstream), "fixture-node", "generate")


def test_native_output_uses_stage_allocation_and_explicit_source_caps(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    client = NonformalClient(["first fixture", "second fixture", "third fixture"])
    client.config["adaptive_output_budget"] = {"stage_targets": {"experiment": 6000}}
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
    upstream = load_upstream(native_upstream)
    NativeBackend(runtime, item, client, upstream, "one", "generate").query(model="gpt-6-luna")
    assert client.calls[-1][1]["max_output_tokens"] == 6000
    item["payload"]["max_output_tokens"] = 4000
    NativeBackend(runtime, item, client, upstream, "two", "generate").query(model="gpt-6-luna", max_tokens=3500)
    assert client.calls[-1][1]["max_output_tokens"] == 3500
    NativeBackend(runtime, item, client, upstream, "three", "generate").query(model="gpt-6-luna", max_tokens=8000)
    assert client.calls[-1][1]["max_output_tokens"] == 4000


def test_native_replay_uses_adaptive_output_and_cached_response_costs_none(tmp_path, native_upstream):
    runtime, item = _runtime(tmp_path, native_upstream)
    item["kind"] = "replay"
    client = NonformalClient(["replay fixture"])
    runtime._replay_output_allowance = lambda work_id: 731
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
    upstream = load_upstream(native_upstream)
    assert NativeBackend(runtime, item, client, upstream, "replay-node", "generate").query(model="gpt-6-luna") == "replay fixture"
    assert client.calls[-1][1]["max_output_tokens"] == 1024
    runtime._replay_output_allowance = lambda work_id: 0
    assert NativeBackend(runtime, item, client, upstream, "replay-node", "generate").query(model="gpt-6-luna") == "replay fixture"
    assert len(client.calls) == 1
