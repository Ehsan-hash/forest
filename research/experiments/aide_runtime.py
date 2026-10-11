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


def _prepare_aide_task_context(messages):
    """Losslessly intern repeated trees before AIDE serializes the task as prose."""
    from .study_runtime import _intern_delivered_messages
    original = deepcopy(messages)
    public_messages, content_encoding = _intern_delivered_messages(original)
    wrapped, description_encoding = _intern_delivered_messages([
        {"role": "user", "content": _json(public_messages)}])
    return original, wrapped[0]["content"], {
        "message_content": content_encoding,
        "task_description": description_encoding,
    }


def _restore_pending_task_context(state, payload):
    """Keep pending AIDE requests bound to their saved pre-upgrade task text."""
    if not isinstance(state, Mapping) or state.get("pending") is None:
        return
    if isinstance(state.get("original_task_messages"), list):
        payload["messages"] = deepcopy(state["original_task_messages"])
        payload["aide_task_context_content"] = state.get("task_context_content")
        payload["aide_task_context_encoding"] = deepcopy(state.get("task_context_encoding", []))
        return
    stored = (state.get("task_desc") or {}).get("Registered scientific task and public observations")
    if not isinstance(stored, str):
        return
    try:
        original = json.loads(stored)
    except (ValueError, TypeError):
        return
    if not isinstance(original, list):
        return
    # This is a migration annotation only. The original prompt text and all
    # saved requests stay intact; replay can use its completed response.
    payload["messages"] = deepcopy(original)
    payload["aide_task_context_content"] = stored
    payload["aide_task_context_encoding"] = []
    state["original_task_messages"] = deepcopy(original)
    state["task_context_content"] = stored
    state["task_context_encoding"] = []


def _prior_context_page_references(action_history):
    references = []
    for turn in action_history or []:
        if not isinstance(turn, Mapping):
            continue
        for action in turn.get("actions", []):
            if not isinstance(action, Mapping):
                continue
            result = action.get("result") or {}
            if not isinstance(result, Mapping) or result.get("operation") != "read_context_segment":
                continue
            references.append({key: result[key] for key in (
                "segment_id", "pointer", "offset", "next_offset", "total_characters",
                "returned_characters", "complete") if key in result})
    return references


def _add_prior_context_page_references(payload, action_history):
    marker = "Previously retrieved exact source pages (references only): "
    messages = [deepcopy(row) for row in payload.get("messages", []) if not (
        isinstance(row, Mapping) and row.get("role") == "system"
        and str(row.get("content", "")).startswith(marker))]
    references = _prior_context_page_references(action_history)
    if references:
        messages.insert(max(0, len(messages) - 1), {"role": "system",
            "content": marker + json.dumps(references, ensure_ascii=False, sort_keys=True) +
            "\nUse the same segment and pointer with next_offset to continue reading. Retrieval is observational."})
    payload["messages"] = messages


def _replace_prior_page_bodies(journal, action_history):
    """Keep complete pages in action receipts; use exact continuation refs in older prompts."""
    by_node = {str(turn.get("node_id")): turn for turn in (action_history or [])
               if isinstance(turn, Mapping) and turn.get("node_id")}
    for node in journal.nodes:
        turn = by_node.get(str(node.id))
        if not turn:
            continue
        # Work on a prompt-only journal clone. Node.term_out is deliberately
        # clipped by upstream AIDE; _term_out retains the exact original output.
        output = "".join(node._term_out or [])
        for action in turn.get("actions", []):
            result = action.get("result") if isinstance(action, Mapping) else None
            if not isinstance(result, Mapping) or result.get("operation") != "read_context_segment":
                continue
            content = result.get("content")
            if not isinstance(content, str):
                continue
            reference = {key: result[key] for key in (
                "segment_id", "pointer", "offset", "next_offset", "total_characters",
                "returned_characters", "complete") if key in result}
            reference_text = "[Exact source page retained in the episode receipt; retrieve it by reference: " + \
                json.dumps(reference, ensure_ascii=False, sort_keys=True) + "]"
            output = output.replace(json.dumps(content, ensure_ascii=False),
                                    json.dumps(reference_text, ensure_ascii=False))
        node._term_out = [output]


