"""Shared, from-scratch long-task stages for FULL and native AIDE.

All stages consume registered public actions and actual original-ledger
receipts. No snapshot fit or hidden evaluation is admitted to these episodes.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS forest_study_end_to_end (
 campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, state_json TEXT NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(campaign_id,work_id));
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a registered JSON object: {path}")
    return value


def is_end_to_end(payload):
    nested = payload.get("episode") or {}
    return any(str(value).upper() == "LIVE_END_TO_END" for value in (
        payload.get("category"), payload.get("stage"), nested.get("category")))


def _save(runtime, work_id, state, event=None):
    with runtime.ledger.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO forest_study_end_to_end VALUES(?,?,?,?) ON CONFLICT(campaign_id,work_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                   (runtime.campaign_id, work_id, _json(state), time.time()))
        if event:
            db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), runtime.campaign_id, work_id, event[0], _json(event[1]), time.time()))


def _load(runtime, work_id):
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
        row = db.execute("SELECT state_json FROM forest_study_end_to_end WHERE campaign_id=? AND work_id=?",
                         (runtime.campaign_id, work_id)).fetchone()
    return json.loads(row[0]) if row else None


def _registered_task(runtime, payload):
    task_id = str(payload.get("task_id") or (payload.get("episode") or {}).get("task_id") or "")
    task = next((deepcopy(dict(value)) for value in runtime.manifest.get("tasks", [])
                 if str(value.get("task_id")) == task_id), None)
    if task is None or not isinstance(task.get("long_from_scratch"), Mapping) or not task["long_from_scratch"].get("enabled"):
        raise ValueError("End-to-end work must name an enabled registered long_from_scratch task")
    return task


def _contract(task, revised):
    if revised:
        value = task.get("C1_revision") or {}
        metric, direction = value.get("revised_primary_metric"), value.get("revised_direction")
        constraints = value.get("revised_side_constraints", [])
        effect = value.get("revised_effect_threshold", task.get("effect_threshold"))
    else:
        metric, direction = task.get("primary_metric"), task.get("primary_direction")
        constraints = task["long_from_scratch"].get("side_constraints", task.get("side_constraints", []))
        effect = task.get("effect_threshold")
    if not isinstance(metric, str) or direction not in {"minimize", "maximize"}:
        raise ValueError("The active long-task objective must be explicitly registered")
    return {"version": 2 if revised else 1, "primary_metric": metric,
            "primary_direction": direction, "side_constraints": deepcopy(constraints),
            "effect_threshold": deepcopy(effect), "scientific_question": task["scientific_question"]}


def _initial_state(runtime, payload, task):
    from .training_runtime import build_fit_config, enumerate_long_configurations
    work_id = str(payload["work_id"])
    source = Path(str(payload.get("source_spec_dir") or runtime.manifest.get("source_spec_dir") or runtime.manifest.get("historical_source_spec_dir")))
    candidates = enumerate_long_configurations(source_spec_dir=source, task_id=task["task_id"])
    anchors = [candidate for candidate in candidates if candidate["is_anchor"]]
    if len(anchors) != 1:
        raise ValueError("Long-task Cartesian space must have exactly one declared anchor")
    spec = task["long_from_scratch"]
    policy = payload.get("resource_policy") or {}
    gpu_allowance = policy.get("actual_gpu_seconds")
    if (not isinstance(gpu_allowance, (int, float)) or isinstance(gpu_allowance, bool)
            or not math.isfinite(float(gpu_allowance)) or float(gpu_allowance) <= 0):
        raise ValueError("End-to-end work requires an explicit positive finite registered GPU allowance")
    resource_policy = {"actual_gpu_seconds": float(gpu_allowance), "active_wall_seconds": None}
    maximum = int(spec["max_distinct_exploration_configurations_including_anchor"])
    if maximum < 2 or maximum > len(candidates):
        raise ValueError("Long exploration bound cannot support the registered non-anchor comparison")
    ordered = [anchors[0]] + sorted((candidate for candidate in candidates if not candidate["is_anchor"]),
                                  key=lambda candidate: candidate["configuration_id"])
    # Current executable plan prespecifies lexical additional configurations.
    order = [candidate["configuration_id"] for candidate in ordered[:maximum]]
    exploration_seed = int(spec["exploration_seed"])
    seeds = list(map(int, spec["confirmation_seeds"]))
    if (int(payload.get("exploration_seed", exploration_seed)) != exploration_seed
            or list(map(int, payload.get("confirmation_seeds", seeds))) != seeds):
        raise ValueError("An episode cannot override its registered long-task seeds")
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("Confirmation seeds must be an explicit nonempty distinct sequence")
    updates_path = payload.get("resolved_updates_path") or runtime.manifest.get("experiment_state", {}).get("resolved_updates_reference")
    resolved = _read(updates_path)
    if not resolved.get("source_validation", {}).get("valid") or resolved.get("formal_score_eligible") is not False:
        raise ValueError("Long-task fits require actual independently validated calibration update counts")
    family = str(task["family_id"])
    updates = (resolved.get("family_update_counts") or {}).get(family)
    if not isinstance(updates, int) or isinstance(updates, bool) or updates < 1:
        raise ValueError("No valid observed family update count exists for this long task")
    data_ref = payload.get("data_ids_reference") or runtime.manifest.get("experiment_state", {}).get("data_ids_reference")
    data_ids = _read(data_ref)[family]
    version_ids = payload.get("dataset_version_ids")
    if not isinstance(version_ids, Mapping):
        version_ids = _read(payload.get("dataset_versions_reference") or runtime.manifest["experiment_state"]["dataset_versions_reference"])
    records = []
    for candidate in ordered:
        for stage, seed_values in (("exploration", [exploration_seed]), ("confirmation", seeds)):
            for seed in seed_values:
                fit_id = str(uuid.uuid4())
                output = runtime.campaign / "runs" / "end_to_end" / work_id / stage / fit_id
                config = build_fit_config(source_spec_dir=source, task_id=task["task_id"],
                    configuration_id=candidate["configuration_id"], seed=seed,
                    optimizer_updates=updates, dataset_version_id=str(version_ids[family]),
                    data_ids=data_ids, phase="formal", work_id=fit_id,
                    configuration_overrides=candidate["overrides"])
                config["is_anchor"] = bool(candidate["is_anchor"])
                config["output_reference"] = str(output)
                config["source_revision_reference"] = str(payload.get("source_revision_reference") or source)
                path = runtime.campaign / "development" / "end_to_end_configs" / work_id / f"{fit_id}.json"
                records.append({"fit_id": fit_id, "stage": stage, "seed": seed,
                    "configuration_id": candidate["configuration_id"], "configuration_path": str(path),
                    "output_directory": str(output), "config": config})
    return {"version": 1, "episode_id": work_id, "task_definition": task,
            "source_spec_dir": str(source), "resolved_updates_reference": str(updates_path),
            "data_ids_reference": str(data_ref), "dataset_version_ids": dict(version_ids),
            "resource_policy": resource_policy,
            "exploration_order": order, "exploration_seed": exploration_seed,
            "confirmation_seeds": seeds, "anchor_configuration_id": anchors[0]["configuration_id"],
            "fits": records, "bindings": {}, "objective": _contract(task, False),
            "objective_history": [], "comparison_records": [], "stage": "exploration",
            "pending_work_ids": []}


def _episode_history(runtime, work_id):
    """Retain actual decisions and response identities at the C1 transition."""
    with runtime.ledger.connect() as db:
        row = db.execute("SELECT decision_json,result_json FROM forest_study_work WHERE campaign_id=? AND work_id=?",
                         (runtime.campaign_id, work_id)).fetchone()
        calls = db.execute("SELECT request_id,status FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? ORDER BY recorded_at,request_id",
                           (runtime.campaign_id, work_id)).fetchall()
    result = json.loads(row["result_json"] or "{}") if row else {}
    return {"last_actual_decision": json.loads(row["decision_json"]) if row and row["decision_json"] else None,
            "actual_action_history": result.get("action_history", []),
            "model_response_records": [dict(call) for call in calls]}


def _public_objective_history(runtime, payload, history):
    """Keep full prior decisions retrievable without repeating transcripts in every prompt."""
    public_history = deepcopy(history)
    memory = runtime._public_memory(payload)
    for index, revision in enumerate(public_history):
        if not isinstance(revision, dict) or "actual_decision_history" not in revision:
            continue
        decision_history = revision.pop("actual_decision_history")
        version = (revision.get("objective") or {}).get("version", index + 1)
        segment = memory.save_json(
            f"end-to-end-decision-history-v{version}", decision_history,
            origin=f"End-to-end episode {payload['work_id']} objective revision {version} decision history")
        revision["actual_decision_history_reference"] = {
            "segment_id": segment["segment_id"], "pointer": "",
            "characters": segment["characters"], "read_operation": "read_context_segment",
            "scientific_progress_eligible": False}
    return public_history


def _work_rows(runtime, identifiers):
    if not identifiers:
        return {}
    with runtime.ledger.connect() as db:
        rows = db.execute("SELECT * FROM forest_study_work WHERE campaign_id=? AND work_id IN (" + ",".join("?" for _ in identifiers) + ")",
                          [runtime.campaign_id, *identifiers]).fetchall()
    return {str(row["work_id"]): dict(row) for row in rows}


def _observations(runtime, state):
    from .study_runtime import _fit_attempt_record, _measured_gpu_seconds
    rows = _work_rows(runtime, [record["fit_id"] for record in state["fits"]])
    observations = []
    for record in state["fits"]:
        row = rows.get(record["fit_id"])
        if row is None:
            continue
        observation = {"work_id": record["fit_id"], "configuration_id": record["configuration_id"],
            "seed": record["seed"], "stage": record["stage"], "state": row["state"],
            "metrics": {}, "receipt_path": None, "config": record["config"]}
        attempt = _fit_attempt_record(runtime.ledger_path, runtime.campaign_id, record["fit_id"])
        output = Path(attempt["output_directory"] if attempt else record["output_directory"])
        path = output / "execution_receipt.json"
        if row["state"] == "completed":
            receipt = _read(path)
            effective = _read(output / "effective_config.json")
            if (receipt.get("status") != "SUCCEEDED" or receipt.get("work_id") != record["fit_id"]
                    or receipt.get("effective_config") != record["config"] or effective != record["config"]
                    or receipt.get("hidden_test_opened") is not False or receipt.get("formal_score_eligible") is not True):
                raise ValueError("Long-task receipt/configuration does not reconcile to its exact independent episode fit")
            observation.update(metrics=dict(receipt["validation_metrics"]), receipt_path=str(path),
                prediction_path=str(output / str(receipt["validation_prediction_file"])), effective_config_path=str(output / "effective_config.json"),
                physical_work_id=str(attempt["supervisor_work_id"] if attempt else record["fit_id"]))
        observation["fit_gpu_seconds"] = _measured_gpu_seconds(runtime.ledger_path, record["fit_id"], runtime.campaign_id, runtime.ledger_id)
        observations.append(observation)
    return observations


def _binding(state, key, value):
    if key not in state["bindings"]:
        state["bindings"][key] = {"work_id": str(uuid.uuid4()), **deepcopy(value)}
    binding = state["bindings"][key]
    # Source conditions are recorded at allocation; physical retry identities
    # and actual usage reconcile elsewhere, never rewrite a saved action.
    return deepcopy(binding)


def _fit_binding(runtime, payload, state, record):
    from .study_runtime import _atomic_json_file
    path = Path(record["configuration_path"])
    if path.is_file() and _read(path) != record["config"]:
        raise ValueError("An independent long fit's explicit full configuration was rewritten")
    if not path.is_file():
        _atomic_json_file(path, record["config"])
    return {"work_id": record["fit_id"], "kind": "fit", "action_name": "train",
        "configuration_id": record["configuration_id"], "seed": record["seed"], "phase": "formal",
        "configuration_path": str(path), "effective_config": record["config"],
        "output_directory": record["output_directory"], "fit_timeout_seconds": float(payload.get("fit_timeout_seconds", state["resource_policy"]["actual_gpu_seconds"])),
        "source_files": [str(Path(__file__).resolve()), str(Path(state["source_spec_dir"]) / "tasks.json"), str(path)]}


def _score_binding(runtime, state, observation):
    key = f"score/{observation['work_id']}/objective-{state['objective']['version']}"
    binding = _binding(state, key, {"kind": "rescore", "action_name": "score",
        "configuration_id": observation["configuration_id"], "seed": observation["seed"],
        "campaign_path": str(runtime.campaign), "config_path": observation["effective_config_path"],
        "prediction_path": observation["prediction_path"], "reference_receipt_path": observation["receipt_path"],
        "data_root": str(runtime.campaign / "data" / "researcher"),
        "output_path": str(runtime.campaign / "runs" / "end_to_end" / state["episode_id"] / "scores" / f"{observation['work_id']}-v{state['objective']['version']}.json")})
    return binding


def _checked_scores(runtime, state, observations):
    bindings = [_score_binding(runtime, state, observation) for observation in observations
                if observation["state"] == "completed"]
    rows = _work_rows(runtime, [binding["work_id"] for binding in bindings])
    checked = []
    pending = []
    for binding in bindings:
        row = rows.get(binding["work_id"])
        if row is None or row["state"] != "completed":
            pending.append(binding)
            continue
        result = json.loads(row["result_json"] or "{}")
        observation = next(value for value in observations if value["receipt_path"] == binding["reference_receipt_path"])
        actual = result.get("metrics") or result.get("validation_metrics") or {}
        if result.get("status") != "SUCCEEDED" or not isinstance(actual, Mapping):
            raise ValueError("Long-task independently rescored result is incomplete")
        for name in (state["objective"]["primary_metric"], *[value["metric"] for value in state["objective"]["side_constraints"] if value["metric"] != "fit_gpu_seconds"]):
            if name not in actual or name not in observation["metrics"] or not math.isclose(float(actual[name]), float(observation["metrics"][name]), rel_tol=1e-6, abs_tol=1e-8):
                raise ValueError("Long-task objective metric did not reconcile to an independent rescore")
        checked.append({**observation, "metrics": dict(actual), "independent_score_work_id": binding["work_id"],
                        "independent_score_path": binding["output_path"]})
    return checked, pending


def _evidence(observations):
    return [{name: value.get(name) for name in ("work_id", "configuration_id", "seed", "receipt_path", "config", "effective_config_path", "fit_gpu_seconds", "independent_score_path", "independent_score_work_id")}
            for value in observations]


def build_end_to_end_episode_request(runtime, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the current actual stage for either method, with identical tools."""
    payload = deepcopy(dict(payload))
    nested = payload.get("episode") or {}
    for name in ("task_id", "arm", "repeat", "category"):
        if name not in payload and name in nested:
            payload[name] = nested[name]
    work_id = str(payload.get("work_id") or nested.get("episode_id") or "")
    payload["work_id"] = work_id
    if not work_id or not is_end_to_end(payload) or payload.get("arm") not in {"FULL", "AIDE"}:
        raise ValueError("From-scratch work needs an explicit LIVE_END_TO_END cell identity and FULL/AIDE arm")
    task = _registered_task(runtime, payload)
    state = _load(runtime, work_id)
    if state is None:
        state = _initial_state(runtime, payload, task)
        _save(runtime, work_id, state, ("end_to_end_initialized", {"exploration_order": state["exploration_order"],
            "exploration_seed": state["exploration_seed"], "confirmation_seeds": state["confirmation_seeds"]}))
    observations = _observations(runtime, state)
    explored = [value for value in observations if value["stage"] == "exploration" and value["state"] == "completed"]
    if len({value["configuration_id"] for value in explored}) >= 2 and state["objective"]["version"] == 1:
        state["objective_history"].append({"objective": deepcopy(state["objective"]),
            "actual_completed_exploration": deepcopy(explored), "prior_comparison_records": deepcopy(state["comparison_records"]),
            "actual_decision_history": _episode_history(runtime, work_id)})
        state["objective"] = _contract(state["task_definition"], True)
        _save(runtime, work_id, state, ("end_to_end_objective_revised", {"objective": state["objective"],
            "trigger_work_ids": [value["work_id"] for value in explored]}))
    checked, score_actions = _checked_scores(runtime, state, observations)
    checked_exploration = [value for value in checked if value["stage"] == "exploration"]
    bindings = list(score_actions)
    # Existing bindings remain available to reconcile saved pending decisions.
    known_rows = _work_rows(runtime, [value["work_id"] for value in state["bindings"].values()])
    select_values = [(binding, known_rows.get(binding["work_id"])) for binding in state["bindings"].values()
                     if binding.get("analysis_operation") == "end_to_end_select"]
    completed_selects = [(binding, json.loads(row["result_json"] or "{}")) for binding, row in select_values
                         if row and row["state"] == "completed"]
    recorded_selection_ids = {value["selection_work_id"] for value in state["comparison_records"]}
    new_selections = [(binding, result) for binding, result in completed_selects
                      if binding["work_id"] not in recorded_selection_ids]
    for binding, result in new_selections:
        selected = result.get("selected_configuration_id")
        if selected == state["anchor_configuration_id"] or selected not in {value["configuration_id"] for value in checked_exploration}:
            raise ValueError("Long-task selection did not name an observed non-anchor under the actual objective")
        state["comparison_records"].append({"comparison_id": str(uuid.uuid4()), "version": len(state["comparison_records"]) + 1,
            "objective": deepcopy(state["objective"]), "selection_work_id": binding["work_id"],
            "anchor_configuration_id": state["anchor_configuration_id"], "selected_configuration_id": selected,
            "constraint_satisfied": result.get("constraint_satisfied"), "exploration_evidence": deepcopy(checked_exploration),
            "full_selected_configuration": next(value["config"] for value in explored if value["configuration_id"] == selected),
            "full_pair_configurations": [deepcopy(value) for value in state["fits"]
                if value["stage"] == "confirmation" and value["configuration_id"] in {selected, state["anchor_configuration_id"]}],
            "data_ids_reference": state["data_ids_reference"], "dataset_version_ids": state["dataset_version_ids"],
            "created_at": time.time()})
        state["stage"] = "confirmation"
        _save(runtime, work_id, state, ("end_to_end_comparison_recorded", state["comparison_records"][-1]))
    recorded_selection_ids = {value["selection_work_id"] for value in state["comparison_records"]}
    # Further legal exploration and selection remain reachable after a pair is
    # recorded. A new agent-requested selection creates a new comparison
    # version; completed work and prior comparison records keep their identity.
    attempted = {value["configuration_id"] for value in observations if value["stage"] == "exploration"}
    next_id = next((candidate for candidate in state["exploration_order"] if candidate not in attempted), None)
    if next_id:
        record = next(value for value in state["fits"] if value["stage"] == "exploration" and value["configuration_id"] == next_id)
        bindings.append(_fit_binding(runtime, payload, state, record))
    if state["objective"]["version"] == 2 and len(checked_exploration) >= 2 and not score_actions:
        selection_key = "select/" + "/".join(sorted(value["work_id"] for value in checked_exploration))
        selection_binding = state["bindings"].get(selection_key)
        if selection_binding is None or selection_binding["work_id"] not in recorded_selection_ids:
            bindings.append(_binding(state, selection_key,
                {"kind": "analysis", "action_name": "select", "configuration_id": task["task_id"], "seed": state["exploration_seed"],
                 "analysis_operation": "end_to_end_select", "metric": state["objective"]["primary_metric"],
                 "direction": state["objective"]["primary_direction"], "side_constraints": state["objective"]["side_constraints"],
                 "anchor_configuration_id": state["anchor_configuration_id"], "evidence": _evidence(checked_exploration),
                 "output_path": str(runtime.campaign / "runs" / "end_to_end" / work_id / f"selection-{len(checked_exploration)}.json")}))
    if state["stage"] == "confirmation":
        comparison = state["comparison_records"][-1]
        pair_ids = {comparison["anchor_configuration_id"], comparison["selected_configuration_id"]}
        all_rows = _work_rows(runtime, [record["fit_id"] for record in state["fits"]])
        for seed in state["confirmation_seeds"]:
            pair_records = [value for value in state["fits"] if value["stage"] == "confirmation" and value["seed"] == seed and value["configuration_id"] in pair_ids]
            unfinished = [record for record in pair_records if all_rows.get(record["fit_id"], {}).get("state") != "completed"]
            if unfinished:
                for record in unfinished:
                    # Failed exact fits remain visible; source-bound retry policy
                    # owns physical retry attempts and the original allowance.
                    bindings.append(_fit_binding(runtime, payload, state, record))
                break
        checked_confirmation = [value for value in checked if value["stage"] == "confirmation" and value["configuration_id"] in pair_ids]
        completed_seeds = [seed for seed in state["confirmation_seeds"] if all(any(value["seed"] == seed and value["configuration_id"] == candidate for value in checked_confirmation) for candidate in pair_ids)]
        if len(completed_seeds) == len(state["confirmation_seeds"]):
            pairs = [{"left": next(value["work_id"] for value in checked_confirmation if value["seed"] == seed and value["configuration_id"] == comparison["anchor_configuration_id"]),
                      "right": next(value["work_id"] for value in checked_confirmation if value["seed"] == seed and value["configuration_id"] == comparison["selected_configuration_id"])} for seed in completed_seeds]
            bindings.append(_binding(state, "compare/" + comparison["comparison_id"], {"kind": "analysis", "action_name": "compare",
                "configuration_id": task["task_id"], "seed": None, "analysis_operation": "end_to_end_compare",
                "metric": state["objective"]["primary_metric"], "direction": state["objective"]["primary_direction"],
                "pairs": pairs, "comparison_record": comparison, "evidence": _evidence(checked_confirmation),
                "output_path": str(runtime.campaign / "runs" / "end_to_end" / work_id / f"comparison-{comparison['comparison_id']}.json")}))
    inspect_key = "inspect/" + _json([[value["work_id"], value["state"], value["metrics"], value["fit_gpu_seconds"]]
                                     for value in observations]) + "/" + str(state["objective"]["version"])
    inspect = _binding(state, inspect_key,
        {"kind": "analysis", "action_name": "inspect", "configuration_id": task["task_id"], "seed": None,
         "analysis_operation": "inspect", "evidence": [{"work_id": value["work_id"], "configuration_id": value["configuration_id"],
            "seed": value["seed"], "state": value["state"], "metrics": value["metrics"], "receipt_path": value["receipt_path"],
            "fit_gpu_seconds": value["fit_gpu_seconds"]} for value in observations] or [{"work_id": work_id, "state": "not_run", "metrics": {}}]})
    bindings.append(inspect)
    # Retain exact previously allocated bindings for replay of a saved action;
    # advertise only the legal current frontier in the public model request.
    current = {value["work_id"]: value for value in bindings}
    retained = [value for value in state["bindings"].values() if value["work_id"] not in current]
    retained.extend(_fit_binding(runtime, payload, state, record) for record in state["fits"]
                    if record["fit_id"] in {value["work_id"] for value in observations} and record["fit_id"] not in current)
    public_actions = [{key: value.get(key) for key in ("action_name", "kind", "configuration_id", "seed", "work_id")} for value in bindings]
    schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"], "properties": {
        "mode": {"type": "string", "enum": ["action", "submit"]}, "actions": {"type": "array", "items": {"type": "object", "required": ["kind", "action_work_id"], "properties": {
            "kind": {"type": "string", "enum": sorted({value["action_name"] for value in bindings})},
            "configuration_id": {"type": ["string", "null"]}, "seed": {"type": ["integer", "null"]}, "action_work_id": {"type": "string"}}}},
        "submission": {"type": "object", "properties": {"selected_configuration_id": {"type": ["string", "null"]}, "claim": {"type": "string"}}},
        "rationale": {"type": "string"}}}
    # The editable current plan supersedes historical freeze terminology in
    # task prose. Preserve the original source definition in the durable state
    # and deliver the active comparison-record semantics to either method.
    long_contract = deepcopy(task["long_from_scratch"])
    long_contract["test_access"] = "No hidden-test targets or intermediate test results during search. Keep versioned exact comparison records before interpreting confirmation results."
    comparison_policy = "Record exact configuration versions, data/split references, metric contract and outputs for each comparison. Later research choices remain editable and receive their own comparison version; retain earlier records."
    required_answer = str(task["required_answer"]).replace("frozen pair", "versioned comparison")
    public = {"cell": {"episode_id": work_id, "category": "LIVE_END_TO_END", "task_id": task["task_id"], "arm": payload["arm"], "repeat": payload.get("repeat")},
        "stage": state["stage"], "active_objective": state["objective"],
        "objective_history": _public_objective_history(runtime, payload, state["objective_history"]),
        "comparison_records": state["comparison_records"], "comparison_record_policy": comparison_policy, "long_contract": long_contract,
        "exploration_order": state["exploration_order"], "confirmation_seeds": state["confirmation_seeds"],
        "observations": observations, "public_action_bindings": public_actions, "decision_schema": schema,
        "required_answer": required_answer, "independent_start": True,
        "resource_policy": state["resource_policy"]}
    from .study_runtime import _treatment_evidence_view
    public["evidence_view"] = _treatment_evidence_view(payload["arm"], observations,
        [{"id": value["work_id"], "kind": "independent_fit_receipt", "configuration_id": value["configuration_id"],
          "seed": value["seed"], "state": value["state"]} for value in observations],
        [{"from": value["work_id"], "to": work_id, "relation": "supports_current_comparison"} for value in checked])
    _save(runtime, work_id, state)
    return {**payload, "category": "LIVE_END_TO_END", "task_id": task["task_id"], "task_contract": {**task, **state["objective"]},
        "family": task["family_id"], "resource_policy": public["resource_policy"], "end_to_end_stage": state["stage"],
        "messages": [{"role": "system", "content": "Choose the next source-bound action for this independent from-scratch scientific episode. Every action must include the exact action_work_id from the currently advertised public action bindings. Use only actual public observations. Complete the declared reachable paired computations, retain objective/comparison history, and report null, harmful or unresolved findings honestly. A submission is independently checked against actual receipts."},
                     {"role": "user", "content": _json(public)}],
        "decision_schema": schema, "allowed_decisions": sorted({value["action_name"] for value in bindings}),
        "action_bindings": bindings + retained, "current_action_work_ids": list(current),
        "output_stage": "experiment"}


