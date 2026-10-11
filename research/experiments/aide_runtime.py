"""Native WecoAI AIDE search with durable FOREST scientific execution.

The upstream Agent chooses draft/debug/improve, generates code, and reviews
actual output. FOREST owns legal actions, receipts, budgets and GPU execution.
This module never launches training or invokes an external model API.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
import importlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS forest_study_aide_sessions (
 campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, state_json TEXT NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(campaign_id,work_id));
CREATE TABLE IF NOT EXISTS forest_study_aide_requests (
 campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, native_key TEXT NOT NULL,
 request_id TEXT NOT NULL UNIQUE,
 PRIMARY KEY(campaign_id,work_id,native_key));
"""
_BACKEND = ContextVar("forest_native_aide_backend", default=None)
_RNG = ContextVar("forest_native_aide_rng", default=None)
_IMPORT_LOCK = threading.Lock()
_UPSTREAM_ROOT: Path | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _query_router(*args, **kwargs):
    backend = _BACKEND.get()
    if backend is None:
        raise RuntimeError("Native AIDE model calls require a bound FOREST backend")
    return backend.query(*args, **kwargs)


class _RandomRouter:
    """Keep the upstream random calls independent in concurrent episodes."""
    def __getattr__(self, name):
        generator = _RNG.get()
        if generator is None:
            raise RuntimeError("Native AIDE search requires its durable episode RNG")
        return getattr(generator, name)


def load_upstream(root: Path):
    """Import the actual installed clone, never an AIDE-inspired replacement."""
    global _UPSTREAM_ROOT
    root = Path(root).resolve()
    required = [root / "aide" / name for name in ("agent.py", "journal.py", "interpreter.py")]
    if any(not path.is_file() for path in required):
        raise FileNotFoundError(f"Official AIDE clone is incomplete: {root}")
    with _IMPORT_LOCK:
        if _UPSTREAM_ROOT is not None and _UPSTREAM_ROOT != root:
            raise ValueError("One process cannot mix different upstream AIDE clones")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        module = importlib.import_module("aide.agent")
        if Path(module.__file__).resolve() != required[0]:
            raise ValueError("Imported AIDE Agent does not belong to the declared upstream clone")
        # Set one fail-closed router, not a per-worker global monkeypatch.
        # ContextVars resolve each call to the worker's own client and RNG.
        module.query = _query_router
        module.random = _RandomRouter()
        backend = importlib.import_module("aide.backend")
        backend.query = _query_router
        _UPSTREAM_ROOT = root
    return {
        "agent": module,
        "journal": importlib.import_module("aide.journal"),
        "execution": importlib.import_module("aide.interpreter"),
        "serialize": importlib.import_module("aide.utils.serialize"),
        "metric": importlib.import_module("aide.utils.metric"),
        "config": importlib.import_module("aide.utils.config"),
        "backend_utils": importlib.import_module("aide.backend.utils"),
    }


def _validate_action_program(code: str):
    """Allow a literal public decision program, with no host/data capabilities.

    The common action executor is the only scientific execution authority.
    Native AIDE still generates, executes, debugs and improves Python nodes;
    the Python program selects the same public actions available to FULL.
    """
    if not isinstance(code, str) or len(code.encode("utf-8")) > 1024 * 1024:
        raise ValueError("AIDE action code must be a Python string below 1 MiB")
    tree = ast.parse(code)
    names = set()

    def value(expr):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, (str, int, float, bool, type(None))):
            if isinstance(expr.value, float) and not math.isfinite(expr.value):
                raise ValueError("Action literals must be finite")
            return
        if isinstance(expr, (ast.List, ast.Tuple)):
            for child in expr.elts:
                value(child)
            return
        if isinstance(expr, ast.Dict):
            for key, child in zip(expr.keys, expr.values):
                if key is None:
                    raise ValueError("Dictionary unpacking is not an action literal")
                value(key)
                value(child)
            return
        if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.USub, ast.UAdd)):
            if isinstance(expr.operand, ast.Constant) and isinstance(expr.operand.value, (int, float)):
                value(expr.operand)
                return
        if isinstance(expr, ast.Name) and expr.id in names:
            return
        if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                and isinstance(expr.func.value, ast.Name) and expr.func.value.id == "json"
                and expr.func.attr == "dumps" and len(expr.args) == 1 and not expr.keywords):
            value(expr.args[0])
            return
        raise ValueError("Native AIDE action programs can only construct public literal decisions")

    prints = 0
    for statement in tree.body:
        if isinstance(statement, ast.Import) and len(statement.names) == 1:
            alias = statement.names[0]
            if alias.name == "json" and alias.asname is None:
                continue
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id not in {"json", "print"} and not target.id.startswith("_"):
                value(statement.value)
                names.add(target.id)
                continue
        if isinstance(statement, ast.Expr):
            expr = statement.value
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                continue  # ordinary module docstring
            if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
                    and expr.func.id == "print" and len(expr.args) == 1 and not expr.keywords):
                value(expr.args[0])
                prints += 1
                continue
        raise ValueError("AIDE action program contains an operation outside its public tool capacity")
    if prints != 1:
        raise ValueError("AIDE action program must print exactly one public decision JSON object")