def _journal_with_prompt_page_refs(journal, action_history, upstream):
    """Build a disposable native prompt journal without mutating durable receipts."""
    clone = upstream["serialize"].loads_json(
        upstream["serialize"].dumps_json(journal), upstream["journal"].Journal)
    _replace_prior_page_bodies(clone, action_history)
    return clone


def _current_page_from_stdout(stdout):
    """Return the exact current read page embedded in the common action result."""
    for line in reversed(str(stdout).splitlines()):
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        for action in reversed(payload.get("observed_action_results", [])):
            if not isinstance(action, Mapping):
                continue
            result = action.get("result")
            if isinstance(result, Mapping) and result.get("operation") == "read_context_segment":
                if result.get("delivery_status") == "not_delivered":
                    return None
                content = result.get("content")
                if isinstance(content, str):
                    return {key: result.get(key) for key in (
                        "segment_id", "pointer", "offset", "next_offset", "total_characters",
                        "returned_characters", "requested_limit", "complete") if key in result} | {
                            "content": content}
    return None


def _review_task_description(native, stdout):
    """Add the complete latest retrieval to this review only; never persist it in AIDE state."""
    task_desc = deepcopy(native.task_desc)
    page = _current_page_from_stdout(stdout)
    if page is not None:
        task_desc["Exact source page retrieved by this action"] = json.dumps(
            page, ensure_ascii=False, sort_keys=True)
    else:
        for line in reversed(str(stdout).splitlines()):
            try:
                rendered = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(rendered, Mapping):
                continue
            undelivered = next((action.get("result") for action in reversed(
                rendered.get("observed_action_results", [])) if isinstance(action, Mapping)
                and isinstance(action.get("result"), Mapping)
                and action["result"].get("delivery_status") == "not_delivered"), None)
            if isinstance(undelivered, Mapping):
                task_desc["Exact source page delivery status"] = {
                    key: undelivered.get(key) for key in ("segment_id", "pointer", "offset",
                        "next_offset", "total_characters", "delivery_status", "delivery_reason",
                        "response_capacity", "minimum_response_characters") if key in undelivered}
                break
    return task_desc


def _legacy_native_prompt_equivalent(saved, current):
    """Verify an old full-context prompt matches after reversible interning."""
    from .study_context import restore_messages
    original = current.get("original_task_messages")
    if not isinstance(original, list):
        return False
    source_text = _json(original)
    saved_messages = saved.get("messages")
    current_messages = current.get("original_messages", current.get("messages"))
    task_context = current.get("task_context_content")
    if not isinstance(saved_messages, list) or not isinstance(current_messages, list) or not isinstance(task_context, str):
        return False

    def replace_exact(messages, needles):
        rendered = deepcopy(messages)
        replacements = 0
        for message in rendered:
            if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
                continue
            content = message["content"]
            for needle in sorted((value for value in needles if isinstance(value, str) and value),
                                 key=len, reverse=True):
                if needle in content:
                    content = content.replace(needle, "<EXACT_REGISTERED_TASK_CONTEXT>", 1)
                    replacements += 1
                    break
            message["content"] = content
        return rendered, replacements

    # Match the whole original task snapshot. Matching a single user-message
    # substring would incorrectly accept a prompt missing newly added source
    # conditions or observations.
    old_needles = [source_text, json.dumps(source_text, ensure_ascii=False)]
    new_needles = [task_context, json.dumps(task_context, ensure_ascii=False)]
    try:
        # The outer task serialization is ordinary JSON; selected user message
        # bodies may contain their own reversible tagged documents.
        outer = restore_messages([{"role": "user", "content": task_context}])[0]["content"]
        delivered_task = json.loads(outer)
        if not isinstance(delivered_task, list) or restore_messages(delivered_task) != original:
            return False
        old_normalized, old_count = replace_exact(saved_messages, old_needles)
        new_normalized, new_count = replace_exact(current_messages, new_needles)
    except (ValueError, TypeError, RecursionError):
        return False
    return old_count > 0 and new_count > 0 and old_normalized == new_normalized