def _verified_analysis_rows(binding):
    rows = []
    for entry in binding.get("evidence", []):
        receipt = _read(entry["receipt_path"])
        effective = _read(entry["effective_config_path"])
        score = _read(entry["independent_score_path"])
        if (receipt.get("status") != "SUCCEEDED" or receipt.get("hidden_test_opened") is not False
                or receipt.get("formal_score_eligible") is not True or score.get("status") != "SUCCEEDED"
                or receipt.get("work_id") != entry["work_id"]
                or receipt.get("effective_config") != entry["config"] or effective != entry["config"]):
            raise ValueError("End-to-end analysis requires actual researcher-only formal receipts and independent rescoring")
        metrics = score.get("metrics") or score.get("validation_metrics") or {}
        for metric, value in metrics.items():
            if metric not in receipt["validation_metrics"] or not math.isclose(float(value), float(receipt["validation_metrics"][metric]), rel_tol=1e-6, abs_tol=1e-8):
                raise ValueError("End-to-end metric differs from its source receipt")
        rows.append({**dict(entry), "metrics": dict(metrics), "receipt": receipt})
    return rows


def _side_constraint_passes(constraint, value, anchor_value, direction):
    """Evaluate the registered absolute-drop constraint in objective direction."""
    allowed = constraint.get("allowed_direction")
    margin = float(constraint["margin"])
    value, anchor_value = float(value), float(anchor_value)
    if not math.isfinite(value) or not math.isfinite(anchor_value) or not math.isfinite(margin) or margin < 0:
        return False
    if allowed == "at_most_anchor_times":
        return value <= margin * anchor_value
    if allowed == "at_least_anchor_times":
        return value >= margin * anchor_value
    if allowed == "no_more_than_absolute_drop":
        if constraint.get("comparison", "relative_to_anchor") != "relative_to_anchor":
            raise ValueError("Absolute-drop constraints must compare with the registered anchor")
        if direction == "maximize":
            return value >= anchor_value - margin
        if direction == "minimize":
            return value <= anchor_value + margin
        raise ValueError("Absolute-drop constraints need an explicit objective direction")
    raise ValueError("Unimplemented registered long-task side constraint")