def run_action_program(code: str, *, python: str | None = None, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Execute validated native Python on CPU; this is not a scientific result.

    Errors are real interpreter failures for native AIDE debugging. Neither
    emitted metric values nor program output are accepted as measurements.
    """
    started = time.monotonic()
    try:
        _validate_action_program(code)
        with tempfile.TemporaryDirectory(prefix="forest-aide-public-action-") as directory:
            script = Path(directory) / "node.py"
            script.write_text(code, encoding="utf-8")
            result = subprocess.run([python or sys.executable, "-I", "-S", str(script)],
                cwd=directory, env={key: value for key, value in os.environ.items()
                                    if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}},
                capture_output=True, text=True, timeout=float(timeout_seconds))
        if result.returncode:
            return {"decision": None, "stdout": result.stdout + result.stderr,
                    "exec_time": time.monotonic() - started, "exc_type": "ActionProgramError"}
        decision = json.loads(result.stdout)
        if not isinstance(decision, dict):
            raise ValueError("Native action program emitted a non-object decision")
        return {"decision": decision, "stdout": result.stdout, "stderr": result.stderr,
                "exec_time": time.monotonic() - started, "exc_type": None}
    except (ValueError, SyntaxError, OSError, subprocess.TimeoutExpired) as exc:
        return {"decision": None, "stdout": f"{type(exc).__name__}: {exc}\n",
                "exec_time": time.monotonic() - started, "exc_type": type(exc).__name__}


class NativeBackend:
    """Record every real native query in the original model-call ledger."""
    def __init__(self, runtime, item, client, upstream, node_id, phase):
        self.runtime, self.item, self.client, self.upstream = runtime, item, client, upstream
        self.node_id, self.phase, self.index = node_id, phase, 0
        provider = client.provider
        registered = item.get("payload", {}).get("model_provider") or runtime.manifest.get("model_provider") or {}
        effort = (registered.get("config") or {}).get("reasoning_effort")
        if (registered.get("kind") != "codex_cli" or not registered.get("model") or not effort
                or any(provider.get(name) != registered.get(name) for name in ("id", "kind", "model"))
                or bool(provider.get("allow_paid", False)) != bool(registered.get("allow_paid", False))
                or client.config.get("reasoning_effort") != effort):
            raise ValueError("Native AIDE requires the original registered local Codex provider and explicit reasoning effort")

    def query(self, system_message=None, user_message=None, model=None,
              temperature=None, max_tokens=None, func_spec=None, **model_kwargs):
        if model != self.client.provider["model"] or model_kwargs:
            raise ValueError("Native AIDE attempted to change the registered inference model or settings")
        key = f"{self.node_id}/{self.phase}/{self.index}"
        self.index += 1
        compile_prompt = self.upstream["backend_utils"].compile_prompt_to_md
        messages = []
        if system_message is not None:
            messages.append({"role": "system", "content": compile_prompt(system_message)})
        if user_message is not None:
            messages.append({"role": "user", "content": compile_prompt(user_message)})
        schema = deepcopy(func_spec.json_schema) if func_spec is not None else None
        if schema is not None:
            # Upstream submit_review documents null for a failed run, but its
            # original schema says number. Preserve that original schema in the
            # record and make only its documented null response representable.
            if "metric" in schema.get("properties", {}):
                schema["properties"]["metric"]["type"] = ["number", "null"]
            messages.append({"role": "user", "content": "Return only the requested JSON object.\n" + _json(schema)})
        from .study_runtime import _assemble_model_messages
        delivered = _assemble_model_messages(self.client, messages, "experiment")
        client = copy(self.client)
        client.config = dict(self.client.config)
        if schema is not None:
            client.config["_forest_action_schema"] = schema
        else:
            client.config.pop("_forest_action_schema", None)
        from research.agents.output_budget import output_allocation
        allocation = output_allocation(client.config, delivered, stage="experiment", requested=max_tokens)
        maximum = int(allocation["tokens"])
        explicit = self.item["payload"].get("max_output_tokens")
        if explicit is not None:
            maximum = min(maximum, int(explicit))
        if maximum < 1:
            raise ValueError("No registered output allowance remains for the native query")
        endpoint, payload = client.build_request(delivered, json_mode=schema is not None,
                                                max_output_tokens=maximum)
        request = {"native_key": key, "native_node_id": self.node_id, "native_phase": self.phase,
            "provider_id": client.provider.get("id"), "model": model, "reasoning_effort": client.config["reasoning_effort"],
            "provider_snapshot": deepcopy(client.provider), "effective_provider_config": deepcopy(client.config),
            "effective_timeout_seconds": getattr(client, "timeout", None),
            "requested_temperature": temperature, "temperature_supported_by_transport": False,
            "max_output_tokens": maximum, "native_system_message": system_message,
            "source_output_allocation": allocation, "native_requested_max_tokens": max_tokens,
            "registered_max_output_tokens": explicit,
            "native_user_message": user_message, "messages": delivered,
            "endpoint": endpoint, "request_payload": payload,
            "native_function_spec": func_spec.to_dict() if func_spec is not None else None,
            "response_schema": schema}
        ledger, campaign, work = self.runtime.ledger, self.runtime.campaign_id, str(self.item["work_id"])
        with ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT c.* FROM forest_study_aide_requests a JOIN forest_study_model_calls c ON c.request_id=a.request_id WHERE a.campaign_id=? AND a.work_id=? AND a.native_key=?",
                             (campaign, work, key)).fetchone()
            if row:
                saved = json.loads(row["request_json"])
                # A completed request keeps its actual earlier timeout. Resume
                # can have a smaller remaining allowance but consumes no new
                # inference; source messages/model/payload must still match.
                dynamic = {"effective_timeout_seconds", "max_output_tokens"}
                comparison = {name: value for name, value in request.items() if name not in dynamic}
                previous = {name: value for name, value in saved.items() if name not in dynamic}
                if previous != comparison:
                    raise ValueError("A native request identity was reused with different full messages/settings")
                if row["status"] != "completed" or not row["response_json"]:
                    raise RuntimeError("Unsettled native model request requires reconciliation; it cannot be repeated")
                response = json.loads(row["response_json"])
                return self._output(response, schema)
            # Replaying an already completed response consumes no model time.
            # Only a fresh external effect needs a new remaining-time bound.
            if callable(getattr(self.runtime, "_budgeted_episode_client", None)):
                bounded = self.runtime._budgeted_episode_client(self.item, client)
                client = bounded
                request["effective_timeout_seconds"] = getattr(client, "timeout", None)
            if self.item.get("kind") == "replay":
                # Per-cell model quotas are nullable. Continue to use the
                # provider's adaptive allocation and persist actual usage.
                pass
            request_id = str(uuid.uuid4())
            started_at = time.time()
            db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,status,recorded_at,started_at) VALUES(?,?,?,?,?,?,?)",
                (request_id, campaign, work, _json(request), "requested", started_at, started_at))
            db.execute("INSERT INTO forest_study_aide_requests VALUES(?,?,?,?)", (campaign, work, key, request_id))
        started = time.monotonic()
        try:
            response = client.complete(delivered, json_mode=schema is not None,
                                       max_output_tokens=maximum, output_stage="experiment")
            with ledger.connect() as db:
                db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                    (_json(response), _json(response.get("usage") or {}), time.time(), time.monotonic()-started, request_id))
            ledger.event(campaign, "aide_model_response", {"request_id": request_id, "native_key": key,
                         "native_node_id": self.node_id, "phase": self.phase}, work)
            return self._output(response, schema)
        except Exception as exc:
            with ledger.connect() as db:
                db.execute("UPDATE forest_study_model_calls SET status=CASE WHEN status='requested' THEN 'failed' ELSE status END,finished_at=?,elapsed_seconds=? WHERE request_id=?",
                           (time.time(), time.monotonic()-started, request_id))
            ledger.event(campaign, "aide_model_failure", {"request_id": request_id,
                         "native_key": key, "error": f"{type(exc).__name__}: {exc}"}, work)
            raise

    @staticmethod
    def _output(response, schema):
        text = response.get("text")
        if not isinstance(text, str):
            raise ValueError("Native AIDE response contains no completed text")
        if schema is None:
            return text
        output = json.loads(text)
        import jsonschema
        jsonschema.validate(output, schema)
        return output


@contextmanager
def _bound_backend(backend, rng):
    backend_token = _BACKEND.set(backend)
    rng_token = _RNG.set(rng)
    try:
        yield
    finally:
        _RNG.reset(rng_token)
        _BACKEND.reset(backend_token)


def _tuple_tree(value):
    return tuple(_tuple_tree(child) for child in value) if isinstance(value, list) else value


def _node_record(node):
    fields = ("code", "plan", "id", "ctime", "step", "_term_out", "exec_time", "exc_type",
              "exc_info", "exc_stack", "analysis", "is_buggy")
    return {**{field: getattr(node, field) for field in fields},
            "parent_id": node.parent.id if node.parent is not None else None,
            "metric": node.metric.to_dict() if node.metric is not None else None}


def _restore_node(record, journal, upstream):
    parent = next((node for node in journal.nodes if node.id == record["parent_id"]), None)
    if record["parent_id"] is not None and parent is None:
        raise ValueError("Saved native node lost its actual journal parent")
    fields = {key: value for key, value in record.items() if key not in {"parent_id", "metric"}}
    node = upstream["journal"].Node(**fields, parent=parent)
    if record["metric"] is not None:
        node.metric = upstream["metric"].MetricValue.from_dict(record["metric"])
    return node


def _save(runtime, work_id, state, *, event_type=None, event_payload=None):
    with runtime.ledger.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO forest_study_aide_sessions VALUES(?,?,?,?) ON CONFLICT(campaign_id,work_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                   (runtime.campaign_id, work_id, _json(state), time.time()))
        if event_type is not None:
            db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), runtime.campaign_id, work_id, event_type,
                 _json(event_payload or {}), time.time()))


def _budget(runtime, item, state):
    policy = item["payload"].get("resource_policy") or {}
    required = ("active_wall_seconds", "actual_gpu_seconds")
    if any(key not in policy or (policy[key] is not None and
            (not isinstance(policy[key], (int, float)) or policy[key] < 0)) for key in required):
        raise ValueError("Native AIDE needs an explicit resource policy; null disables that ceiling")
    if hasattr(runtime, "episode_budget_status"):
        status = runtime.episode_budget_status(item)
        measured = dict(status.get("used", status))
    else:
        # Native inference and public CPU programs are measured locally; actual
        # fit allocations are read from the original supervisor's usage rows.
        measured = {"active_wall_seconds": float(state.get("local_active_seconds", 0)), "actual_gpu_seconds": 0.0}
        with runtime.ledger.connect() as db:
            actions = db.execute("SELECT target_id FROM forest_study_edges WHERE campaign_id=? AND source_id=? AND relation='derived_from'",
                                 (runtime.campaign_id, item["work_id"])).fetchall()
            ids = [str(row[0]) for row in actions]
            if ids:
                slots = ",".join("?" for _ in ids)
                physical = db.execute(f"SELECT supervisor_work_id FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id IN ({slots})",
                                      [runtime.campaign_id, *ids]).fetchall()
                ids = list(set(ids + [str(row[0]) for row in physical]))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_compute_usage'").fetchone():
                    slots = ",".join("?" for _ in ids)
                    measured["actual_gpu_seconds"] = float(db.execute(f"SELECT COALESCE(SUM(quantity),0)/1000.0 FROM forest_compute_usage WHERE ledger_id=? AND resource_type='gpu_allocated_ms' AND work_id IN ({slots})",
                                                [runtime.ledger_id, *ids]).fetchone()[0])
    if any(key not in measured or not math.isfinite(float(measured[key])) or float(measured[key]) < 0 for key in required):
        raise ValueError("Episode resource accounting must supply measured finite nonnegative active and GPU seconds")
    # Cumulative active wall time is reported for analysis but is not a
    # per-cell stop condition. The declared GPU allowance remains binding.
    measured["exhausted"] = (policy["actual_gpu_seconds"] is not None and
        float(measured["actual_gpu_seconds"]) >= float(policy["actual_gpu_seconds"]))
    measured["allowance"] = dict(policy)
    return measured


def run_aide_episode(runtime, episode_item: Mapping[str, Any], *, client,
                     gpu_devices=(), defer_gpu: bool = True) -> dict[str, Any]:
    """Advance one native AIDE step, yielding as soon as GPU work is pending.

    ``runtime.execute_aide_node`` is the common action/receipt hook described
    in AIDE_RUNTIME.md. Completed steps are journal appends, not dispatches.
    There is no artificial lifetime model-step limit.
    """
    item = dict(episode_item)
    payload = deepcopy(dict(item["payload"]))
    work_id = str(item["work_id"])
    if str(payload.get("arm", payload.get("episode", {}).get("arm", ""))).upper() != "AIDE":
        raise ValueError("The native AIDE adapter only accepts an explicitly registered AIDE arm")
    if not callable(getattr(runtime, "execute_aide_node", None)):
        raise ValueError("StudyRuntime must supply the common execute_aide_node scientific action hook")
    upstream_root = payload.get("aide_upstream_root") or runtime.manifest.get("executors", {}).get("aide_upstream_root") or runtime.campaign / "executors" / "aide_upstream"
    upstream = load_upstream(Path(upstream_root))
    with runtime.ledger.connect() as db:
        db.executescript(SCHEMA)
        row = db.execute("SELECT state_json FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?",
                         (runtime.campaign_id, work_id)).fetchone()
    state = json.loads(row[0]) if row else None
    if state is not None and state.get("finished"):
        return dict(state["final_outcome"])
    if not payload.get("action_bindings"):
        payload = runtime.build_episode_request({**payload, "work_id": work_id})
    item["payload"] = payload
    if state is None:
        cfg = upstream["config"]._load_cfg(use_cli_args=False)
        native_config = dict(payload.get("aide_search") or {})
        for key in ("num_drafts", "debug_prob", "max_debug_depth"):
            if key in native_config:
                cfg.agent.search[key] = native_config[key]
        cfg.agent.code.model = cfg.agent.feedback.model = client.provider["model"]
        cfg.agent.k_fold_validation = 1
        cfg.agent.data_preview = False
        cfg.workspace_dir = str(runtime.campaign / "runs" / "episodes" / work_id / "aide")
        task_desc = {"Registered scientific task and public observations": _json(payload.get("messages", [])),
            "Public action program contract": (
                "Write a self-contained Python program that prints exactly one JSON object satisfying "
                "the supplied public decision schema. Use only import json, public literal dictionary/list "
                "construction, variable assignment, and print(json.dumps(decision)). The program requests "
                "the same source-bound scientific operations as the other method. The common executor "
                "performs training, inference and scoring and returns independently verified empirical "
                "output. Do not implement training directly, access files, call external services, invent "
                "metrics, or print a metric before that executor returns it. Draft, debug and improve "
                "your scientific action program using the actual output. A final measured answer uses "
                "mode submit and the required scientific submission fields.")}
        from omegaconf import OmegaConf
        rng = random.Random(int(payload.get("search_seed", payload.get("seed", payload.get("repeat", 0)))))
        state = {"version": 1, "upstream_root": str(Path(upstream_root).resolve()),
            "configuration": OmegaConf.to_container(cfg, resolve=True), "task_desc": task_desc,
            "native_steps_setting_is_inactive": True,
            "journal": upstream["serialize"].dumps_json(upstream["journal"].Journal()),
            "rng_state": rng.getstate(), "completed_steps": 0, "pending": None,
            "local_active_seconds": 0.0, "finished": False}
        _save(runtime, work_id, state)
    elif state["upstream_root"] != str(Path(upstream_root).resolve()):
        raise ValueError("AIDE episode cannot resume against a different native clone")
    if state.get("pending") is None:
        # A contract revision or newly available receipt reaches the next native
        # node through the same public request builder used by the other arm.
        state["task_desc"]["Registered scientific task and public observations"] = _json(payload.get("messages", []))
        _save(runtime, work_id, state)
    from omegaconf import OmegaConf
    cfg = OmegaConf.create(state["configuration"])
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    rng = random.Random()
    rng.setstate(_tuple_tree(state["rng_state"]))
    native = upstream["agent"].Agent(task_desc=state["task_desc"], cfg=cfg, journal=journal)
    native.data_preview = "Only the public registered task/evidence above is available."
    # The algorithm and all draft/debug/improve templates remain native. The
    # execution guideline reflects the common tool capacity instead of native
    # unrestricted Kaggle Python and cross-validation assumptions.
    class PublicActionAgent(type(native)):
        @property
        def _prompt_impl_guideline(self):
            return {"Implementation guideline": [
                state["task_desc"]["Public action program contract"],
                "Use the public task's exact metric, seeds, configuration bounds and resource allowance.",
                "Only measured output returned by the common scientific executor is empirical evidence."]}
    native.__class__ = PublicActionAgent
    started = time.monotonic()
    try:
        pending = state.get("pending")
        if pending is None:
            budget = _budget(runtime, item, state)
            # Persist the selected parent and RNG before any external inference.
            with _bound_backend(None, rng):
                parent = native.search_policy()
            pending = {"node_id": uuid.uuid4().hex, "parent_id": parent.id if parent else None,
                       "stage": "draft" if parent is None else "debug" if parent.is_buggy else "improve",
                       "generation_rng": rng.getstate(), "node": None, "execution": None}
            state["pending"] = pending
            state["rng_state"] = rng.getstate()
            _save(runtime, work_id, state)
        if pending["node"] is None:
            parent = next((node for node in journal.nodes if node.id == pending["parent_id"]), None)
            if pending["parent_id"] and parent is None:
                raise ValueError("Saved native search parent is missing from the journal")
            rng.setstate(_tuple_tree(pending["generation_rng"]))
            backend = NativeBackend(runtime, item, client, upstream, pending["node_id"], "generate")
            with _bound_backend(backend, rng):
                node = native._draft() if parent is None else native._debug(parent) if parent.is_buggy else native._improve(parent)
            if node.parent is not None:
                node.parent.children.remove(node)
            node.id = pending["node_id"]
            if node.parent is not None:
                node.parent.children.add(node)
            pending["node"] = _node_record(node)
            state["rng_state"] = rng.getstate()
            _save(runtime, work_id, state)
            runtime.ledger.event(runtime.campaign_id, "aide_native_node_generated", {
                "node_id": node.id, "parent_id": pending["parent_id"], "stage": pending["stage"],
                "completed_steps": len(journal)}, work_id)
        else:
            node = _restore_node(pending["node"], journal, upstream)
        execution = pending.get("execution")
        if execution is None or execution.get("state") in {"retryable", "awaiting_reconciliation", "pending", "running"}:
            execution = runtime.execute_aide_node(item, node_id=node.id, code=node.code,
                client=client, gpu_devices=gpu_devices, defer_gpu=defer_gpu)
            if str(execution.get("node_id", "")) != node.id:
                raise ValueError("Scientific execution returned a different native node identity")
            pending["execution"] = deepcopy(execution)
            _save(runtime, work_id, state)
        if execution.get("state") == "paused_no_progress":
            outcome = {"state": "paused_no_progress", "valid": False,
                "reason": execution.get("pause_reason") or execution.get("exc_info"),
                "node_id": node.id, "native_completed_steps": len(journal),
                "pending_action": False, "pending_work_ids": [],
                "action_result": execution, "action_history": list(state.get("action_history", []))}
            _save(runtime, work_id, state, event_type="aide_episode_paused_no_progress",
                  event_payload={"node_id": node.id, "execution": execution})
            return outcome
        if execution.get("state") == "budget_exhausted":
            outcome = {"state": "budget_exhausted", "valid": False,
                "reason": "Common scientific executor exhausted the registered episode allowance",
                "node_id": node.id, "native_completed_steps": len(journal),
                "pending_action": False, "pending_work_ids": [],
                "action_result": execution, "action_history": list(state.get("action_history", []))}
            state.update(finished=True, final_outcome=outcome)
            _save(runtime, work_id, state, event_type="aide_episode_budget_exhausted",
                  event_payload={"node_id": node.id, "native_completed_steps": len(journal),
                                 "execution": execution})
            return outcome
        if execution.get("state") in {"retryable", "awaiting_reconciliation", "pending", "running"}:
            pending_ids = list(map(str, execution.get("pending_work_ids", execution.get("action_work_ids", []))))
            runtime.ledger.event(runtime.campaign_id, "aide_native_step_yielded", {
                "node_id": node.id, "action_work_ids": execution.get("action_work_ids", []),
                "pending_work_ids": pending_ids, "state": execution["state"]}, work_id)
            return {"state": execution["state"], "pending_action": True,
                    "pending_work_ids": pending_ids, "node_id": node.id,
                    "native_completed_steps": len(journal), "action_result": execution,
                    "action_history": list(state.get("action_history", []))}
        stdout = execution.get("stdout", "")
        stdout = stdout if isinstance(stdout, list) else [str(stdout)]
        result = upstream["execution"].ExecutionResult(term_out=stdout,
            exec_time=float(execution.get("exec_time", 0)),
            exc_type=execution.get("exc_type") or (None if execution.get("state") == "completed" else "ScientificActionError"),
            exc_info=execution.get("exc_info"), exc_stack=execution.get("exc_stack"))
        backend = NativeBackend(runtime, item, client, upstream, node.id, "review")
        with _bound_backend(backend, rng):
            native.parse_exec_result(node, result)
        # Native feedback supplies the journal metric; independently checked
        # observations constrain it. Never substitute an oracle best metric.
        verified = execution.get("verified_metrics", [])
        if not node.is_buggy:
            matching = [record for record in verified if isinstance(record, Mapping)
                and isinstance(record.get("value"), (int, float)) and math.isfinite(float(record["value"]))
                and float(record["value"]) == float(node.metric.value)
                and record.get("maximize") is node.metric.maximize and record.get("receipt_path")]
            if not matching:
                node.is_buggy = True
                node.metric = upstream["metric"].WorstMetricValue()
                node.analysis = str(node.analysis) + "\nThe reported metric did not match an independently verified common-executor observation."
        journal.append(node)
        history = list(state.get("action_history", []))
        turn_record = {"node_id": node.id, "stage": pending["stage"],
            "actions": execution.get("action_results", []), "execution": execution}
        from .study_runtime import _episode_source_frontier
        turn_record["source_frontier"] = _episode_source_frontier(payload, state.get("action_history", []))
        if execution.get("source_frontier") is not None:
            turn_record["source_frontier"] = execution["source_frontier"]
        submission_validation = execution.get("submission_validation")
        if isinstance(submission_validation, Mapping):
            turn_record["submission"] = dict(execution.get("submission", {}))
            turn_record["submission_validation"] = {
                "valid": submission_validation.get("valid"),
                "reason": submission_validation.get("reason"),
                "source_validation_reason": submission_validation.get("source_validation_reason"),
                "source_validation_signature": submission_validation.get("source_validation_signature")}
            if submission_validation.get("no_progress_observation"):
                turn_record["no_progress_observation"] = submission_validation["no_progress_observation"]
        if execution.get("no_progress_observation"):
            turn_record["no_progress_observation"] = execution["no_progress_observation"]
        history.append(turn_record)
        state.update(journal=upstream["serialize"].dumps_json(journal), rng_state=rng.getstate(),
                     pending=None, completed_steps=len(journal), action_history=history)
        completed_event = {
            "node_id": node.id, "parent_id": pending["parent_id"], "stage": pending["stage"],
            "completed_step": node.step, "native_completed_steps": len(journal),
            "is_buggy": node.is_buggy, "metric": node.metric.to_dict()}
        submission = execution.get("submission_validation") or execution.get("submission_result")
        if isinstance(submission, Mapping):
            outcome = {"state": "completed" if submission.get("valid") is True else "retryable",
                "valid": submission.get("valid") is True, "submission_validation": dict(submission),
                "node_id": node.id, "native_completed_steps": len(journal), "pending_work_ids": [],
                "pending_action": False, "action_result": execution, "action_history": history}
            if submission.get("valid") is True:
                state.update(finished=True, final_outcome=outcome)
            _save(runtime, work_id, state, event_type="aide_native_step_completed", event_payload=completed_event)
            return outcome
        _save(runtime, work_id, state, event_type="aide_native_step_completed", event_payload=completed_event)
        return {"state": "retryable", "pending_action": False, "pending_work_ids": [],
                "node_id": node.id, "native_completed_steps": len(journal),
                "action_result": execution, "action_history": history}
    finally:
        state["local_active_seconds"] = float(state.get("local_active_seconds", 0)) + time.monotonic()-started
        _save(runtime, work_id, state)