def _native_serialization_equivalent(saved, current):
    """Replay an unchanged saved native source after a representation upgrade."""
    if (not isinstance(saved.get("original_messages"), list)
            or saved["original_messages"] != current.get("original_messages")):
        return False
    # These fields derive from the delivered representation or the current
    # remaining allowance. Every original source, setting and schema must match.
    presentation = {"messages", "context_encoding", "request_payload", "source_output_allocation",
        "effective_timeout_seconds", "max_output_tokens", "transport_trace_path"}
    if ({key: value for key, value in saved.items() if key not in presentation}
            != {key: value for key, value in current.items() if key not in presentation}):
        return False
    before, after = deepcopy(saved.get("request_payload")), deepcopy(current.get("request_payload"))
    if not isinstance(before, dict) or not isinstance(after, dict):
        return before == after
    for payload in (before, after):
        payload.pop("messages", None)
        payload.pop("max_output_tokens", None)
    return before == after


def _rejected_before_generation(row):
    """Only an explicit transport rejection with no model response is replay-safe."""
    if not row or row["status"] != "failed" or row["usage_json"] not in (None, "", "{}"):
        return False
    try:
        response = json.loads(row["response_json"] or "{}")
        error = response.get("transport_error") or {}
    except (TypeError, ValueError):
        return False
    return (error.get("code") == "input_too_large" and error.get("ambiguous") is False
        and not error.get("response_id") and not error.get("partial_output"))


def _public_messages_equal_after_reference_expansion(saved_messages, current_messages, runtime, payload):
    """Compare task snapshots after resolving only registered public-memory references."""
    if saved_messages == current_messages:
        return True
    if not isinstance(saved_messages, list) or not isinstance(current_messages, list):
        return False
    marker = "Arm-public context catalog (metadata only): "
    def without_catalog(messages):
        return [deepcopy(message) for message in messages if not (
            isinstance(message, Mapping) and message.get("role") == "system"
            and str(message.get("content", "")).startswith(marker))]
    saved = without_catalog(saved_messages)
    current = without_catalog(current_messages)
    if len(saved) != len(current):
        return False
    try:
        from .study_memory import StudyMemory
        binding = next(row for row in payload.get("action_bindings", [])
            if isinstance(row, Mapping) and row.get("action_name") == "read_context_segment"
            and row.get("memory_workspace"))
        workspace = Path(str(binding["memory_workspace"])).resolve()
        if workspace.name == ".forest-context":
            workspace = workspace.parent
        memory = StudyMemory(workspace)
        for before, after in zip(saved, current):
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                return False
            if before.get("role") != after.get("role"):
                return False
            left, right = before.get("content"), after.get("content")
            if before.get("role") == "user" and isinstance(left, str) and isinstance(right, str):
                try:
                    left_value, right_value = json.loads(left), json.loads(right)
                except ValueError:
                    if left != right:
                        return False
                    continue
                old_history = left_value.get("objective_history") if isinstance(left_value, Mapping) else None
                new_history = right_value.get("objective_history") if isinstance(right_value, Mapping) else None
                if not isinstance(old_history, list) or not isinstance(new_history, list) or len(old_history) != len(new_history):
                    if left_value != right_value:
                        return False
                    continue
                expanded = deepcopy(right_value)
                for old_revision, new_revision in zip(old_history, expanded["objective_history"]):
                    reference = new_revision.pop("actual_decision_history_reference", None)
                    if reference is None:
                        continue
                    if "actual_decision_history" in old_revision:
                        page = memory.read_json(str(reference["segment_id"]),
                            pointer=str(reference.get("pointer", "")), offset=0,
                            limit=max(1, int(reference.get("characters", 0)) + 1))
                        if page.get("complete") is not True or not isinstance(page.get("content"), str):
                            return False
                        new_revision["actual_decision_history"] = json.loads(page["content"])
                if left_value != expanded:
                    return False
            elif left != right:
                return False
        return True
    except (StopIteration, OSError, ValueError, TypeError, KeyError):
        return False