def execute_end_to_end_analysis(binding):
    """The shared observed-data tool, invoked only by an agent public action."""
    rows = _verified_analysis_rows(binding)
    metric = str(binding["metric"])
    if binding["analysis_operation"] == "end_to_end_select":
        anchor = next(value for value in rows if value["configuration_id"] == binding["anchor_configuration_id"])
        candidates = [value for value in rows if value["configuration_id"] != anchor["configuration_id"] and metric in value["metrics"]]
        eligible = []
        for value in candidates:
            passes = True
            for constraint in binding.get("side_constraints", []):
                name = str(constraint["metric"])
                left = value["fit_gpu_seconds"] if name == "fit_gpu_seconds" else value["metrics"].get(name)
                right = anchor["fit_gpu_seconds"] if name == "fit_gpu_seconds" else anchor["metrics"].get(name)
                if left is None or right is None or not math.isfinite(float(left)) or not math.isfinite(float(right)):
                    passes = False
                else:
                    passes = passes and _side_constraint_passes(
                        constraint, left, right, str(binding.get("direction", "")))
            if passes:
                eligible.append(value)
        pool = eligible or candidates
        if not pool:
            raise ValueError("No actual observed non-anchor is available for long-task selection")
        sign = 1 if binding["direction"] == "minimize" else -1
        best = min(pool, key=lambda value: (sign * float(value["metrics"][metric]), value["configuration_id"]))
        result = {"status": "SUCCEEDED", "operation": "select", "metric": metric, "direction": binding["direction"],
            "selected_configuration_id": best["configuration_id"], "selected_work_id": best["work_id"],
            "selected_value": float(best["metrics"][metric]), "constraint_satisfied": bool(eligible),
            "eligible_work_ids": [value["work_id"] for value in eligible]}
    elif binding["analysis_operation"] == "end_to_end_compare":
        by_id = {value["work_id"]: value for value in rows}
        differences = [float(by_id[pair["right"]]["metrics"][metric]) - float(by_id[pair["left"]]["metrics"][metric]) for pair in binding["pairs"]]
        result = {"status": "SUCCEEDED", "operation": "compare", "metric": metric,
            "pair_count": len(differences), "paired_differences_right_minus_left": differences,
            "mean_paired_difference": sum(differences) / len(differences), "comparison_record": binding["comparison_record"]}
    else:
        raise ValueError("Unknown explicit end-to-end analysis operation")
    path = Path(binding["output_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(result) + "\n", encoding="utf-8")
    return result


def verify_end_to_end_submission(runtime, payload, history, submission):
    """Require actual independent exploration, selection and paired confirmation."""
    # Reconcile the actual ledger again; conversation/model numbers have no
    # authority and cannot turn an unrun comparison into a result.
    build_end_to_end_episode_request(runtime, payload)
    state = _load(runtime, str(payload["work_id"]))
    comparisons = state["comparison_records"]
    if not comparisons:
        return {"state": "failed", "valid": False, "reason": "missing_actual_revised_objective_comparison_record"}
    selected = comparisons[-1]["selected_configuration_id"]
    if submission.get("selected_configuration_id") != selected:
        return {"state": "failed", "valid": False, "reason": "submission_does_not_match_actual_versioned_pair"}
    observations = _observations(runtime, state)
    checked, pending_scores = _checked_scores(runtime, state, observations)
    ids = {comparisons[-1]["anchor_configuration_id"], selected}
    missing = [{"configuration_id": candidate, "seed": seed} for seed in state["confirmation_seeds"] for candidate in ids
               if not any(value["stage"] == "confirmation" and value["seed"] == seed and value["configuration_id"] == candidate for value in checked)]
    if missing:
        return {"state": "failed", "valid": False, "reason": "confirmation_pair_computations_missing", "missing": missing,
                "pending_score_work_ids": [value["work_id"] for value in pending_scores]}
    current = comparisons[-1]
    by_pair = {(value["configuration_id"], value["seed"]): value for value in checked
               if value["stage"] == "confirmation" and value["configuration_id"] in ids}
    expected_pairs = [{"left": by_pair[(current["anchor_configuration_id"], seed)]["work_id"],
                       "right": by_pair[(selected, seed)]["work_id"]} for seed in state["confirmation_seeds"]]
    expected_ids = {value for pair in expected_pairs for value in pair.values()}
    bindings = [value for value in state["bindings"].values() if value.get("analysis_operation") == "end_to_end_compare"
                and value.get("comparison_record") == current and value.get("pairs") == expected_pairs
                and value.get("metric") == state["objective"]["primary_metric"]
                and value.get("direction") == state["objective"]["primary_direction"]
                and {entry.get("work_id") for entry in value.get("evidence", [])} == expected_ids]
    rows = _work_rows(runtime, [value["work_id"] for value in bindings])
    actual = []
    expected_differences = [float(by_pair[(selected, seed)]["metrics"][state["objective"]["primary_metric"]])
                            - float(by_pair[(current["anchor_configuration_id"], seed)]["metrics"][state["objective"]["primary_metric"]])
                            for seed in state["confirmation_seeds"]]
    for binding in bindings:
        row = rows.get(binding["work_id"], {})
        if row.get("state") != "completed" or json.loads(row["payload_json"]) != binding:
            continue
        result = json.loads(row["result_json"] or "{}")
        differences = result.get("paired_differences_right_minus_left") or []
        if (result.get("status") != "SUCCEEDED" or result.get("operation") != "compare"
                or result.get("comparison_record") != current or result.get("metric") != binding["metric"]
                or result.get("pair_count") != len(expected_pairs) or len(differences) != len(expected_differences)
                or any(not math.isclose(float(left), right, rel_tol=1e-6, abs_tol=1e-8)
                       for left, right in zip(differences, expected_differences))
                or not math.isclose(float(result.get("mean_paired_difference", math.nan)),
                                    sum(expected_differences) / len(expected_differences), rel_tol=1e-6, abs_tol=1e-8)
                or _read(binding["output_path"]) != result):
            continue
        _verified_analysis_rows(binding)
        actual.append(result)
    if not actual or not str(submission.get("claim", "")).strip():
        return {"state": "failed", "valid": False, "reason": "missing_actual_paired_comparison_or_measured_claim"}
    return {"state": "completed", "valid": True, "selected_configuration_id": selected,
            "comparison_record": comparisons[-1], "comparison": actual[-1], "claim": submission["claim"],
            "confirmation_seeds": state["confirmation_seeds"], "hidden_test_opened": False,
            "scope": state["task_definition"]["scope"]}