def _safe_context_repack(saved, current, row, runtime, payload):
    """Permit a new native request identity only for a lossless, smaller re-encoding."""
    if not _rejected_before_generation(row):
        return False
    same_public_source = _public_messages_equal_after_reference_expansion(
        saved.get("original_task_messages"), current.get("original_task_messages"), runtime, payload)
    legacy_prompt_match = _legacy_native_prompt_equivalent(saved, current)
    if (not isinstance(saved.get("original_task_messages"), list)
            or saved.get("task_context_content") == current.get("task_context_content")
            # Legacy requests may have embedded the registered task in their
            # system message. Rebase that identity only when both the current
            # task's public evidence and the complete old/new delivered prompt
            # are independently proven equivalent. Either check alone is too
            # weak to authorize a new external inference.
            or not (same_public_source and legacy_prompt_match)):
        return False
    stable_fields = ("native_node_id", "native_phase", "provider_id", "model",
        "reasoning_effort", "provider_snapshot", "requested_temperature",
        "temperature_supported_by_transport", "native_requested_max_tokens",
        "registered_max_output_tokens", "native_function_spec", "response_schema")
    if any(saved.get(name) != current.get(name) for name in stable_fields):
        return False
    saved_config = dict(saved.get("effective_provider_config") or {})
    current_config = dict(current.get("effective_provider_config") or {})
    for private_key in ("_forest_action_schema", "_forest_transport_trace_path"):
        saved_config.pop(private_key, None)
        current_config.pop(private_key, None)
    if saved_config != current_config:
        return False
    old_payload, new_payload = saved.get("request_payload"), current.get("request_payload")
    if not isinstance(old_payload, Mapping) or not isinstance(new_payload, Mapping):
        return False
    old_size = len(json.dumps(old_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    new_size = len(json.dumps(new_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return new_size < old_size


def _pending_generation_rejection(runtime, work_id, node_id):
    with runtime.ledger.connect() as db:
        rows = db.execute("SELECT c.* FROM forest_study_aide_requests a "
            "JOIN forest_study_model_calls c ON c.request_id=a.request_id "
            "WHERE a.campaign_id=? AND a.work_id=? AND "
            "(a.native_key=? OR a.native_key LIKE ?) ORDER BY c.started_at DESC",
            (runtime.campaign_id, str(work_id), f"{node_id}/generate/0",
             f"{node_id}/generate/0/context-repack/%")).fetchall()
    return rows[0] if rows and _rejected_before_generation(rows[0]) else None


def _repack_pending_task_context(runtime, work_id, state, payload):
    """Re-encode saved task context after a proven pre-inference size rejection."""
    pending = state.get("pending") if isinstance(state, Mapping) else None
    if not isinstance(pending, Mapping) or pending.get("node") is not None:
        return False
    if _pending_generation_rejection(runtime, work_id, pending.get("node_id")) is None:
        return False
    original = state.get("original_task_messages")
    if not isinstance(original, list):
        return False
    rebuilt = payload
    from .end_to_end_runtime import is_end_to_end
    if is_end_to_end(payload):
        rebuilt = runtime.build_episode_request({**payload, "work_id": str(work_id)})
        if not _public_messages_equal_after_reference_expansion(original, rebuilt.get("messages"), runtime, rebuilt):
            return False
    source, content, encoding = _prepare_aide_task_context(rebuilt.get("messages", original))
    if content == state.get("task_context_content") or len(content) >= len(str(state.get("task_context_content", ""))):
        return False
    payload.clear()
    payload.update(rebuilt)
    state["task_desc"]["Registered scientific task and public observations"] = content
    state["original_task_messages"] = source
    state["task_context_content"] = content
    state["task_context_encoding"] = encoding
    payload["messages"] = source
    payload["aide_task_context_content"] = content
    payload["aide_task_context_encoding"] = encoding
    _save(runtime, work_id, state, event_type="aide_native_context_repacked_after_preinference_rejection",
        event_payload={"node_id": pending.get("node_id"), "source_messages_preserved": True})
    return True


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
        from .study_runtime import _assemble_model_messages, _codex_strict_schema, _intern_delivered_messages
        delivered = _assemble_model_messages(self.client, messages, "experiment")
        original_delivered = deepcopy(delivered)
        delivered, context_encoding = _intern_delivered_messages(delivered)
        client = copy(self.client)
        client.config = dict(self.client.config)
        transport_schema = _codex_strict_schema(schema) if schema is not None else None
        if schema is not None:
            client.config["_forest_action_schema"] = transport_schema
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
            "source_output_allocation": allocation, "context_encoding": context_encoding,
            "native_requested_max_tokens": max_tokens,
            "registered_max_output_tokens": explicit,
            "native_user_message": user_message, "messages": delivered,
            "original_messages": original_delivered,
            "original_task_messages": deepcopy(self.item.get("payload", {}).get("messages", [])),
            "task_context_content": self.item.get("payload", {}).get("aide_task_context_content"),
            "task_context_encoding": deepcopy(self.item.get("payload", {}).get("aide_task_context_encoding", [])),
            "endpoint": endpoint, "request_payload": payload,
            "native_function_spec": func_spec.to_dict() if func_spec is not None else None,
            "response_schema": schema, "transport_response_schema": transport_schema}
        if getattr(self, "measure_only", False):
            self.measured_wire_characters.append(len(json.dumps(payload, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"))))
            return {"is_bug": True, "summary": "measurement fixture", "metric": None,
                    "lower_is_better": True}
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
                dynamic = {"effective_timeout_seconds", "max_output_tokens", "transport_trace_path"}
                comparison = {name: value for name, value in request.items() if name not in dynamic}
                previous = {name: value for name, value in saved.items() if name not in dynamic}
                legacy_prompt_match = False
                if "original_task_messages" not in saved and "original_task_messages" in request:
                    legacy_prompt_match = _legacy_native_prompt_equivalent(saved, request)
                if legacy_prompt_match:
                    # Older AIDE requests placed the full scientific message in
                    # the prompt directly. New requests may losslessly intern
                    # repeated JSON subtrees, so compare their source evidence
                    # exactly and compare all stable non-presentation settings.
                    stable_fields = (
                        "native_key", "native_node_id", "native_phase", "provider_id", "model",
                        "reasoning_effort", "provider_snapshot",
                        "requested_temperature", "temperature_supported_by_transport",
                        "native_requested_max_tokens", "registered_max_output_tokens",
                        "native_function_spec", "response_schema",
                        "native_system_message",
                    )
                    saved_config = dict(saved.get("effective_provider_config") or {})
                    request_config = dict(request.get("effective_provider_config") or {})
                    for private_key in ("_forest_action_schema", "_forest_transport_trace_path"):
                        saved_config.pop(private_key, None)
                        request_config.pop(private_key, None)
                    legacy_prompt_match = (legacy_prompt_match and
                        all(saved.get(name) == request.get(name) for name in stable_fields) and
                        saved_config == request_config)
                if previous != comparison and not legacy_prompt_match and not _native_serialization_equivalent(saved, request):
                    if _safe_context_repack(saved, request, row, self.runtime,
                            self.item.get("payload", {})):
                        previous_request_id = str(row["request_id"])
                        key = f"{key}/context-repack/{previous_request_id}"
                        request["native_key"] = key
                        request["recovered_from_request_id"] = previous_request_id
                        row = None
                    else:
                        raise ValueError("A native request identity was reused with different full messages/settings")
                if row is None:
                    pass
                elif row["status"] != "completed" or not row["response_json"]:
                    raise RuntimeError("Unsettled native model request requires reconciliation; it cannot be repeated")
                else:
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
            from .study_runtime import _transport_trace_reference
            trace_reference = _transport_trace_reference(
                self.runtime.campaign, request_id, client.provider)
            if trace_reference:
                client.config["_forest_transport_trace_path"] = trace_reference
                request["transport_trace_path"] = trace_reference
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
            error_record = {"error_type": type(exc).__name__, "message": str(exc)}
            for name in ("code", "request_id", "response_id", "http_status", "retryable", "ambiguous",
                         "incomplete_reason", "requested_output_tokens", "partial_output",
                         "transport_diagnostics", "return_code", "transport_trace_path"):
                value = getattr(exc, name, None)
                if value is not None:
                    error_record[name] = value
            error_usage = getattr(exc, "usage", None)
            finished_at = time.time()
            elapsed_seconds = time.monotonic() - started
            with ledger.connect() as db:
                saved = db.execute("SELECT status,response_json FROM forest_study_model_calls WHERE request_id=?",
                                   (request_id,)).fetchone()
                if not (saved and saved["status"] == "completed" and saved["response_json"] is not None):
                    db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=COALESCE(?,usage_json),status='failed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                        (_json({"transport_error": error_record}), _json(error_usage) if error_usage is not None else None,
                         finished_at, elapsed_seconds, request_id))
            ledger.event(campaign, "aide_model_failure", {"request_id": request_id,
                         "native_key": key, "error": error_record}, work)
            raise

    @staticmethod
    def _output(response, schema):
        text = response.get("text")
        if not isinstance(text, str):
            raise ValueError("Native AIDE response contains no completed text")
        if schema is None:
            return text
        output = json.loads(text)
        from .study_runtime import _restore_strict_optional_nulls
        output = _restore_strict_optional_nulls(output, schema)
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


def _measure_review_wire(runtime, item, client, upstream, native, node, stdout):
    """Measure the exact native review query without recording or sending it."""
    backend = NativeBackend(runtime, item, client, upstream, node.id, "review")
    backend.measure_only = True
    backend.measured_wire_characters = []
    # parse_exec_result mutates only execution/review fields on the node. Keep
    # its parent and children references shared; copying the entire search tree
    # at every page-sizing probe would scale with all prior native work.
    node_copy = copy(node)
    node_copy._term_out = list(node._term_out or [])
    result = upstream["execution"].ExecutionResult(term_out=[str(stdout)], exec_time=0.0, exc_type=None,
        exc_info=None, exc_stack=None)
    saved_task_desc = native.task_desc
    native.task_desc = _review_task_description(native, stdout)
    try:
        with _bound_backend(backend, random.Random(0)):
            native.parse_exec_result(node_copy, result)
    finally:
        native.task_desc = saved_task_desc
    if len(backend.measured_wire_characters) != 1:
        raise ValueError("Native review measurement did not produce exactly one request")
    return int(backend.measured_wire_characters[0])


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
    _restore_pending_task_context(state, payload)
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
        original_task_messages, task_context_content, task_context_encoding = _prepare_aide_task_context(
            payload.get("messages", []))
        payload["aide_task_context_content"] = task_context_content
        payload["aide_task_context_encoding"] = task_context_encoding
        task_desc = {"Public action program contract": (
                "Write a self-contained Python program that prints exactly one JSON object satisfying "
                "the supplied public decision schema. Use only import json, public literal dictionary/list "
                "construction, variable assignment, and print(json.dumps(decision)). The program requests "
                "the same source-bound scientific operations as the other method. The common executor "
                "performs training, inference and scoring and returns independently verified empirical "
                "output. Do not implement training directly, access files, call external services, invent "
                "metrics, or print a metric before that executor returns it. Draft, debug and improve "
                "your scientific action program using the actual output. A final measured answer uses "
                "mode submit and the required scientific submission fields."),
            "Registered scientific task and public observations": task_context_content}
        from omegaconf import OmegaConf
        rng = random.Random(int(payload.get("search_seed", payload.get("seed", payload.get("repeat", 0)))))
        state = {"version": 1, "upstream_root": str(Path(upstream_root).resolve()),
            "configuration": OmegaConf.to_container(cfg, resolve=True), "task_desc": task_desc,
            "original_task_messages": original_task_messages,
            "task_context_content": task_context_content,
            "task_context_encoding": task_context_encoding,
            "native_steps_setting_is_inactive": True,
            "journal": upstream["serialize"].dumps_json(upstream["journal"].Journal()),
            "rng_state": rng.getstate(), "completed_steps": 0, "pending": None,
            "local_active_seconds": 0.0, "finished": False}
        _save(runtime, work_id, state)
    elif state["upstream_root"] != str(Path(upstream_root).resolve()):
        raise ValueError("AIDE episode cannot resume against a different native clone")
    if state is not None:
        payload["aide_task_context_content"] = state.get("task_context_content")
        payload["aide_task_context_encoding"] = deepcopy(state.get("task_context_encoding", []))
    if state.get("pending") is None:
        _add_prior_context_page_references(payload, state.get("action_history", []))
        # A contract revision or newly available receipt reaches the next native
        # node through the same public request builder used by the other arm.
        original_task_messages, task_context_content, task_context_encoding = _prepare_aide_task_context(
            payload.get("messages", []))
        payload["aide_task_context_content"] = task_context_content
        payload["aide_task_context_encoding"] = task_context_encoding
        state["task_desc"]["Registered scientific task and public observations"] = task_context_content
        state["original_task_messages"] = original_task_messages
        state["task_context_content"] = task_context_content
        state["task_context_encoding"] = task_context_encoding
        _save(runtime, work_id, state)
    else:
        _repack_pending_task_context(runtime, work_id, state, payload)
    item["payload"] = payload
    from omegaconf import OmegaConf
    cfg = OmegaConf.create(state["configuration"])
    journal = upstream["serialize"].loads_json(state["journal"], upstream["journal"].Journal)
    # Reconstruct the same reference-only prompt tree for every generation
    # retry, including a completed-but-not-yet-checkpointed native request.
    # The serialized durable journal continues to retain each exact output.
    prompt_journal = _journal_with_prompt_page_refs(journal, state.get("action_history", []), upstream)
    rng = random.Random()
    rng.setstate(_tuple_tree(state["rng_state"]))
    native = upstream["agent"].Agent(task_desc=state["task_desc"], cfg=cfg, journal=prompt_journal)
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
            # Fresh native generation must see the same prompt-only page
            # references that were used to form the task context. The durable
            # journal remains untouched and is reconstructed before commit.
            parent = next((node for node in prompt_journal.nodes if node.id == pending["parent_id"]), None)
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
                client=client, gpu_devices=gpu_devices, defer_gpu=defer_gpu,
                context_read_measure=lambda stdout: _measure_review_wire(
                    runtime, item, client, upstream, native, node, stdout))
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
        saved_task_desc = native.task_desc
        native.task_desc = _review_task_description(native, "".join(stdout))
        try:
            with _bound_backend(backend, rng):
                native.parse_exec_result(node, result)
        finally:
            native.task_desc = saved_task_desc
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
        # The working Agent sees a prompt-only journal with old page bodies
        # replaced by exact references. Keep source-bound stdout byte-for-byte
        # in the durable journal used for recovery and audit.
        durable_node = _restore_node(_node_record(node), journal, upstream)
        journal.append(durable_node)
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
            "completed_step": durable_node.step, "native_completed_steps": len(journal),
            "is_buggy": durable_node.is_buggy, "metric": durable_node.metric.to_dict()}
        submission = execution.get("submission_validation") or execution.get("submission_result")
        if isinstance(submission, Mapping):
            outcome = {"state": "completed" if submission.get("valid") is True else "retryable",
                "valid": submission.get("valid") is True, "submission_validation": dict(submission),
                "node_id": durable_node.id, "native_completed_steps": len(journal), "pending_work_ids": [],
                "pending_action": False, "action_result": execution, "action_history": history}
            if submission.get("valid") is True:
                state.update(finished=True, final_outcome=outcome)
            _save(runtime, work_id, state, event_type="aide_native_step_completed", event_payload=completed_event)
            return outcome
        _save(runtime, work_id, state, event_type="aide_native_step_completed", event_payload=completed_event)
        return {"state": "retryable", "pending_action": False, "pending_work_ids": [],
                "node_id": durable_node.id, "native_completed_steps": len(journal),
                "action_result": execution, "action_history": history}
    finally:
        state["local_active_seconds"] = float(state.get("local_active_seconds", 0)) + time.monotonic()-started
        _save(runtime, work_id, state)
