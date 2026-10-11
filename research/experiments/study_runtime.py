"""Durable, source-bound runner for the registered FOREST study.

This module registers explicit work from ``study_execution_manifest.json`` in
the existing study ledger, dispatches actual model requests through a saved
local Codex provider, and submits fit processes to ``ComputeSupervisor``. It
does not invent missing work, infer outcomes from agent labels, or access
evaluator/hidden data.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from urllib.parse import quote
from typing import Any, Mapping, Sequence


SCHEMA = """
CREATE TABLE IF NOT EXISTS forest_study_campaigns (
  campaign_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL, manifest_id TEXT NOT NULL,
  manifest_version INTEGER NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_work (
  work_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, stage TEXT NOT NULL,
  kind TEXT NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL,
  lease_owner TEXT, lease_at REAL, supervisor_work_id TEXT,
  decision_json TEXT, result_json TEXT, error TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_edges (
  campaign_id TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
  relation TEXT NOT NULL, PRIMARY KEY(campaign_id,source_id,target_id,relation));
CREATE TABLE IF NOT EXISTS forest_study_events (
  event_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, work_id TEXT,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_model_calls (
  request_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, work_id TEXT NOT NULL,
  request_json TEXT NOT NULL, response_json TEXT, usage_json TEXT,
  status TEXT NOT NULL, recorded_at REAL NOT NULL, started_at REAL,
  finished_at REAL, elapsed_seconds REAL);
CREATE TABLE IF NOT EXISTS forest_study_replay_requests (
  physical_request_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, work_id TEXT NOT NULL,
  status TEXT NOT NULL, request_json TEXT NOT NULL, usage_json TEXT,
  started_at REAL NOT NULL, finished_at REAL, elapsed_seconds REAL);
CREATE TABLE IF NOT EXISTS forest_study_fit_attempts (
  campaign_id TEXT NOT NULL, logical_work_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
  supervisor_work_id TEXT NOT NULL UNIQUE, output_directory TEXT NOT NULL,
  state TEXT NOT NULL, result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY(campaign_id,logical_work_id,attempt_number));
CREATE TABLE IF NOT EXISTS forest_study_work_revisions (
  campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, revision_number INTEGER NOT NULL,
  manifest_version INTEGER NOT NULL, old_payload_json TEXT NOT NULL, new_payload_json TEXT NOT NULL,
  old_inbound_edges_json TEXT NOT NULL, new_inbound_edges_json TEXT NOT NULL,
  recorded_at REAL NOT NULL, PRIMARY KEY(campaign_id,work_id,revision_number));
CREATE TABLE IF NOT EXISTS forest_study_active_intervals (
  interval_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, parent_work_id TEXT NOT NULL,
          action_work_id TEXT NOT NULL, category TEXT NOT NULL, started_at REAL NOT NULL,
          finished_at REAL NOT NULL, elapsed_seconds REAL NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS forest_study_aide_programs (
  campaign_id TEXT NOT NULL, work_id TEXT NOT NULL, node_id TEXT NOT NULL,
  code TEXT NOT NULL, program_json TEXT NOT NULL, recorded_at REAL NOT NULL,
  PRIMARY KEY(campaign_id,work_id,node_id));
"""

TERMINAL = {"completed", "failed", "cancelled", "not_run", "budget_exhausted"}
BLOCKING_RELATIONS = {"requires", "requires_valid_execution", "requires_completed"}
SETTLEMENT_RELATIONS = {"requires_settled"}
SETTLED_STATES = {"completed", "failed", "cancelled", "not_run"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _transport_trace_reference(campaign: Path, request_id: str, provider: Mapping[str, Any]) -> str | None:
    """Return a durable, request-unique local Codex trace location."""
    if provider.get("kind") != "codex_cli":
        return None
    return str(Path(campaign).resolve() / "runs" / "transport_traces" / f"{request_id}.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _audit_config(value: Any) -> Any:
    """Copy provider settings for audit while omitting credential-like values."""
    sensitive = {"api_key", "access_token", "refresh_token", "auth_token", "password",
        "authorization", "secret", "credential", "client_secret", "private_key", "bearer_token"}
    if isinstance(value, Mapping):
        return {str(key): ("<redacted>" if str(key).lower() in sensitive
                           or str(key).lower().endswith(("_secret", "_password", "_credential", "_api_key", "_access_token", "_refresh_token"))
                           else _audit_config(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_audit_config(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _validate_episode_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate the small JSON-schema subset emitted by the study adapter."""
    if not isinstance(schema, Mapping):
        raise ValueError("Decision schema must be an object")
    expected = schema.get("type")
    valid = {
        "object": lambda item: isinstance(item, Mapping),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(valid.get(kind, lambda _: False)(value) for kind in expected_types):
        raise ValueError(f"Decision schema type mismatch at {path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Decision schema enum mismatch at {path}")
    if isinstance(value, Mapping):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ValueError(f"Decision schema missing required fields at {path}: {missing}")
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                _validate_episode_schema(value[key], child, f"{path}.{key}")
    elif isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate_episode_schema(item, schema["items"], f"{path}[{index}]")


def _codex_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Make a private strict-output copy while leaving FOREST's schema intact."""
    def infer_type(node):
        values = node.get("enum") if isinstance(node, Mapping) else None
        if isinstance(values, list) and values:
            value = next((entry for entry in values if entry is not None), values[0])
            if isinstance(value, bool): return "boolean"
            if isinstance(value, int): return "integer"
            if isinstance(value, (int, float)): return "number"
            if isinstance(value, str): return "string"
        if isinstance(node, Mapping) and "properties" in node: return "object"
        return None

    def normalize(node, *, optional=False, root=False):
        if not isinstance(node, Mapping):
            raise ValueError("Structured-output schema nodes must be JSON objects")
        result = copy.deepcopy(dict(node))
        if "type" not in result:
            inferred = infer_type(result)
            if inferred:
                result["type"] = inferred
        kind = result.get("type")
        if kind == "object" or "properties" in result:
            properties = result.get("properties") or {}
            if not isinstance(properties, Mapping):
                raise ValueError("Structured-output object properties must be an object")
            originally_required = set(result.get("required", []))
            normalized = {}
            for key, child in properties.items():
                normalized[key] = normalize(child, optional=(key not in originally_required))
            result["type"] = "object"
            result["properties"] = normalized
            result["required"] = list(properties)
            result["additionalProperties"] = False
        elif kind == "array" and isinstance(result.get("items"), Mapping):
            result["items"] = normalize(result["items"])
        if optional:
            kind = result.get("type")
            if isinstance(kind, str):
                result["type"] = [kind, "null"]
            elif isinstance(kind, list) and "null" not in kind:
                result["type"] = [*kind, "null"]
            elif kind is None:
                raise ValueError("An optional structured-output field needs a type")
            if isinstance(result.get("enum"), list) and None not in result["enum"]:
                result["enum"] = [*result["enum"], None]
        return result

    if not isinstance(schema, Mapping):
        raise ValueError("Structured-output schema must be an object")
    return normalize(schema, root=True)


def _restore_strict_optional_nulls(value: Any, schema: Mapping[str, Any]) -> Any:
    """Map strict-output null placeholders back to source-schema omissions."""
    if isinstance(value, Mapping) and isinstance(schema.get("properties"), Mapping):
        result = copy.deepcopy(dict(value))
        required = set(schema.get("required", []))
        for key, child in schema["properties"].items():
            if key not in result:
                continue
            allows_null = isinstance(child, Mapping) and (
                child.get("type") == "null" or
                isinstance(child.get("type"), list) and "null" in child["type"] or
                isinstance(child.get("enum"), list) and None in child["enum"])
            if key not in required and result[key] is None and not allows_null:
                result.pop(key)
            else:
                result[key] = _restore_strict_optional_nulls(result[key], child)
        return result
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        return [_restore_strict_optional_nulls(item, schema["items"]) for item in value]
    return copy.deepcopy(value)


def _intern_delivered_messages(messages: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Losslessly intern repeated subtrees in large structured user messages."""
    from .study_context import intern_json_with_stats
    delivered = copy.deepcopy([dict(message) for message in messages])
    records = []
    for index, message in enumerate(delivered):
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        decoder = json.JSONDecoder()
        starts = {0}
        for match in re.finditer(r"\n[ \t]*[\[{]", content):
            starts.add(match.end() - 1)
        for match in re.finditer(r":[ \t]*[\[{]", content):
            starts.add(match.end() - 1)
        best = None
        consumed_until = -1
        for start in sorted(starts):
            if start < consumed_until:
                continue
            try:
                value, end = decoder.raw_decode(content[start:])
                document, stats = intern_json_with_stats(value)
            except (ValueError, TypeError, RecursionError):
                continue
            consumed_until = start + end
            if stats["saved_characters"] > 0 and (best is None or stats["saved_characters"] > best[0]):
                best = (stats["saved_characters"], start, end, document, stats)
        if best is None:
            continue
        _, start, end, document, stats = best
        suffix = content[start + end:]
        introduction = ("COMPLETE LOSSLESS JSON CONTEXT DOCUMENT follows. Read its instructions and expand every reference "
            "that matters for your decision. All original keys, list order, values and source positions are preserved.\n")
        message["content"] = content[:start] + introduction + _json(document) + suffix
        records.append({"message_index": index, **stats,
            "delivered_content_characters": len(message["content"])})
    return delivered, records


def _validate_action_bindings(payload: Mapping[str, Any], actions: Sequence[Any]) -> None:
    """Check an entire proposed batch before any registered operation can run."""
    bindings = payload.get("action_bindings")
    if not isinstance(bindings, list):
        raise ValueError("The episode has no registered action binding list")
    kind_for_action = {"train": "fit", "infer": "infer", "score": "rescore",
                       "select": "analysis", "compare": "analysis", "inspect": "analysis",
                       "read_context_segment": "analysis"}
    replay = _is_replay_episode(payload)
    selected_ids: set[str] = set()
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            raise ValueError(f"Action {index} must be a JSON object")
        name = str(action.get("action", action.get("kind", "")))
        kind = "analysis" if name == "read_context_segment" else "replay_transition" if replay else kind_for_action.get(name)
        if name not in kind_for_action or kind is None or action.get("kind") not in (None, name):
            raise ValueError(f"Action {index} has an unsupported or inconsistent operation kind")
        candidates = [binding for binding in bindings if isinstance(binding, Mapping)
            and binding.get("kind") == kind
            and binding.get("action_name", name) == name
            and (action.get("action_work_id") is None or str(binding.get("work_id")) == str(action.get("action_work_id")))
            and (action.get("configuration_id") is None or binding.get("configuration_id") == action.get("configuration_id"))
            and (action.get("seed") is None or binding.get("seed") == action.get("seed"))]
        if len(candidates) != 1:
            raise ValueError(f"Action {index} must identify exactly one registered binding")
        binding_id = str(candidates[0].get("work_id", ""))
        if not binding_id:
            raise ValueError(f"Registered binding for action {index} has no work ID")
        if binding_id in selected_ids:
            raise ValueError(f"Action batch repeats registered work ID {binding_id}")
        selected_ids.add(binding_id)


class StudyLedger:
    """Campaign records in the original SQLite ledger; legacy rows are untouched."""

    def __init__(self, path: Path, ledger_id: str):
        self.path = Path(path).resolve()
        self.ledger_id = str(ledger_id)
        if not self.ledger_id:
            raise ValueError("An explicit existing ledger ID is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            existing = {row[1] for row in db.execute("PRAGMA table_info(forest_study_model_calls)")}
            for name, kind in (("started_at", "REAL"), ("finished_at", "REAL"), ("elapsed_seconds", "REAL")):
                if name not in existing:
                    db.execute(f"ALTER TABLE forest_study_model_calls ADD COLUMN {name} {kind}")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def event(self, campaign_id: str, event_type: str, payload: Mapping[str, Any], work_id: str | None = None):
        with self.connect() as db:
            db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                       (str(uuid.uuid4()), campaign_id, work_id, event_type, _json(dict(payload)), time.time()))

    def register_action(self, campaign_id: str, work_id: str, payload: Mapping[str, Any], *, parent_id: str):
        """Persist an explicitly declared action before its external process effect."""
        now = time.time()
        value = dict(payload)
        kind = str(value.get("kind", ""))
        if kind not in {"fit", "infer", "rescore", "analysis"}:
            raise ValueError(f"Unsupported bound action kind: {kind}")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM forest_study_work WHERE work_id=?", (work_id,)).fetchone()
            encoded = _json(value)
            if existing:
                if existing["campaign_id"] != campaign_id or existing["payload_json"] != encoded:
                    raise ValueError(f"Action work ID {work_id} was already used with different conditions")
            else:
                db.execute("INSERT INTO forest_study_work(work_id,campaign_id,stage,kind,payload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                           (work_id, campaign_id, str(value.get("stage", value.get("phase", ""))),
                            kind, encoded, "queued", now, now))
                db.execute("INSERT OR IGNORE INTO forest_study_edges VALUES(?,?,?,?)",
                           (campaign_id, parent_id, work_id, "derived_from"))
        self.event(campaign_id, "action_registered", {"parent_work_id": parent_id, "kind": kind}, work_id)

    def register(self, manifest: Mapping[str, Any]) -> dict[str, int]:
        campaign_id = str(manifest.get("campaign_id", ""))
        manifest_id = str(manifest.get("manifest_id", ""))
        manifest_version = int(manifest.get("manifest_version", 0))
        if not campaign_id or not manifest_id or manifest_version < 1:
            raise ValueError("Manifest must identify campaign_id, manifest_id, and positive manifest_version")
        work_items = _manifest_work_items(manifest)
        edges = _manifest_edges(manifest)
        now = time.time()
        inserted = unchanged = revised = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            campaign = db.execute("SELECT * FROM forest_study_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if campaign and campaign["ledger_id"] != self.ledger_id:
                raise ValueError("Campaign is already associated with a different original ledger ID")
            previous_manifest_version = int(campaign["manifest_version"]) if campaign else 0
            db.execute("INSERT INTO forest_study_campaigns VALUES(?,?,?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET manifest_id=excluded.manifest_id,manifest_version=excluded.manifest_version,updated_at=excluded.updated_at",
                       (campaign_id, self.ledger_id, manifest_id, manifest_version, now))
            for item in work_items:
                work_id = str(item.get("work_id") or item.get("cell_id") or item.get("id") or "")
                if not work_id:
                    # Cells are not runnable until the manifest gives them an
                    # explicit identity and complete action contract.
                    continue
                kind = str(item.get("kind", "fit" if "optimizer_updates" in item else ""))
                if kind not in {"fit", "episode", "replay", "infer", "rescore", "analysis",
                                "calibration_decision", "snapshot_stage"}:
                    raise ValueError(f"Runnable work {work_id} has unsupported or missing kind")
                stage = str(item.get("stage", item.get("phase", item.get("category", ""))))
                prior = db.execute("SELECT * FROM forest_study_work WHERE work_id=?", (work_id,)).fetchone()
                payload_json = _json(dict(item))
                if prior:
                    if prior["campaign_id"] != campaign_id or prior["payload_json"] != payload_json:
                        if (prior["state"] != "queued" or manifest_version <= previous_manifest_version
                                or self._has_execution_evidence(db, campaign_id, work_id)):
                            raise ValueError(f"Registered work ID {work_id} has changed conditions after claim/execution; issue a new work ID/revision")
                        old_edges = [tuple(row) for row in db.execute(
                            "SELECT source_id,target_id,relation FROM forest_study_edges WHERE campaign_id=? AND target_id=? ORDER BY source_id,relation",
                            (campaign_id, work_id)).fetchall()]
                        new_edges = sorted(edge for edge in edges if edge[1] == work_id)
                        revision = int(db.execute("SELECT COALESCE(MAX(revision_number),0)+1 FROM forest_study_work_revisions WHERE campaign_id=? AND work_id=?",
                            (campaign_id, work_id)).fetchone()[0])
                        db.execute("INSERT INTO forest_study_work_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                            (campaign_id, work_id, revision, manifest_version, prior["payload_json"], payload_json,
                             _json(old_edges), _json(new_edges), now))
                        db.execute("UPDATE forest_study_work SET stage=?,kind=?,payload_json=?,updated_at=? WHERE campaign_id=? AND work_id=? AND state='queued'",
                            (stage, kind, payload_json, now, campaign_id, work_id))
                        db.execute("DELETE FROM forest_study_edges WHERE campaign_id=? AND target_id=?", (campaign_id, work_id))
                        for source, target, relation in new_edges:
                            db.execute("INSERT OR IGNORE INTO forest_study_edges VALUES(?,?,?,?)",
                                (campaign_id, source, target, relation))
                        db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                            (str(uuid.uuid4()), campaign_id, work_id, "planned_work_revised",
                             _json({"revision_number": revision, "manifest_version": manifest_version,
                                    "old_inbound_edges": old_edges, "new_inbound_edges": new_edges}), now))
                        revised += 1
                    else:
                        unchanged += 1
                else:
                    db.execute("INSERT INTO forest_study_work(work_id,campaign_id,stage,kind,payload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                               (work_id, campaign_id, stage, kind, payload_json, "queued", now, now))
                    inserted += 1
            for source, target, relation in edges:
                db.execute("INSERT OR IGNORE INTO forest_study_edges VALUES(?,?,?,?)", (campaign_id, source, target, relation))
        self.event(campaign_id, "manifest_registered", {"manifest_id": manifest_id,
            "manifest_version": manifest_version, "inserted": inserted, "unchanged": unchanged, "revised": revised,
            "work_items_seen": len(work_items), "edges_seen": len(edges)})
        return {"inserted": inserted, "unchanged": unchanged, "revised": revised, "work_items_seen": len(work_items)}

    @staticmethod
    def _has_execution_evidence(db: sqlite3.Connection, campaign_id: str, work_id: str) -> bool:
        """Reject planned payload edits once a worker/provider/supervisor touched the item."""
        for table, query, params in (
            ("forest_compute_work", "SELECT 1 FROM forest_compute_work WHERE work_id=? LIMIT 1", (work_id,)),
            ("forest_study_fit_attempts", "SELECT 1 FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id=? LIMIT 1", (campaign_id, work_id)),
            ("forest_study_model_calls", "SELECT 1 FROM forest_study_model_calls WHERE campaign_id=? AND work_id=? LIMIT 1", (campaign_id, work_id)),
        ):
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists and db.execute(query, params).fetchone():
                return True
        touched = db.execute("SELECT 1 FROM forest_study_events WHERE campaign_id=? AND work_id=? AND event_type IN ('work_claimed','action_registered') LIMIT 1",
                             (campaign_id, work_id)).fetchone()
        if touched:
            return True
        return False

    def claim(self, campaign_id: str, owner: str, *, work_ids: Sequence[str] | None = None,
              kinds: Sequence[str] | None = None) -> dict[str, Any] | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM forest_study_work WHERE campaign_id=? AND state IN ('queued','retryable')"
            params: list[Any] = [campaign_id]
            if work_ids is not None:
                if not work_ids:
                    return None
                query += " AND work_id IN (" + ",".join("?" for _ in work_ids) + ")"
                params.extend(map(str, work_ids))
            if kinds is not None:
                if not kinds:
                    return None
                query += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
                params.extend(map(str, kinds))
            query += " ORDER BY created_at,work_id"
            rows = db.execute(query, params).fetchall()
            prerequisite_ids = {row[0] for row in db.execute(
                "SELECT DISTINCT source_id FROM forest_study_edges WHERE campaign_id=? AND relation IN ('requires','requires_valid_execution','requires_completed')",
                (campaign_id,))}
            kind_priority = {"fit": 0, "infer": 1, "rescore": 2, "analysis": 3,
                             "episode": 4, "replay": 5, "calibration_decision": 6,
                             "snapshot_stage": 9}
            rows = sorted(rows, key=lambda row: (0 if row["work_id"] in prerequisite_ids else 1,
                                                  kind_priority.get(row["kind"], 7), row["created_at"], row["work_id"]))
            for row in rows:
                deps = db.execute("SELECT e.relation,w.state FROM forest_study_edges e LEFT JOIN forest_study_work w ON w.work_id=e.source_id WHERE e.campaign_id=? AND e.target_id=?", (campaign_id, row["work_id"])).fetchall()
                blocked = any(
                    (d["relation"] in BLOCKING_RELATIONS and d["state"] != "completed")
                    or (d["relation"] in SETTLEMENT_RELATIONS and d["state"] not in SETTLED_STATES)
                    for d in deps)
                if blocked:
                    continue
                changed = db.execute("UPDATE forest_study_work SET state='claimed',lease_owner=?,lease_at=?,updated_at=? WHERE work_id=? AND state IN ('queued','retryable')",
                                     (owner, now, now, row["work_id"])).rowcount
                if changed:
                    db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                        (str(uuid.uuid4()), campaign_id, row["work_id"], "work_claimed",
                         _json({"owner": owner, "kind": row["kind"]}), now))
                    value = dict(row)
                    value["state"] = "claimed"
                    value["lease_owner"] = owner
                    value["lease_at"] = now
                    value["payload"] = json.loads(value.pop("payload_json"))
                    return value
        return None

    def heartbeat(self, campaign_id: str, work_id: str, owner: str) -> None:
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE forest_study_work SET lease_at=?,updated_at=? WHERE campaign_id=? AND work_id=? AND lease_owner=? AND state IN ('claimed','running')",
                (now, now, campaign_id, work_id, owner)).rowcount
        if not changed:
            raise ValueError(f"Cannot renew a lease not held by {owner}: {work_id}")

    def release_claim(self, campaign_id: str, work_id: str, owner: str, *, state: str = "queued") -> None:
        if state not in {"queued", "retryable"}:
            raise ValueError("A released claim must return to queued or retryable")
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE forest_study_work SET state=?,lease_owner=NULL,lease_at=NULL,updated_at=? WHERE campaign_id=? AND work_id=? AND lease_owner=? AND state='claimed'",
                (state, now, campaign_id, work_id, owner)).rowcount
        if not changed:
            raise ValueError(f"Cannot release a claim not held in claimed state by {owner}: {work_id}")

    def set_state(self, campaign_id: str, work_id: str, state: str, *, result=None, error=None,
                  supervisor_work_id=None, decision=None):
        if state not in TERMINAL | {"queued", "claimed", "running", "awaiting_reconciliation", "retryable", "blocked", "paused_no_progress"}:
            raise ValueError(f"Unknown study state: {state}")
        now = time.time()
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM forest_study_work WHERE campaign_id=? AND work_id=?", (campaign_id, work_id)).fetchone()
            if not row:
                raise ValueError(f"Unknown study work ID: {work_id}")
            db.execute("UPDATE forest_study_work SET state=?,result_json=COALESCE(?,result_json),error=?,supervisor_work_id=COALESCE(?,supervisor_work_id),decision_json=COALESCE(?,decision_json),updated_at=? WHERE work_id=?",
                       (state, _json(result) if result is not None else None, error, supervisor_work_id,
                        _json(decision) if decision is not None else None, now, work_id))
        self.event(campaign_id, "work_state", {"state": state, "error": error,
            "supervisor_work_id": supervisor_work_id, "result": result}, work_id)

    def resume_no_progress(self, campaign_id: str, work_id: str, *, operator: str) -> None:
        """Explicitly reopen a paused episode while retaining its prior evidence."""
        if not operator.strip():
            raise ValueError("An explicit operator identity is required to resume paused work")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,kind FROM forest_study_work WHERE campaign_id=? AND work_id=?",
                (campaign_id, work_id)).fetchone()
            if not row:
                raise ValueError(f"Unknown study work ID: {work_id}")
            if row["state"] != "paused_no_progress":
                raise ValueError(f"Study work is not paused for no progress: {work_id}")
            db.execute("UPDATE forest_study_work SET state='retryable',error=NULL,updated_at=? WHERE campaign_id=? AND work_id=?",
                (now, campaign_id, work_id))
            has_aide_sessions = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_study_aide_sessions'").fetchone()
            if row["kind"] in {"episode", "replay"} and has_aide_sessions:
                session = db.execute("SELECT state_json FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?",
                    (campaign_id, work_id)).fetchone()
                if session:
                    state = json.loads(session["state_json"])
                    pending = state.get("pending")
                    if isinstance(pending, Mapping) and (pending.get("execution") or {}).get("state") == "paused_no_progress":
                        pending = dict(pending)
                        state["pending"] = None
                        state["resume_after_no_progress"] = {"at": now, "operator": operator,
                            "previous_pending_node_id": pending.get("node_id")}
                        db.execute("UPDATE forest_study_aide_sessions SET state_json=?,updated_at=? WHERE campaign_id=? AND work_id=?",
                            (_json(state), now, campaign_id, work_id))
            db.execute("INSERT INTO forest_study_events VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), campaign_id, work_id, "no_progress_resumed",
                 _json({"operator": operator, "prior_result_preserved": True}), now))

    def counts(self, campaign_id: str) -> dict[str, int]:
        with self.connect() as db:
            return {row["state"]: row["n"] for row in db.execute(
                "SELECT state,count(*) AS n FROM forest_study_work WHERE campaign_id=? GROUP BY state", (campaign_id,))}


class EpisodeBudgetExhausted(RuntimeError):
    def __init__(self, budget: Mapping[str, Any]):
        self.budget = dict(budget)
        super().__init__(f"Episode resource allowance exhausted: {self.budget.get('exhausted', [])}")


def _manifest_work_items(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    gathered: list[dict[str, Any]] = []
    execution = manifest.get("execution_state") or {}
    for key in ("task_queue", "work_items", "pending_operations"):
        values = execution.get(key, []) if isinstance(execution, Mapping) else []
        if values:
            if not isinstance(values, list) or any(not isinstance(x, Mapping) for x in values):
                raise ValueError(f"execution_state.{key} must be a list of objects")
            for value in values:
                item = copy.deepcopy(dict(value))
                nested = item.pop("payload", None)
                if isinstance(nested, Mapping):
                    item = {**copy.deepcopy(dict(nested)), **item}
                gathered.append(item)
    for key in ("work_items", "operations"):
        values = manifest.get(key, [])
        if values:
            if not isinstance(values, list) or any(not isinstance(x, Mapping) for x in values):
                raise ValueError(f"manifest.{key} must be a list of objects")
            for value in values:
                item = copy.deepcopy(dict(value))
                nested = item.pop("payload", None)
                if isinstance(nested, Mapping):
                    item = {**copy.deepcopy(dict(nested)), **item}
                gathered.append(item)
    # Only runnable cells with explicit IDs and action payloads are registered.
    for cell in manifest.get("cells", []):
        if isinstance(cell, Mapping) and any(cell.get(k) for k in ("work_id", "cell_id", "id")) and cell.get("kind"):
            gathered.append(copy.deepcopy(dict(cell)))
    # Queue entries carry full scientific configs; normalized work_items may
    # add supervisor/action controls for those same IDs. Preserve all explicit
    # fields in one durable condition record rather than double-enqueueing.
    by_id: dict[str, dict[str, Any]] = {}
    for item in gathered:
        work_id = str(item.get("work_id") or item.get("cell_id") or item.get("id") or "")
        if not work_id:
            continue
        if work_id in by_id:
            merged = {**by_id[work_id], **item}
            for field in ("requires", "derived_from"):
                if field in by_id[work_id] or field in item:
                    merged[field] = list(dict.fromkeys([*(by_id[work_id].get(field) or []), *(item.get(field) or [])]))
            by_id[work_id] = merged
        else:
            by_id[work_id] = item
    return list(by_id.values())


def _manifest_edges(manifest: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    graph = manifest.get("graph") or {}
    raw = graph.get("edges", []) if isinstance(graph, Mapping) else []
    result = []
    for edge in raw:
        if not isinstance(edge, Mapping):
            raise ValueError("Manifest graph edges must be objects")
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        relation = str(edge.get("relation", ""))
        if source and target and relation:
            result.append((source, target, relation))
    for item in _manifest_work_items(manifest):
        work_id = str(item.get("work_id") or item.get("cell_id") or item.get("id") or "")
        for source in item.get("requires", []) or []:
            result.append((str(source), work_id, "requires"))
        for source in item.get("derived_from", []) or []:
            result.append((str(source), work_id, "derived_from"))
    return result


class StudyRuntime:
    def __init__(self, *, campaign: Path, ledger: Path, ledger_id: str, manifest: Path):
        self.campaign = Path(campaign).resolve()
        self.manifest_path = Path(manifest).resolve()
        self.manifest = _read_json(self.manifest_path)
        self.campaign_id = str(self.manifest["campaign_id"])
        expected_ledger = str(self.manifest.get("external_ledger_id", ledger_id))
        if expected_ledger != ledger_id:
            raise ValueError(f"Manifest ledger ID {expected_ledger} does not match requested existing ledger ID {ledger_id}")
        self.ledger = StudyLedger(ledger, ledger_id)
        self.registered = self.ledger.register(self.manifest)
        self.ledger_path = Path(ledger).resolve()
        self.ledger_id = ledger_id
        self._manifest_identity = self._manifest_file_identity()
        self._active_episode_started: dict[str, float] = {}
        self._snapshot_plan_cache: dict[str, tuple[tuple[int, int, int, int], dict[str, Any]]] = {}
        self._candidate_rows_cache: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

    def _manifest_file_identity(self) -> tuple[int, int, int, int]:
        info = self.manifest_path.stat()
        return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns))

    def reload_manifest(self):
        identity = self._manifest_file_identity()
        if identity == self._manifest_identity:
            return self.registered
        latest = _read_json(self.manifest_path)
        if latest["campaign_id"] != self.campaign_id:
            raise ValueError("Manifest campaign_id changed while the runner was active")
        registered = self.ledger.register(latest)
        self.manifest = latest
        self.registered = registered
        self._manifest_identity = identity
        return registered

    def build_episode_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Materialize one model episode from manifest facts and saved receipts.

        The episode item names the scientific question and treatment. This
        adapter resolves only registered task/config/data references and
        completed source artifacts; it never fills an evidence slot with a
        predicted or synthetic value.
        """
        from .end_to_end_runtime import is_end_to_end, build_end_to_end_episode_request
        if is_end_to_end(payload):
            return self._attach_public_context(payload, build_end_to_end_episode_request(self, payload))
        episode = _normalize_episode_payload(payload)
        if _is_replay_episode(episode):
            from .mechanism_replay import prepare_replay
            return self._attach_public_context(episode, prepare_replay(self, episode))
        task_id = str(episode.get("task_id", ""))
        task = next((dict(row) for row in self.manifest.get("tasks", [])
                     if isinstance(row, Mapping) and str(row.get("task_id")) == task_id), None)
        if task is None:
            raise ValueError(f"Episode task_id is not registered in the manifest: {task_id!r}")
        family = str(task.get("family_id", ""))
        arm = str(episode.get("arm", episode.get("design_arm", ""))).upper()
        stream = str(episode.get("stream", episode.get("update_type", ""))).upper()
        if arm not in {"FULL", "DAG_ID", "FLAT", "AIDE", "ID_ONLY", "RERUN_ALL", "FULL_NO_GATE"}:
            raise ValueError("Episode declares an unsupported evidence treatment")
        is_replay = _is_replay_episode(episode)
        if stream not in {"EVAL_UPDATE", "TRAIN_UPDATE", "REPLAY"}:
            raise ValueError("Episode must declare a supported stream or a registered replay case")
        mutation = _episode_mutation_id(episode)
        episode["mutation"] = mutation
        question = str(episode.get("research_question") or episode.get("objective") or
                       f"Choose and execute the next registered {stream} action for {task_id}.")

        fits = [row for row in _manifest_work_items(self.manifest)
                if row.get("kind") == "fit" and str(row.get("task_id")) == task_id]
        candidate_rows = self._episode_candidate_rows(episode, fits)
        if not candidate_rows:
            raise ValueError(f"No registered candidate configurations were supplied for task {task_id}")
        original_pair = self._select_original_pair(task, candidate_rows, episode)
        pair_rows = original_pair["rows"]
        pair_work_ids = {str(row["config"].get("work_id", "")) for row in pair_rows}

        action_bindings: list[dict[str, Any]] = []
        evidence_records: list[dict[str, Any]] = []
        inference_evidence_rows: list[dict[str, Any]] = []
        training_evidence_rows: list[dict[str, Any]] = []
        evidence_graph_nodes: list[dict[str, Any]] = []
        evaluation_rows = candidate_rows if stream == "EVAL_UPDATE" else pair_rows
        for row in candidate_rows:
            config = row["config"]
            receipt_path = row["receipt_path"]
            receipt = row["receipt"]
            if not isinstance(receipt, Mapping) and receipt_path:
                try:
                    receipt = _read_json(receipt_path)
                except (OSError, ValueError, TypeError):
                    receipt = None
            effective_config = dict(config)
            if receipt_path:
                config_path = Path(receipt_path).parent / "effective_config.json"
                if config_path.is_file():
                    try:
                        effective_config = _read_json(config_path)
                    except (OSError, ValueError, TypeError):
                        pass
            completed_updates = (receipt or {}).get("completed_updates")
            if completed_updates is None and (receipt or {}).get("status") == "SUCCEEDED":
                completed_updates = effective_config.get("optimizer_updates")
            complete_record = {"configuration_id": config["configuration_id"],
                "seed": config.get("seed"), "work_id": config.get("work_id"),
                "state": row["state"], "receipt_path": str(receipt_path) if receipt_path else None,
                "status": (receipt or {}).get("status"),
                "completed_updates": completed_updates,
                "metrics": dict((receipt or {}).get("validation_metrics") or {}),
                "fit_gpu_seconds": _measured_gpu_seconds(self.ledger_path, str(config.get("work_id", "")), self.campaign_id, self.ledger_id)
                    if config.get("work_id") else None,
                "formal_score_eligible": (receipt or {}).get("formal_score_eligible"),
                "hidden_test_opened": (receipt or {}).get("hidden_test_opened"),
                "effective_config": effective_config, "receipt": dict(receipt or {})}
            evidence_records.append(complete_record)
            evidence_graph_nodes.append({"id": str(config.get("work_id") or config["configuration_id"]),
                "kind": "fit_receipt", "task_id": task_id,
                "configuration_id": config["configuration_id"], "seed": config.get("seed"),
                "state": row["state"], "receipt_path": str(receipt_path) if receipt_path else None})
            if (receipt and receipt.get("status") == "SUCCEEDED"
                    and receipt.get("hidden_test_opened") is False
                    and row["receipt_path"].parent.joinpath("effective_config.json").is_file()
                    and receipt.get("validation_prediction_file")
                    and row["receipt_path"].parent.joinpath(str(receipt["validation_prediction_file"])).is_file()):
                if (stream == "EVAL_UPDATE" and mutation == "C1"
                        and str(config.get("work_id", "")) in {str(r["config"].get("work_id", "")) for r in evaluation_rows}):
                    binding = self._episode_rescore_binding(episode, row)
                    action_bindings.append(binding)

        # Training revisions are fitted from the registered recipe builder,
        # using researcher-only data IDs and exact version references.
        if stream == "TRAIN_UPDATE":
            from .training_runtime import build_episode_fit_config, researcher_c3_labels
            if mutation not in {"C3", "C4"}:
                raise ValueError("TRAIN_UPDATE episodes must explicitly identify registered C3 or C4")
            allowed_configurations = {str(row["config"].get("configuration_id")) for row in pair_rows}
            if len(allowed_configurations) != 2 or _task_anchor_configuration_id(task) not in allowed_configurations:
                raise ValueError("Training revisions require the source-selected non-anchor and registered anchor pair")
            factual = dict(episode.get("factual_changes") or {})
            update_counts = episode.get("family_update_counts") or factual.get("family_update_counts") or {}
            if not update_counts and episode.get("resolved_updates_path"):
                resolved = _read_json(Path(str(episode["resolved_updates_path"])))
                update_counts = resolved.get("family_update_counts", {})
            updates = int(episode.get("optimizer_updates") or update_counts.get(family, 0))
            if updates <= 0:
                raise ValueError("TRAIN_UPDATE requires an observed calibration-selected positive update count")
            data_ref = episode.get("data_ids_reference") or self.manifest.get("experiment_state", {}).get("data_ids_reference")
            versions_ref = episode.get("dataset_versions_reference") or self.manifest.get("experiment_state", {}).get("dataset_versions_reference")
            if not data_ref or not versions_ref:
                raise ValueError("Training revision needs registered data ID and dataset version references")
            data_ids = _read_json(Path(str(data_ref)))[family]
            versions = _read_json(Path(str(versions_ref)))
            dataset_version = str(episode.get("dataset_version_id") or versions.get(family, ""))
            if not dataset_version:
                raise ValueError(f"No registered dataset version is available for {family}")
            source_spec = Path(str(episode.get("source_spec_dir") or self.manifest.get("historical_source_spec_dir", "")))
            data_root = episode.get("source_data_root") or self.manifest.get("experiment_state", {}).get("data_root")
            labels = researcher_c3_labels(Path(str(data_root))) if family == "bloodmnist" and mutation == "C3" else None
            training_revision = factual.get("training_data_revision") or {}
            recipe_revision = factual.get("training_recipe_revision") or {}
            mutation_seed = training_revision.get("mutation_seed", factual.get("mutation_seed", 20261012))
            declared_selection = training_revision.get("mutation_selection", factual.get("mutation_selection"))
            config_dir = self.campaign / "development" / "episode_configs"
            for row in pair_rows:
                original = row["config"]
                selected_candidate = str(original.get("configuration_id", ""))
                seed = int(original.get("seed", 0))
                work_id = _action_work_id(str(episode.get("work_id", "episode")), "train",
                                          selected_candidate, str(seed), mutation)
                config = build_episode_fit_config(source_spec_dir=source_spec,
                    task_id=task_id, configuration_id=selected_candidate,
                    seed=seed, mutation=mutation, work_id=work_id,
                    dataset_version_id=dataset_version, data_ids=data_ids, class_labels=labels,
                    optimizer_updates=updates,
                    mutation_seed=int(mutation_seed),
                    recipe_version_id=recipe_revision.get("recipe_version_id", factual.get("recipe_version_id")),
                    recipe_overrides=recipe_revision.get("recipe_overrides", recipe_revision or factual.get("recipe_overrides")))
                if declared_selection is not None and not _same_registered_mutation_selection(
                        config.get("mutation_selection") or {}, declared_selection):
                    raise ValueError("Computed C3 retained/removed IDs differ from the exact registered selection")
                declared_train_ids = (episode.get("factual_changes", {}).get("training_data_revision", {}) or {}).get("train_sample_ids")
                if declared_train_ids is not None and config.get("train_sample_ids") != declared_train_ids:
                    raise ValueError("Computed C3 training IDs differ from the explicit registered training_data_revision IDs")
                output = self.campaign / "runs" / "episodes" / str(episode.get("work_id", "episode")) / mutation / work_id
                config_path = config_dir / f"{work_id}.json"
                config["output_reference"] = str(output)
                _atomic_json_file(config_path, config)
                training_evidence_rows.append({"work_id": work_id,
                    "receipt_path": str(output / "execution_receipt.json"),
                    "configuration_id": selected_candidate, "seed": seed,
                    "fit_gpu_seconds": None})
                if not is_replay:
                    action_bindings.append({"kind": "fit", "action_name": "train", "work_id": work_id,
                    "configuration_id": selected_candidate, "seed": config["seed"],
                    "configuration_path": str(config_path), "output_directory": str(output),
                    "fit_timeout_seconds": float(episode.get("fit_timeout_seconds", 90)),
                    "phase": "formal", "effective_config": config,
                    "source_files": [str(Path(__file__).resolve()), str(source_spec / "tasks.json"), str(config_path)]})

        # Evaluation updates may use a declared input perturbation to score an
        # existing checkpoint. Noise parameters and targets are bound to the
        # registered factual change; this action performs zero optimizer steps.
        if stream == "EVAL_UPDATE":
            factual = dict(episode.get("factual_changes") or {})
            eval_revision = factual.get("revision_id") or factual.get("evaluation_revision_id")
            if eval_revision is None and isinstance(factual.get("validation_input_config"), Mapping):
                eval_revision = "C2"
            if eval_revision is None and isinstance(factual.get("evaluation_contract"), Mapping):
                eval_revision = factual["evaluation_contract"].get("revision_id", "C1")
            noise = factual.get("input_noise") or factual.get("noise") or factual.get("validation_input_config")
            if noise and not is_replay:
                for row in candidate_rows:
                    receipt = row["receipt"]
                    if not receipt or receipt.get("status") != "SUCCEEDED":
                        continue
                    checkpoint = Path(str(row["output_directory"])) / "checkpoint.pt"
                    if not checkpoint.is_file():
                        continue
                    cfg = dict(row["config"])
                    inference_config = self.campaign / "development" / "episode_configs" / f"{episode['work_id']}-eval-{cfg['configuration_id']}-{cfg.get('seed')}.json"
                    overrides = _c2_evaluation_overrides(family, noise, factual, eval_revision)
                    eval_config = {**cfg, **overrides}
                    _atomic_json_file(inference_config, eval_config)
                    infer_id = _action_work_id(str(episode["work_id"]), "infer", str(cfg["configuration_id"]), str(cfg.get("seed", "")))
                    output = self.campaign / "runs" / "episodes" / str(episode["work_id"]) / "inference" / infer_id
                    action_bindings.append({"kind": "infer", "action_name": "infer", "work_id": infer_id,
                        "configuration_id": cfg["configuration_id"], "seed": cfg.get("seed"),
                        "inference_spec": {"config_path": str(inference_config), "checkpoint_path": str(checkpoint),
                            "output_directory": str(output), "fit_timeout_seconds": float(episode.get("fit_timeout_seconds", 90)),
                            "phase": "formal", "gpu_devices": episode.get("gpu_devices", []),
                            "allowed_config_changes": sorted(overrides), "evaluation_revision": factual,
                            "dataset_root": str(self.manifest["experiment_state"]["data_root"])}})
                    inference_evidence_rows.append({"work_id": infer_id,
                        "receipt_path": str(output / "inference_receipt.json"),
                        "configuration_id": cfg["configuration_id"], "seed": cfg.get("seed"),
                        "fit_gpu_seconds": row.get("fit_gpu_seconds")})
            required_pairs = {(str(row["config"].get("configuration_id")), str(row["config"].get("seed")))
                              for row in candidate_rows
                              if str(row["config"].get("seed")) in set(map(str, original_pair["record"]["paired_seeds"]))}
            if mutation == "C1":
                observed_pairs = {(str(row.get("configuration_id")), str(row.get("seed")))
                                  for row in action_bindings if row.get("kind") == "rescore"}
            else:
                observed_pairs = {(str(row.get("configuration_id")), str(row.get("seed")))
                                  for row in action_bindings if row.get("kind") == "infer"}
            if required_pairs != observed_pairs or len(required_pairs) != 6:
                raise ValueError("EVAL_UPDATE requires fresh source-bound scoring/inference for all three snapshot configurations and both registered seeds")

        if stream == "TRAIN_UPDATE":
            required_pairs = {(str(row["config"].get("configuration_id")), str(row["config"].get("seed")))
                              for row in pair_rows}
            observed_pairs = {(str(row.get("configuration_id")), str(row.get("seed")))
                              for row in action_bindings if row.get("kind") == "fit"}
            if required_pairs != observed_pairs or len(required_pairs) != 4:
                raise ValueError("TRAIN_UPDATE requires the anchor and selected candidate at both registered seeds")

        metric_info = _episode_metric_contract(task, episode, mutation)
        if is_replay and mutation not in {"C0", "C1"}:
            metric_info = {}
        primary_metric = str(metric_info.get("revised_primary_metric") or task.get("revised_primary_metric") or "")
        direction = str(metric_info.get("revised_direction") or task.get("revised_direction") or "")
        if primary_metric and direction:
            analysis_id = _action_work_id(str(episode.get("work_id", "episode")), "select", task_id, stream)
            evidence_rows = (training_evidence_rows if stream == "TRAIN_UPDATE" else
                             inference_evidence_rows if inference_evidence_rows else None)
            if evidence_rows is not None:
                evidence = [dict(row) for row in evidence_rows]
            elif mutation == "C1":
                evidence = [{"work_id": binding["work_id"], "receipt_path": binding["output_path"],
                    "configuration_id": binding["configuration_id"], "seed": binding.get("seed"),
                    "evidence_kind": "independent_rescore"}
                    for binding in action_bindings if binding.get("kind") == "rescore"]
            else:
                evidence = [{"work_id": row["config"].get("work_id"), "receipt_path": str(row["receipt_path"]),
                             "configuration_id": row["config"].get("configuration_id"),
                             "seed": row["config"].get("seed"),
                             "fit_gpu_seconds": row.get("fit_gpu_seconds")}
                            for row in evaluation_rows if row["receipt_path"] and row["receipt"]]
            if evidence:
                action_bindings.append({"kind": "analysis", "action_name": "select", "work_id": analysis_id,
                    "configuration_id": task_id, "seed": episode.get("seed"),
                    "analysis_operation": "select", "metric": primary_metric, "direction": direction,
                    "require_formal": stream == "EVAL_UPDATE", "evidence": evidence,
                    "side_constraints": metric_info.get("revised_side_constraints", []),
                    "anchor_configuration_id": _task_anchor_configuration_id(task),
                    "exclude_anchor": True,
                    "ledger_path": str(self.ledger_path), "ledger_id": self.ledger_id,
                    "campaign_id": self.campaign_id,
                    "source_fit_work_ids": [{"work_id": str(row["config"].get("work_id")),
                        "configuration_id": row["config"].get("configuration_id"), "seed": row["config"].get("seed")}
                        for row in evaluation_rows],
                    "output_path": str(self.campaign / "runs" / "episodes" / str(episode.get("work_id")) / "selection.json")})
        pairs = episode.get("comparison_pairs") or (episode.get("factual_changes") or {}).get("comparison_pairs")
        if primary_metric and not is_replay:
            evidence = [dict(row) for row in training_evidence_rows] if stream == "TRAIN_UPDATE" else [
                {"work_id": row["config"].get("work_id"), "receipt_path": str(row["receipt_path"]),
                 "configuration_id": row["config"].get("configuration_id"),
                 "seed": row["config"].get("seed"), "fit_gpu_seconds": row.get("fit_gpu_seconds")}
                for row in pair_rows if row["receipt_path"] and row["receipt"]]
            if inference_evidence_rows:
                evidence = [dict(row) for row in inference_evidence_rows]
            elif mutation == "C1":
                evidence = [{"work_id": binding["work_id"], "receipt_path": binding["output_path"],
                    "configuration_id": binding["configuration_id"], "seed": binding.get("seed"),
                    "evidence_kind": "independent_rescore"}
                    for binding in action_bindings if binding.get("kind") == "rescore"]
            anchor_id = _task_anchor_configuration_id(task)
            evidence_by_config_seed = {(str(row["configuration_id"]), str(row.get("seed"))): row for row in evidence}
            candidate_ids = sorted({str(row["configuration_id"]) for row in evidence
                                    if row.get("configuration_id") is not None and str(row["configuration_id"]) != str(anchor_id)})
            comparison_ids = candidate_ids if stream == "EVAL_UPDATE" else [str(original_pair["record"]["selected_non_anchor_configuration_id"])]
            for comparison_id in comparison_ids:
                pair_records = [{"left": evidence_by_config_seed[(str(anchor_id), str(seed))]["work_id"],
                                 "right": evidence_by_config_seed[(str(comparison_id), str(seed))]["work_id"]}
                                for seed in original_pair["record"]["paired_seeds"]
                                if (str(anchor_id), str(seed)) in evidence_by_config_seed and
                                   (str(comparison_id), str(seed)) in evidence_by_config_seed]
                if not pair_records:
                    continue
                action_bindings.append({"kind": "analysis", "action_name": "compare",
                    "work_id": _action_work_id(str(episode.get("work_id", "episode")), "compare", task_id, stream, comparison_id),
                    "configuration_id": task_id, "seed": episode.get("seed"),
                    "analysis_operation": "compare", "metric": primary_metric,
                    "pairs": pair_records, "evidence": evidence,
                    "selection_work_id": analysis_id if stream == "EVAL_UPDATE" else None,
                    "compared_configuration_ids": [str(anchor_id), str(comparison_id)],
                    "compared_seeds": list(original_pair["record"]["paired_seeds"]),
                    "ledger_path": str(self.ledger_path), "ledger_id": self.ledger_id,
                    "campaign_id": self.campaign_id,
                    "source_fit_work_ids": [{"work_id": str(row.get("work_id")),
                        "configuration_id": row.get("configuration_id"), "seed": row.get("seed")}
                        for row in evidence],
                    "output_path": str(self.campaign / "runs" / "episodes" / str(episode.get("work_id")) / f"comparison-{comparison_id}.json")})
        if not action_bindings:
            action_bindings = [{"kind": "analysis", "action_name": "inspect",
                "work_id": _action_work_id(str(episode.get("work_id", "episode")), "inspect", task_id),
                "configuration_id": task_id, "seed": episode.get("seed"),
                "analysis_operation": "inspect", "evidence": evidence_records}]

        graph_edges = [{"from": node["id"], "to": str(episode.get("work_id")), "relation": "supports"}
                       for node in evidence_graph_nodes]
        treatment_view = _treatment_evidence_view(arm, evidence_records, evidence_graph_nodes, graph_edges)
        binding_ids = sorted(str(binding["work_id"]) for binding in action_bindings)
        decision_schema = {"type": "object", "required": ["mode", "actions", "submission", "rationale"],
            "properties": {"mode": {"type": "string", "enum": ["action", "submit"]},
                "actions": {"type": "array", "items": {"type": "object",
                "required": ["kind", "action_work_id"], "properties": {"kind": {"type": "string",
                    "enum": sorted({b["action_name"] for b in action_bindings})},
                    "action_work_id": {"type": "string", "enum": binding_ids},
                    "configuration_id": {"type": ["string", "null"]}, "seed": {"type": ["integer", "null"]}}}},
                "submission": {"type": "object", "properties": {"selected_configuration_id": {"type": ["string", "null"]},
                    "claim": {"type": "string"}}},
                "rationale": {"type": "string"}}}
        messages = [{"role": "system", "content": "You are deciding one action in a registered scientific experiment. Use only supplied researcher-visible evidence and source-bound operations. Missing or incomplete receipts are unknown; do not infer results. Choose an action only from the supplied action bindings. The action executor will independently verify the actual result."},
            {"role": "user", "content": _json({"episode": {"work_id": episode.get("work_id"), "task_id": task_id,
                "family": family, "arm": arm, "stream": stream, "mutation": mutation,
                "case_id": episode.get("case_id"), "repeat": episode.get("repeat"), "seed": episode.get("seed"),
                "research_question": question}, "task_contract": task, "original_pair_selection": original_pair["record"],
                "factual_changes": episode.get("factual_changes", {}),
                "evidence_view": treatment_view, "action_bindings": [{k: b.get(k) for k in
                    ("action_name", "configuration_id", "seed", "kind", "work_id")} for b in action_bindings],
                "public_tools": episode.get("public_tools", self.manifest.get("public_tools", [])),
                "decision_schema": decision_schema})}]
        episode_payload = {**episode, "messages": messages, "decision_schema": decision_schema,
            "allowed_decisions": sorted({b["action_name"] for b in action_bindings}),
            "action_bindings": action_bindings, "output_stage": "experiment"}
        episode_payload["task_contract"] = task
        episode_payload["original_pair_selection"] = original_pair["record"]
        if is_replay:
            # Replays expose only previously observed evidence and CPU analysis.
            # Mutated fits or inference are never registered as replay actions.
            action_bindings = [b for b in action_bindings if b.get("kind") in {"analysis", "rescore"}]
            if mutation not in {"C0", "C1"}:
                action_bindings = [b for b in action_bindings if b.get("action_name") == "inspect"]
            if not action_bindings:
                action_bindings = [{"kind": "analysis", "action_name": "inspect",
                    "work_id": _action_work_id(str(episode.get("work_id", "replay")), "inspect", task_id),
                    "configuration_id": task_id, "seed": episode.get("seed"),
                    "analysis_operation": "inspect", "evidence": evidence_records}]
        limits = self.episode_resource_limits({"work_id": episode.get("work_id"),
            "kind": episode.get("kind", "episode"), "payload": episode_payload})
        episode_payload.setdefault("resource_policy", {
            "active_wall_seconds": limits["active_wall_seconds"],
            "actual_gpu_seconds": limits["gpu_seconds"]})
        return self._attach_public_context(episode, episode_payload)

    def _public_memory(self, payload: Mapping[str, Any]) -> "StudyMemory":
        from .study_memory import ContextPageCapacityError, StudyMemory
        work_id = str(payload.get("work_id") or (payload.get("episode") or {}).get("work_id") or "")
        if not work_id:
            raise ValueError("Public context memory requires an explicit episode work ID")
        workspace = (self.campaign / "runs" / "episodes" / quote(work_id, safe="._-", encoding="utf-8")).resolve() / "public_memory"
        try:
            workspace.relative_to(self.campaign)
        except ValueError:
            raise ValueError("Episode public memory must remain inside the campaign") from None
        return StudyMemory(workspace)

    def _context_response_capacity(self, client: Any) -> int | None:
        """Return only a declared or previously observed transport input limit."""
        declared = []
        candidates = [getattr(client, "config", {}), getattr(client, "provider", {})]
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            nested = [candidate, candidate.get("transport_capabilities"), candidate.get("model_capabilities")]
            for row in nested:
                if not isinstance(row, Mapping):
                    continue
                for key in ("max_input_characters", "max_input_chars"):
                    value = row.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        declared.append(value)
        provider = getattr(client, "provider", {}) or {}
        provider_id, model = provider.get("id"), provider.get("model")
        api = getattr(client, "api", None) or provider.get("kind")
        if api not in {"codex_cli", None} and provider.get("kind") != "codex_cli":
            self._context_capacity_sources = [{"source": "provider_capability", "max_chars": value}
                                              for value in declared]
            return min(declared) if declared else None
        cache = getattr(self, "_context_capacity_cache", None)
        if cache is None:
            cache = self._context_capacity_cache = {}
        config = getattr(client, "config", {})
        configured_executable = os.environ.get("FOREST_CODEX_EXECUTABLE", "codex")
        expected_executable = shutil.which(configured_executable) or ""
        cache_key = (str(api), expected_executable)
        with self.ledger.connect() as db:
            rows = db.execute("SELECT request_id,response_json, "
                "json_extract(request_json,'$.provider_api') AS provider_api, "
                "json_extract(request_json,'$.provider_snapshot.kind') AS provider_kind, "
                "json_extract(request_json,'$.transport_trace_path') AS trace_path "
                "FROM forest_study_model_calls "
                "WHERE campaign_id=? AND (response_json LIKE '%input_too_large%' "
                "OR response_json LIKE '%transport_trace_path%') ORDER BY recorded_at DESC",
                (self.campaign_id,)).fetchall()
        observed = []
        sources = [{"source": "provider_capability", "max_chars": value}
                   for value in declared]
        for row in rows:
            try:
                request_api = row["provider_api"] or row["provider_kind"]
                if request_api != "codex_cli":
                    continue
                response = json.loads(row["response_json"] or "{}")
                error = response.get("transport_error", response) if isinstance(response, Mapping) else {}
                trace_path = (row["trace_path"] or
                    (error.get("transport_trace_path") if isinstance(error, Mapping) else None))
                if not trace_path or not Path(str(trace_path)).is_file():
                    continue
                trace = _read_json(Path(str(trace_path)))
                argv = trace.get("argv")
                trace_executable = str(argv[0]) if isinstance(argv, list) and argv else ""
                if not expected_executable or not trace_executable or \
                        str(Path(trace_executable).resolve()) != str(Path(expected_executable).resolve()):
                    continue
                diagnostics = error.get("transport_diagnostics", error) if isinstance(error, Mapping) else {}
                value = diagnostics.get("max_chars") if isinstance(diagnostics, Mapping) else None
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    observed.append(value)
                    sources.append({"source": "persisted_transport_diagnostic",
                        "request_id": row["request_id"], "max_chars": value,
                        "trace_path": str(trace_path), "executable": trace_executable})
                stderr = str(trace.get("stderr", ""))
                from research.agents.codex_transport import _input_rejection
                rejection = _input_rejection(stderr)
                value = rejection.get("max_chars") if isinstance(rejection, Mapping) else None
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    observed.append(value)
                    sources.append({"source": "persisted_cli_trace", "request_id": row["request_id"],
                        "trace_path": str(trace_path), "executable": trace_executable, "max_chars": value})
            except (OSError, ValueError, TypeError, KeyError):
                continue
        values = [*declared, *observed]
        capacity = min(values) if values else None
        self._context_capacity_sources = sources
        cache[cache_key] = capacity
        return capacity

    def _prepare_public_page_measure_context(self, payload: Mapping[str, Any],
                                    history: Sequence[Mapping[str, Any]], page: Mapping[str, Any],
                                    action_work_id: str, prefix_results: Sequence[Mapping[str, Any]] = (),
                                    trailing_history: Sequence[Mapping[str, Any]] = ()):
        """Build the candidate-independent next request envelope once per page read."""
        actions = [copy.deepcopy(dict(row)) for row in prefix_results if isinstance(row, Mapping)]
        actions.append({"action_work_id": action_work_id, "kind": "analysis", "state": "completed",
            "result": {"operation": "read_context_segment", **copy.deepcopy(dict(page))}})
        turns = [*copy.deepcopy(list(history)), {"actions": actions, "submission": {}}]
        turns.extend(copy.deepcopy(list(trailing_history)))
        work_id = str(payload.get("work_id", ""))
        from .end_to_end_runtime import is_end_to_end
        if (is_end_to_end(payload) or _is_replay_episode(payload)
                or (payload.get("task_id") and payload.get("family") and payload.get("stream"))):
            next_base = self.build_episode_request({**dict(payload), "work_id": work_id})
        else:
            next_base = copy.deepcopy(dict(payload))
        return self._attach_public_context(next_base, next_base, history=turns)

    def _measure_public_page_request(self, client: Any, payload: Mapping[str, Any],
                                    history: Sequence[Mapping[str, Any]], page: Mapping[str, Any],
                                    action_work_id: str, prefix_results: Sequence[Mapping[str, Any]] = (),
                                    trailing_history: Sequence[Mapping[str, Any]] = (),
                                    prepared_context: Mapping[str, Any] | None = None) -> int:
        """Measure exact next request wire against an already prepared stable envelope."""
        actions = [copy.deepcopy(dict(row)) for row in prefix_results if isinstance(row, Mapping)]
        action = {"action_work_id": action_work_id, "kind": "analysis", "state": "completed",
            "result": {"operation": "read_context_segment", **copy.deepcopy(dict(page))}}
        actions.append(action)
        turns = [*history, {"actions": actions, "submission": {}}]
        turns.extend(copy.deepcopy(list(trailing_history)))
        next_payload = prepared_context or self._prepare_public_page_measure_context(
            payload, history, page, action_work_id, prefix_results, trailing_history)
        messages = copy.deepcopy(list(next_payload.get("messages", [])))
        arm = str(next_payload.get("arm", next_payload.get("design_arm", ""))).upper()
        context = _treatment_history_view(arm, turns)
        messages.append({"role": "user", "content":
            "Observed results from completed source-bound operations in this episode:\n" + _json(context)})
        schema = next_payload.get("decision_schema", {})
        if messages:
            messages[-1]["content"] = str(messages[-1].get("content", "")) + \
                "\n\nReturn exactly one JSON object satisfying this decision contract:\n" + _json(schema)
        from research.agents.policy import ensure_research_messages
        messages = ensure_research_messages(messages, client.config, str(next_payload.get("output_stage", "experiment")))
        messages, _ = _intern_delivered_messages(messages)
        clone = copy.copy(client)
        clone.config = copy.deepcopy(dict(getattr(client, "config", {})))
        transport_schema = _codex_strict_schema(schema) if (
            (getattr(client, "provider", {}) or {}).get("kind") == "codex_cli"
            or getattr(client, "api", None) == "codex_cli") else copy.deepcopy(schema)
        clone.config["_forest_action_schema"] = transport_schema
        clone.config["output_stage"] = str(next_payload.get("output_stage", "experiment"))
        allocation = next_payload.get("max_output_tokens")
        if allocation is None:
            from research.agents.output_budget import output_allocation
            allocation = output_allocation(clone.config, messages, stage=clone.config["output_stage"])["tokens"]
        _, request_payload = clone.build_request(messages, json_mode=True, max_output_tokens=int(allocation))
        return len(json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def _public_page_measure_callback(self, client: Any, payload: Mapping[str, Any],
                                      history: Sequence[Mapping[str, Any]], action_work_id: str,
                                      trailing_history: Sequence[Mapping[str, Any]] = ()):
        """Reuse one prepared envelope for all binary-search page candidates."""
        prepared = []
        def measure(page, *, prefix_results=(), action_work_id=action_work_id, action=None):
            prefix = [copy.deepcopy(dict(row)) for row in prefix_results if isinstance(row, Mapping)]
            key = _json({"action_work_id": str(action_work_id), "prefix": prefix})
            template = next((entry[1] for entry in prepared if entry[0] == key), None)
            if template is None:
                template = self._prepare_public_page_measure_context(payload, history, page,
                    str(action_work_id), prefix, trailing_history)
                prepared.append((key, template))
            return self._measure_public_page_request(client, payload, history, page,
                str(action_work_id), prefix, trailing_history, prepared_context=template)
        return measure

    def _refit_latest_context_page(self, payload: Mapping[str, Any], history: list[dict[str, Any]],
                                  client: Any) -> list[dict[str, Any]]:
        """Fit the last working page after every same-turn receipt has settled."""
        capacity = self._context_response_capacity(client)
        if capacity is None or not history:
            return history
        page_turn_index = None
        page_action_index = None
        for turn_index in range(len(history) - 1, -1, -1):
            actions = history[turn_index].get("actions", [])
            for action_index in range(len(actions) - 1, -1, -1):
                if _is_context_read_action(actions[action_index]):
                    page_turn_index, page_action_index = turn_index, action_index
                    break
            if page_turn_index is not None:
                break
        if page_turn_index is None:
            return history
        # A prior read page is represented by an immutable reference in later
        # turns. Its cursor reflects what was actually returned then; never
        # advance it to a slice that no current model request received.
        if page_turn_index != len(history) - 1:
            return history
        turn = history[page_turn_index]
        read_action = turn["actions"][page_action_index]
        old_page = read_action.get("result") or {}
        if not isinstance(old_page, Mapping) or not old_page.get("segment_id"):
            return history
        binding = next((row for row in payload.get("action_bindings", [])
            if isinstance(row, Mapping) and row.get("action_name") == "read_context_segment"), None)
        if not binding:
            return history
        workspace = Path(str(binding.get("memory_workspace", ""))).resolve()
        if workspace.name == ".forest-context":
            workspace = workspace.parent
        from .study_memory import ContextPageCapacityError, StudyMemory
        memory = StudyMemory(workspace)
        other_actions = [copy.deepcopy(action) for index, action in enumerate(turn.get("actions", []))
                         if index != page_action_index and isinstance(action, Mapping)]
        preceding_turns = history[:page_turn_index]
        action_work_id = str(read_action.get("action_work_id", binding.get("work_id", "")))
        page_measure = self._public_page_measure_callback(client, payload, preceding_turns,
            action_work_id, trailing_history=history[page_turn_index + 1:])
        measure = lambda page, page_measure=page_measure: page_measure(page, prefix_results=other_actions)
        requested_limit = int(old_page.get("requested_limit", old_page.get("returned_characters", 6000)))
        try:
            fitted = memory.read_json(str(old_page["segment_id"]),
                pointer=str(old_page.get("pointer", "")), offset=int(old_page.get("offset", 0)),
                limit=requested_limit, response_capacity=capacity, measure_response=measure)
        except ContextPageCapacityError as exc:
            offset = int(old_page.get("offset", 0))
            fitted = {key: old_page[key] for key in ("segment_id", "pointer", "total_characters", "complete")
                      if key in old_page}
            fitted.update({"offset": offset, "next_offset": offset, "requested_limit": requested_limit,
                "returned_characters": 0, "content": None, "delivery_status": "not_delivered",
                "delivery_reason": "The exact source page no longer fits alongside all completed same-turn results.",
                "response_capacity": capacity,
                "minimum_response_characters": getattr(exc, "minimum_response_characters", None)})
            fitted["complete"] = offset >= int(fitted.get("total_characters", offset))
        if fitted != old_page:
            read_action["result"] = {**dict(fitted), "operation": "read_context_segment"}
            self.ledger.event(self.campaign_id, "context_page_projection", {
                "action_work_id": action_work_id, "source_segment_id": fitted["segment_id"],
                "pointer": fitted["pointer"], "offset": fitted["offset"],
                "next_offset": fitted["next_offset"], "total_characters": fitted["total_characters"],
                "requested_limit": fitted["requested_limit"], "returned_characters": fitted["returned_characters"],
                "delivery_status": fitted.get("delivery_status", "delivered"),
                "response_capacity": capacity, "response_capacity_sources":
                    copy.deepcopy(getattr(self, "_context_capacity_sources", [])),
                "result": fitted}, str(payload.get("work_id", "")))
        return history

    def _attach_public_context(self, source_payload: Mapping[str, Any], prepared: Mapping[str, Any], *,
                               history: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        """Add an arm-local, paged read action over the exact public request evidence."""
        payload = copy.deepcopy(dict(prepared))
        memory = self._public_memory({**dict(source_payload), "work_id": payload.get("work_id", source_payload.get("work_id"))})
        old_context_binding_ids = {
            str(row.get("work_id")) for row in payload.get("action_bindings", [])
            if isinstance(row, Mapping) and row.get("action_name") == "read_context_segment"
            and row.get("work_id") is not None
        }
        bindings = [dict(row) for row in payload.get("action_bindings", []) if isinstance(row, Mapping)
                    and row.get("action_name") != "read_context_segment"]
        base_schema = copy.deepcopy(payload.get("decision_schema", {}))
        action_schema = base_schema.get("properties", {}).get("actions", {}).get("items", {})
        action_properties = action_schema.get("properties", {})
        if isinstance(action_properties.get("kind"), Mapping) and isinstance(action_properties["kind"].get("enum"), list):
            action_properties["kind"]["enum"] = [value for value in action_properties["kind"]["enum"]
                                                    if value != "read_context_segment"]
        binding_schema = action_properties.get("action_work_id")
        if isinstance(binding_schema, Mapping) and isinstance(binding_schema.get("enum"), list):
            binding_schema["enum"] = [value for value in binding_schema["enum"]
                                       if str(value) not in old_context_binding_ids]
        for key in ("context_segment_id", "context_pointer", "context_offset", "context_limit"):
            action_properties.pop(key, None)
        base_messages = copy.deepcopy(payload.get("messages", []))
        catalog_marker = "Arm-public context catalog (metadata only): "
        observational_markers = (catalog_marker,
            "Previously retrieved exact source pages (references only): ")
        base_messages = [message for message in base_messages if not (
            isinstance(message, Mapping) and message.get("role") == "system"
            and str(message.get("content", "")).startswith(observational_markers))]
        public_turns = []
        for turn in history:
            if not isinstance(turn, Mapping):
                continue
            actions = [copy.deepcopy(dict(action)) for action in turn.get("actions", [])
                       if isinstance(action, Mapping) and not _is_context_read_action(action)]
            if actions or turn.get("submission_validation"):
                public_turns.append({"actions": actions,
                    "submission": copy.deepcopy(turn.get("submission", {})),
                    "submission_validation": copy.deepcopy(turn.get("submission_validation"))})
        if not history:
            with self.ledger.connect() as db:
                aide_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_study_aide_sessions'").fetchone()
                if aide_table:
                    row = db.execute("SELECT state_json FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?",
                                     (self.campaign_id, str(payload.get("work_id", "")))).fetchone()
                    if row:
                        try:
                            stored_turns = json.loads(row[0]).get("action_history", [])
                            public_turns = []
                            for turn in stored_turns:
                                if not isinstance(turn, Mapping):
                                    continue
                                actions = [copy.deepcopy(dict(action)) for action in turn.get("actions", [])
                                    if isinstance(action, Mapping) and not _is_context_read_action(action)]
                                if actions or turn.get("submission_validation") or turn.get("submission"):
                                    public_turns.append({"actions": actions,
                                        "submission": copy.deepcopy(turn.get("submission", {})),
                                        "submission_validation": copy.deepcopy(turn.get("submission_validation"))})
                        except (ValueError, TypeError):
                            public_turns = []
        memory_value = {"messages": base_messages,
            "decision_schema": base_schema,
            "task_contract": copy.deepcopy(payload.get("task_contract", {})),
            "original_pair_selection": copy.deepcopy(payload.get("original_pair_selection")),
            "action_bindings": bindings, "action_history": public_turns,
            "allowed_decisions": [name for name in payload.get("allowed_decisions", [])
                                  if name != "read_context_segment"]}
        metadata = memory.save_json("public-context", memory_value, origin="arm-public prepared request and observed history")
        catalog = memory.catalog()
        segment_ids = sorted(str(row.get("segment_id", row.get("id"))) for row in catalog.get("segments", [])
                             if row.get("segment_id", row.get("id")))
        context_binding = {"kind": "analysis", "action_name": "read_context_segment",
            "work_id": _action_work_id(str(payload.get("work_id", "episode")), "read-context", metadata["segment_id"]),
            "analysis_operation": "read_context_segment", "memory_workspace": str(memory.store.root),
            "available_segment_ids": segment_ids}
        payload["action_bindings"] = bindings + [context_binding]
        payload["allowed_decisions"] = sorted((set(payload.get("allowed_decisions", [])) - {"read_context_segment"}) | {"read_context_segment"})
        schema = base_schema
        action_schema = schema.get("properties", {}).get("actions", {}).get("items", {})
        action_properties = action_schema.setdefault("properties", {})
        kind_schema = action_properties.get("kind")
        if isinstance(kind_schema, Mapping) and isinstance(kind_schema.get("enum"), list):
            kind_schema["enum"] = sorted(set(kind_schema["enum"]) | {"read_context_segment"})
        binding_schema = action_properties.setdefault("action_work_id", {"type": "string", "enum": []})
        if isinstance(binding_schema, Mapping) and isinstance(binding_schema.get("enum"), list):
            binding_schema["enum"] = sorted(set(binding_schema["enum"]) | {context_binding["work_id"]})
        action_properties.update({
            "context_segment_id": {"type": ["string", "null"], "enum": [*segment_ids, None]},
            "context_pointer": {"type": ["string", "null"]},
            "context_offset": {"type": ["integer", "null"]},
            "context_limit": {"type": ["integer", "null"]}})
        payload["decision_schema"] = schema
        payload["context_memory"] = {"segment_id": metadata["segment_id"],
            "catalog": catalog, "read_operation": "read_context_segment",
            "scientific_progress_eligible": False}
        if payload.get("messages"):
            messages = copy.deepcopy(payload["messages"])
            catalog_note = ("Arm-public context catalog (metadata only): " + _json(catalog)
                + "\nUse read_context_segment with the exact segment ID, JSON pointer, offset, and limit to retrieve exact source content. "
                  "Paging is observational and does not count as a scientific operation.")
            if not any(isinstance(message, Mapping) and message.get("role") == "system"
                       and str(message.get("content", "")).startswith(catalog_marker) for message in messages):
                messages.insert(max(0, len(messages) - 1), {"role": "system", "content": catalog_note})
            payload["messages"] = messages
        return payload

    def _episode_candidate_rows(self, episode: Mapping[str, Any], fit_items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        from .gpu_supervisor import ComputeSupervisor
        plan_path = episode.get("snapshot_plan_path") or self.manifest.get("experiment_state", {}).get("snapshot_plan_reference")
        supplied = episode.get("candidate_configs")
        configs = []
        if supplied is not None:
            if not isinstance(supplied, list):
                raise ValueError("candidate_configs must be a list of full registered configurations")
            configs = [dict(x) for x in supplied]
        elif plan_path and Path(str(plan_path)).is_file():
            plan_file = Path(str(plan_path))
            stat = plan_file.stat()
            identity = (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))
            cache = getattr(self, "_snapshot_plan_cache", {})
            cached = cache.get(str(plan_file))
            if cached and cached[0] == identity:
                plan = cached[1]
            else:
                plan = _read_json(plan_file)
                cache[str(plan_file)] = (identity, plan)
                self._snapshot_plan_cache = cache
            configs = [dict(x) for x in plan.get("configs", [])]
        else:
            for item in fit_items:
                path = item.get("configuration_path")
                if path and Path(str(path)).is_file():
                    configs.append(_read_json(Path(str(path))))
        task_id = str(episode["task_id"])
        configs = [c for c in configs if str(c.get("task_id")) == task_id]
        if not configs:
            return []
        # Cache source receipt parsing per task while checking receipt file
        # identities each time, so newly completed snapshots invalidate the
        # cached partial view without repeatedly parsing the 72-fit plan.
        fingerprint = []
        for registered in configs:
            path = Path(str(registered.get("output_directory") or registered.get("output_reference") or ""))
            receipt = path / "execution_receipt.json"
            effective = path / "effective_config.json"
            config_file = Path(str(registered.get("configuration_path", ""))) if registered.get("configuration_path") else None
            config_identity = (str(config_file),
                (config_file.stat().st_size, config_file.stat().st_mtime_ns)
                if config_file is not None and config_file.is_file() else None)
            output_identity = tuple((str(p), (p.stat().st_size, p.stat().st_mtime_ns) if p.is_file() else None)
                                    for p in (receipt, effective))
            fingerprint.append((str(registered.get("work_id")), _json(registered),
                                config_identity, output_identity))
        task_cache = getattr(self, "_candidate_rows_cache", {})
        cache_key = str(task_id)
        cached_rows = task_cache.get(cache_key)
        if cached_rows and cached_rows[0] == tuple(fingerprint):
            return copy.deepcopy(cached_rows[1])
        rows = []
        for registered in configs:
            config = dict(registered)
            config_path = Path(str(registered.get("configuration_path", ""))) if registered.get("configuration_path") else None
            if config_path is not None and config_path.is_file():
                # Snapshot-plan rows add queue-only fields. The saved full
                # trainer config is the source of truth for receipt matching.
                config = _read_json(config_path)
            work_id = str(config.get("work_id", ""))
            output = Path(str(registered.get("output_directory") or registered.get("output_reference")
                              or config.get("output_directory") or config.get("output_reference") or ""))
            attempt = _fit_attempt_record(self.ledger_path, self.campaign_id, work_id)
            if attempt:
                output = Path(str(attempt["output_directory"]))
            receipt_path = output / "execution_receipt.json" if str(output) not in ("", ".") else None
            if receipt_path and receipt_path.is_file():
                receipt = _read_json(receipt_path)
                effective_path = receipt_path.parent / "effective_config.json"
                effective = _read_json(effective_path) if effective_path.is_file() else None
                valid = (receipt.get("status") == "SUCCEEDED" and receipt.get("hidden_test_opened") is False
                    and isinstance(effective, Mapping) and effective == receipt.get("effective_config")
                    and config == effective)
                state = "completed" if valid else "invalid_receipt"
                if not valid:
                    receipt = None
            else:
                receipt, state = None, "not_run"
            rows.append({"config": config, "receipt_path": receipt_path if receipt_path and receipt_path.is_file() else None,
                "receipt": receipt, "state": state, "output_directory": output,
                "fit_gpu_seconds": _measured_gpu_seconds(self.ledger_path, work_id, self.campaign_id, self.ledger_id)
                    if work_id else None})
        task_cache[cache_key] = (tuple(fingerprint), copy.deepcopy(rows))
        self._candidate_rows_cache = task_cache
        return rows

    def _select_original_pair(self, task: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                              episode: Mapping[str, Any]) -> dict[str, Any]:
        """Select the preregistered original-objective candidate and fixed anchor from actual snapshots."""
        task_id = str(task.get("task_id", ""))
        anchor_id = _task_anchor_configuration_id(task)
        if not anchor_id:
            raise ValueError(f"Task {task_id} has no unique registered snapshot anchor")
        declared_seeds = task.get("shared_snapshot_seeds", episode.get("training_seeds"))
        if declared_seeds is None:
            # Legacy/minimal manifests used by infrastructure-only tests may
            # omit the paired-seed field. Production campaign tasks declare it.
            declared_seeds = sorted({int((row.get("config") or {}).get("seed")) for row in rows
                                     if (row.get("config") or {}).get("seed") is not None})
        seeds = [int(value) for value in declared_seeds]
        if not seeds or len(set(seeds)) != len(seeds):
            raise ValueError(f"Task {task_id} must declare unique snapshot seeds")
        primary = task.get("primary_metric", "")
        direction = str(task.get("primary_direction", ""))
        metric = str(primary.get("name", "") if isinstance(primary, Mapping) else primary)
        if isinstance(primary, Mapping):
            direction = str(primary.get("direction", direction))
        revision = task.get("C1_revision") or {}
        metric = str(metric or revision.get("original_primary_metric", revision.get("revised_primary_metric", "")))
        direction = str(direction or revision.get("original_direction", revision.get("revised_direction", "")))
        if not metric or direction not in {"minimize", "maximize"}:
            raise ValueError(f"Task {task_id} needs an explicit original selection objective")
        by_config: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            config = row.get("config") or {}
            if config.get("configuration_id"):
                by_config.setdefault(str(config["configuration_id"]), []).append(row)
        anchor_rows = by_config.get(str(anchor_id), [])

        def complete_group(group):
            by_seed = {int((entry.get("config") or {}).get("seed", -1)): entry for entry in group}
            if set(by_seed) != set(seeds):
                return None
            entries = [by_seed[seed] for seed in seeds]
            for entry in entries:
                receipt = entry.get("receipt") or {}
                if (entry.get("state") != "completed" or receipt.get("status") != "SUCCEEDED"
                        or receipt.get("hidden_test_opened") is not False
                        or (metric == "fit_gpu_seconds" and entry.get("fit_gpu_seconds") is None)
                        or (metric != "fit_gpu_seconds" and metric not in (receipt.get("validation_metrics") or {}))):
                    return None
            return entries

        anchor_complete = complete_group(anchor_rows)
        if anchor_complete is None:
            raise ValueError(f"Task {task_id} anchor snapshot is incomplete for both registered seeds")
        value_for = lambda row, key: (row.get("fit_gpu_seconds") if key == "fit_gpu_seconds"
                                      else (row.get("receipt", {}).get("validation_metrics") or {}).get(key))
        anchor_metric = sum(float(value_for(row, metric)) for row in anchor_complete) / len(seeds)
        anchor_costs = [row.get("fit_gpu_seconds") for row in anchor_complete]
        candidates = []
        for configuration_id, group in by_config.items():
            if configuration_id == str(anchor_id):
                continue
            complete = complete_group(group)
            if complete is None:
                continue
            values = [float(value_for(row, metric)) for row in complete]
            costs = [row.get("fit_gpu_seconds") for row in complete]
            candidates.append({"configuration_id": configuration_id, "rows": complete,
                "mean_metric": sum(values) / len(values), "metrics_by_seed": values,
                "gpu_seconds_by_seed": costs})
        if not candidates:
            raise ValueError(f"Task {task_id} has no completed non-anchor snapshot pair across both seeds")
        constraints = list(task.get("side_constraints", []))
        eligible = []
        for candidate in candidates:
            passes = True
            for constraint in constraints:
                name = str(constraint.get("metric", ""))
                margin = float(constraint.get("margin", float("nan")))
                if name == "fit_gpu_seconds":
                    c_values, a_values = candidate["gpu_seconds_by_seed"], anchor_costs
                else:
                    c_values = [value_for(row, name) for row in candidate["rows"]]
                    a_values = [value_for(row, name) for row in anchor_complete]
                if any(value is None or not math.isfinite(float(value)) for value in [*c_values, *a_values]):
                    passes = False
                    break
                if constraint.get("allowed_direction") == "at_most_anchor_times":
                    passes = all(float(left) <= margin * float(right) for left, right in zip(c_values, a_values))
                elif constraint.get("allowed_direction") == "at_least_anchor_times":
                    passes = all(float(left) >= margin * float(right) for left, right in zip(c_values, a_values))
                elif constraint.get("allowed_direction") == "no_more_than_absolute_drop":
                    passes = all(float(left) >= float(right) - margin for left, right in zip(c_values, a_values))
                else:
                    raise ValueError(f"Task {task_id} has an unsupported original-pair side constraint")
                if not passes:
                    break
            if passes:
                eligible.append(candidate)
        pool = eligible or candidates
        sign = 1 if direction == "minimize" else -1
        selected = min(pool, key=lambda value: (sign * value["mean_metric"], value["configuration_id"]))
        selected = dict(selected)
        pair_rows = [*anchor_complete, *selected["rows"]]
        record = {"status": "SUCCEEDED", "task_id": task_id, "objective": metric,
            "direction": direction, "anchor_configuration_id": str(anchor_id),
            "selected_non_anchor_configuration_id": selected["configuration_id"],
            "selected_metric_mean": selected["mean_metric"], "anchor_metric_mean": anchor_metric,
            "eligible_non_anchor_configuration_ids": sorted(value["configuration_id"] for value in eligible),
            "constraint_satisfied": bool(eligible), "fallback_used": not bool(eligible),
            "paired_seeds": seeds,
            "source_work_ids": [str((row.get("config") or {}).get("work_id")) for row in pair_rows],
            "source_receipt_paths": [str(row["receipt_path"]) for row in pair_rows]}
        output = self.campaign / "runs" / "shared_snapshot" / "pair_selection" / f"{task_id}.json"
        record["selection_record_path"] = str(output)
        _atomic_json_file(output, record)
        return {"rows": pair_rows, "record": record}

    def episode_resource_usage(self, item: Mapping[str, Any]) -> dict[str, float]:
        """Read actual active/model/GPU use for one live or replay cell."""
        work_id = str(item.get("work_id", ""))
        if not work_id:
            raise ValueError("Episode resource accounting requires its stable work_id")
        with self.ledger.connect() as db:
            calls = db.execute("SELECT COALESCE(SUM(elapsed_seconds),0) FROM forest_study_model_calls WHERE campaign_id=? AND work_id=?",
                               (self.campaign_id, work_id)).fetchone()[0]
            interval_columns = {row[1] for row in db.execute("PRAGMA table_info(forest_study_active_intervals)")}
            if "category" in interval_columns:
                cpu_actions = db.execute("SELECT COALESCE(SUM(elapsed_seconds),0) FROM forest_study_active_intervals WHERE campaign_id=? AND parent_work_id=? AND category!='aide_action_program'",
                                         (self.campaign_id, work_id)).fetchone()[0]
            elif "source" in interval_columns:
                cpu_actions = db.execute("SELECT COALESCE(SUM(elapsed_seconds),0) FROM forest_study_active_intervals WHERE campaign_id=? AND parent_work_id=? AND source!='aide_action_program'",
                                         (self.campaign_id, work_id)).fetchone()[0]
            else:
                cpu_actions = 0.0
            children = [row[0] for row in db.execute(
                "SELECT target_id FROM forest_study_edges WHERE campaign_id=? AND source_id=? AND relation='derived_from'",
                (self.campaign_id, work_id)).fetchall()]
            compute_ids = set(children)
            if children:
                placeholders = ",".join("?" for _ in children)
                for row in db.execute(f"SELECT supervisor_work_id FROM forest_study_work WHERE campaign_id=? AND work_id IN ({placeholders}) AND supervisor_work_id IS NOT NULL",
                                      [self.campaign_id, *children]).fetchall():
                    compute_ids.add(str(row[0]))
                for row in db.execute(f"SELECT supervisor_work_id FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id IN ({placeholders})",
                                      [self.campaign_id, *children]).fetchall():
                    compute_ids.add(str(row[0]))
        gpu_ms = active_ms = 0
        if compute_ids:
            placeholders = ",".join("?" for _ in compute_ids)
            with self.ledger.connect() as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "forest_compute_usage" in tables:
                    gpu_ms = int(db.execute(f"SELECT COALESCE(SUM(quantity),0) FROM forest_compute_usage WHERE ledger_id=? AND resource_type='gpu_allocated_ms' AND work_id IN ({placeholders})",
                        [self.ledger_id, *sorted(compute_ids)]).fetchone()[0])
                if "forest_compute_work" in tables:
                    active_ms = int(db.execute(f"SELECT COALESCE(SUM(accounted_ms),0) FROM forest_compute_work WHERE ledger_id=? AND work_id IN ({placeholders})",
                        [self.ledger_id, *sorted(compute_ids)]).fetchone()[0])
        model_seconds = float(calls or 0.0)
        native_active_seconds = 0.0
        with self.ledger.connect() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_study_aide_sessions'").fetchone()
            if table:
                row = db.execute("SELECT state_json FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?",
                    (self.campaign_id, work_id)).fetchone()
                if row:
                    native_active_seconds = float(json.loads(row[0]).get("local_active_seconds", 0.0))
        in_progress = max(0.0, time.monotonic() - self._active_episode_started[work_id]) if work_id in self._active_episode_started else 0.0
        active_before_compute = max(model_seconds + float(cpu_actions or 0.0),
                                    native_active_seconds + in_progress)
        gpu_seconds = gpu_ms / 1000.0
        active_wall_seconds = active_before_compute + active_ms / 1000.0
        return {"model_seconds": model_seconds, "gpu_seconds": gpu_seconds,
                "actual_gpu_seconds": gpu_seconds, "active_wall_seconds": active_wall_seconds}

    def episode_resource_limits(self, item: Mapping[str, Any]) -> dict[str, float]:
        payload = item.get("payload") or {}
        declared = payload.get("resource_budgets") or payload.get("resource_policy") or item.get("resource_budgets") or {}
        if not isinstance(declared, Mapping):
            raise ValueError("resource_budgets must be an object or null")
        defaults = {"gpu_seconds": math.inf, "active_wall_seconds": math.inf,
                    "model_seconds": math.inf}
        result = dict(defaults)
        if "actual_gpu_seconds" in declared and "gpu_seconds" not in declared:
            declared = {**dict(declared), "gpu_seconds": declared["actual_gpu_seconds"]}
        is_replay = _is_replay_episode(item) or _is_replay_episode(payload) or bool(declared.get("replay_only", False))
        for name in result:
            # The current study policy keeps only the declared physical GPU
            # ceiling. Model-call count, token totals, and accumulated active
            # wall time remain observations and do not stop an episode.
            if name != "gpu_seconds":
                continue
            if name in declared:
                if declared[name] is None:
                    result[name] = math.inf
                    continue
                value = float(declared[name])
                if not math.isfinite(value) or value < 0 or (value == 0 and not (name == "gpu_seconds" and is_replay)):
                    raise ValueError(f"resource_budgets.{name} must be finite and nonnegative only for replay GPU time; null means unlimited")
                result[name] = value
        return result

    def _model_provider_spec(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve a cell-specific saved provider before the campaign default."""
        payload = item.get("payload") or {}
        declared = payload.get("model_provider") if isinstance(payload, Mapping) else None
        if declared is None:
            declared = item.get("model_provider")
        if declared is None:
            declared = self.manifest.get("model_provider")
        if not isinstance(declared, Mapping) or not declared.get("id"):
            raise ValueError("Model work must name an explicit saved provider binding")
        return dict(declared)

    def episode_budget_status(self, item: Mapping[str, Any]) -> dict[str, Any]:
        used = self.episode_resource_usage(item)
        limits = self.episode_resource_limits(item)
        remaining_internal = {name: max(0.0, limits[name] - used[name]) for name in limits}
        remaining = {name: (None if math.isinf(value) else value)
                     for name, value in remaining_internal.items()}
        is_replay = _is_replay_episode(item) or _is_replay_episode(item.get("payload") or {}) or bool(
            ((item.get("payload") or {}).get("resource_policy") or {}).get("replay_only", False))
        exhausted = [name for name, value in remaining.items()
                     if value is not None and value <= 0 and not (is_replay and name == "gpu_seconds")]
        public_limits = {name: (None if math.isinf(value) else value) for name, value in limits.items()}
        return {"used": used, "limits": public_limits, "remaining": remaining,
                "exhausted": exhausted, "state": "budget_exhausted" if exhausted else "within_budget"}

    def _budgeted_episode_client(self, item: Mapping[str, Any], client: Any) -> Any:
        """Keep provider request timeout separate from cumulative cell time."""
        # The provider's timeout remains a per-request transport bound. The
        # campaign-level cumulative active and model seconds are observations.
        return copy.copy(client)

    def _replay_request_client(self, item: Mapping[str, Any], client: Any) -> Any:
        """Persist every replay physical request and its actual usage.

        Provider output recovery and transport retries call ``request_guard``
        once per actual provider request. Counting at that boundary avoids
        treating a high-level ``complete`` call as one request when it made
        several network/CLI attempts.
        """
        work_id = str(item["work_id"])
        bounded = copy.copy(client)
        original_guard = getattr(client, "request_guard", None)
        if original_guard is None:
            raise ValueError("Replay model calls require the provider's physical-request guard")

        def guard(event):
            phase = str(event.get("phase", ""))
            if phase == "before":
                request_id = str(uuid.uuid4())
                max_output = int(event.get("max_output_tokens") or 0)
                with self.ledger.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    # Retain each physical request and its eventual usage for
                    # audit without a cumulative episode request quota.
                    input_bytes = int(event.get("input_bytes") or 0)
                    db.execute("INSERT INTO forest_study_replay_requests VALUES(?,?,?,?,?,?,?,?,?)",
                        (request_id, self.campaign_id, work_id, "requested",
                         _json({"input_bytes": input_bytes, "max_output_tokens": max_output,
                                "attempt": event.get("attempt"), "api": event.get("api"),
                                "model": event.get("model")}), None, time.time(), None, None))
                try:
                    external_reservation = original_guard(event)
                except Exception:
                    with self.ledger.connect() as db:
                        db.execute("UPDATE forest_study_replay_requests SET status='rejected_before_send',finished_at=? WHERE physical_request_id=?",
                            (time.time(), request_id))
                    raise
                return {"forest_study_request_id": request_id,
                        "external_reservation": external_reservation,
                        "started_monotonic": time.monotonic()}
            reservation = event.get("reservation")
            if isinstance(reservation, Mapping):
                request_id = reservation.get("forest_study_request_id")
                if request_id:
                    try:
                        if phase == "after":
                            external_event = {**dict(event), "reservation": reservation.get("external_reservation")}
                            external_guard_result = original_guard(external_event)
                            usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
                            input_tokens = usage.get("input_tokens")
                            output_tokens = usage.get("output_tokens")
                            valid_usage = (isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0
                                           and isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0)
                            with self.ledger.connect() as db:
                                db.execute("UPDATE forest_study_replay_requests SET status=?,usage_json=?,finished_at=?,elapsed_seconds=? WHERE physical_request_id=?",
                                    ("completed" if valid_usage else "unknown", _json(dict(usage)) if valid_usage else _json({}),
                                     time.time(), max(0.0, time.monotonic() - float(reservation.get("started_monotonic", time.monotonic()))),
                                     str(request_id)))
                            return external_guard_result
                        if phase == "error":
                            external_event = {**dict(event), "reservation": reservation.get("external_reservation")}
                            result = original_guard(external_event)
                            ambiguous = bool(event.get("ambiguous", False))
                            status = "unknown" if ambiguous else "failed_before_response"
                            with self.ledger.connect() as db:
                                db.execute("UPDATE forest_study_replay_requests SET status=?,usage_json=?,finished_at=?,elapsed_seconds=? WHERE physical_request_id=?",
                                    (status, _json({}) if ambiguous else _json({"input_tokens": 0, "output_tokens": 0}),
                                     time.time(), max(0.0, time.monotonic() - float(reservation.get("started_monotonic", time.monotonic()))),
                                     str(request_id)))
                            return result
                    except Exception:
                        raise
            return original_guard(event)

        bounded.request_guard = guard
        return bounded

    def _replay_output_allowance(self, work_id: str) -> None:
        """Replay has no hidden per-cell output quota; provider allocation stays adaptive."""
        return None

    def _replay_token_budget(self, work_id: str) -> dict[str, Any]:
        with self.ledger.connect() as db:
            rows = db.execute("SELECT status,usage_json FROM forest_study_replay_requests WHERE campaign_id=? AND work_id=?",
                (self.campaign_id, str(work_id))).fetchall()
        calls = sum(row["status"] != "rejected_before_send" for row in rows)
        input_tokens = output_tokens = 0
        unknown = False
        for row in rows:
            usage = json.loads(row["usage_json"] or "{}")
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    if key == "input_tokens":
                        input_tokens += value
                    else:
                        output_tokens += value
                elif row["status"] not in {"rejected_before_send", "failed_before_response"}:
                    unknown = True
        return {"physical_calls": calls, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "usage_unknown": unknown,
            "limits": None, "within_budget": True}

    def execute_aide_node(self, item: Mapping[str, Any], *, node_id: str, code: str,
                          client: Any = None, gpu_devices=(), defer_gpu: bool = True,
                          context_read_measure=None) -> dict[str, Any]:
        """Execute one native AIDE Python node through the shared public action contract."""
        from .aide_runtime import run_action_program
        payload = dict(item["payload"])
        if not payload.get("action_bindings") or not payload.get("decision_schema"):
            payload = self.build_episode_request({**payload, "work_id": str(item["work_id"])})
        episode = {**dict(item), "payload": payload}
        budget = self.episode_budget_status(episode)
        allowance = 5.0
        with self.ledger.connect() as db:
            saved_program = db.execute("SELECT code,program_json FROM forest_study_aide_programs WHERE campaign_id=? AND work_id=? AND node_id=?",
                (self.campaign_id, str(item["work_id"]), str(node_id))).fetchone()
        if saved_program:
            if saved_program["code"] != code:
                raise ValueError("A native node ID was reused with different Python source")
            program = json.loads(saved_program["program_json"])
        else:
            program = run_action_program(code, timeout_seconds=allowance)
            with self.ledger.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO forest_study_aide_programs VALUES(?,?,?,?,?,?)",
                    (self.campaign_id, str(item["work_id"]), str(node_id), code, _json(program), time.time()))
        base = {"node_id": str(node_id), "stdout": str(program.get("stdout", "")),
                "exec_time": float(program.get("exec_time", 0.0)),
                "exc_type": program.get("exc_type"), "action_results": [],
                "action_work_ids": [], "pending_work_ids": [], "verified_metrics": []}
        decision = program.get("decision")
        def reject_decision(reason: str, proposed: Any = None) -> dict[str, Any]:
            frontier = _episode_source_frontier(payload, self._aide_action_history(str(item["work_id"])))
            signature = _semantic_action_proposal(proposed)
            prior = []
            for turn in self._aide_action_history(str(item["work_id"])):
                execution = turn.get("execution") if isinstance(turn, Mapping) else None
                if not isinstance(execution, Mapping) or execution.get("exc_type") != "DecisionValidationError":
                    continue
                if execution.get("validation_reason") == reason and execution.get("semantic_proposal") == signature \
                        and _json(turn.get("source_frontier")) == _json(frontier):
                    prior.append(execution)
            diagnosed = bool(prior)
            observation = {"same_source_bound_results": True, "new_evidence_added": False,
                "validation_error": reason, "diagnostic_previously_delivered": diagnosed}
            response = {"node_id": str(node_id), "state": "paused_no_progress" if diagnosed else "completed",
                "stdout": base["stdout"] + "\n" + _json({"decision_validation_error": reason,
                    "no_progress_observation": observation}), "exec_time": base["exec_time"],
                "exc_type": "DecisionValidationError", "exc_info": reason, "validation_reason": reason,
                "decision": copy.deepcopy(proposed), "semantic_proposal": signature,
                "source_frontier": frontier, "no_progress_observation": observation,
                "action_results": [], "action_work_ids": [], "pending_work_ids": [], "verified_metrics": []}
            return response
        if not isinstance(decision, Mapping):
            return reject_decision("Action program did not emit a valid JSON decision", decision)
        schema = payload["decision_schema"]
        required = schema.get("required", [])
        try:
            _validate_episode_schema(decision, schema)
        except ValueError as exc:
            return reject_decision(str(exc), decision)
        mode = str(decision.get("mode", ""))
        actions = decision.get("actions")
        if mode not in {"action", "submit"} or not isinstance(actions, list):
            return reject_decision("Action program output violates the registered public decision schema", decision)
        allowed = set(payload.get("allowed_decisions", []))
        if any(not isinstance(action, Mapping) or action.get("kind") not in allowed for action in actions):
            return reject_decision("Action program requested an operation outside registered bindings", decision)
        submission = decision.get("submission") if isinstance(decision.get("submission"), Mapping) else {}
        if mode == "submit":
            if actions:
                return reject_decision("A final submission cannot include unexecuted actions", decision)
            history = self._aide_action_history(str(item["work_id"]))
            validation = verify_episode_submission(payload, history, submission, runtime=self)
            if str(payload.get("arm", "")).upper() != "FULL_NO_GATE" and validation.get("valid") is not True:
                frontier = _episode_source_frontier(payload, self._aide_action_history(str(item["work_id"])))
                semantic_submission = _semantic_submission(submission)
                matching = [turn for turn in history
                    if _semantic_submission(turn.get("submission", {})) == semantic_submission
                    and _json(turn.get("source_frontier")) == _json(frontier)
                    and isinstance(turn.get("submission_validation"), Mapping)
                    and turn["submission_validation"].get("valid") is False
                    and _json(turn["submission_validation"].get("source_validation_signature",
                        _validation_signature(turn["submission_validation"])))
                        == _json(_validation_signature(validation))]
                diagnosed = any(isinstance(turn.get("no_progress_observation"), Mapping)
                    and _semantic_submission(turn["no_progress_observation"].get("repeated_submission", {})) == semantic_submission
                    for turn in matching)
                if matching:
                    feedback = ("The same final proposal received the same source-validation result against unchanged evidence. "
                        "Revise it, perform a different registered action, or identify the missing source evidence.")
                    validation = {**validation,
                        "state": "paused_no_progress" if diagnosed else "retryable",
                        "request_new_decision": not diagnosed,
                        "source_validation_reason": validation.get("reason"),
                        "source_validation_signature": _validation_signature(validation),
                        "reason": "repeated_invalid_submission_without_new_evidence",
                        "response_validation_feedback": None if diagnosed else feedback,
                        "pause_reason": ("The same invalid proposal recurred after diagnosis without a source-frontier change."
                            if diagnosed else None),
                        "no_progress_observation": {"action_work_ids": [],
                            "same_source_bound_results": True, "new_evidence_added": False,
                            "repeated_submission": semantic_submission,
                            "diagnostic_previously_delivered": diagnosed},
                        "source_frontier": frontier}
            base.update(state=validation.get("state", "completed"), submission_validation=validation,
                        submission=dict(submission),
                        stdout=base["stdout"] + "\n" + _json({"independent_submission_validation": validation}))
            return base
        if not actions:
            return reject_decision("Action mode requires at least one declared action", decision)
        try:
            _validate_action_bindings(payload, actions)
        except ValueError as exc:
            return reject_decision(str(exc), decision)
        if str(payload.get("arm", "")).upper() == "AIDE":
            prior_rows = [action for turn in self._aide_action_history(str(item["work_id"]))
                          for action in turn.get("actions", []) if isinstance(action, Mapping)
                          and action.get("state") == "completed" and not _is_context_read_action(action)]
            prior_by_id = {str(action.get("action_work_id")): action for action in prior_rows
                           if action.get("action_work_id") and isinstance(action.get("result"), Mapping)}
            duplicates = [prior_by_id.get(str(action.get("action_work_id"))) for action in actions
                          if isinstance(action, Mapping)]
            if len(duplicates) == len(actions) and duplicates and all(
                    prior is not None for prior in duplicates):
                frontier = _episode_source_frontier(payload, self._aide_action_history(str(item["work_id"])))
                identities = sorted(str(row.get("action_work_id")) for row in duplicates)
                same_source_diagnostic = any(
                    isinstance(turn.get("no_progress_observation"), Mapping)
                    and turn["no_progress_observation"].get("action_work_ids") == identities
                    and _json(turn.get("source_frontier")) == _json(frontier)
                    for turn in self._aide_action_history(str(item["work_id"])))
                feedback = "The requested registered action IDs already completed with the same saved source-bound results. This program added no new evidence. Choose a different available operation, use the existing evidence to submit, or state what new source-bound evidence is needed."
                observation = {"action_work_ids": identities, "same_source_bound_results": True,
                    "new_evidence_added": False, "diagnostic_previously_delivered": same_source_diagnostic}
                base.update(state="paused_no_progress" if same_source_diagnostic else "completed",
                    exc_type="RepeatedActionWithoutNewEvidence",
                    exc_info=feedback, action_results=duplicates,
                    action_work_ids=[str(row.get("action_work_id")) for row in duplicates],
                    no_progress_observation=observation,
                    source_frontier=frontier,
                    stdout=base["stdout"] + "\n" + _json({"no_progress_observation": observation,
                        "pause_reason": ("The same completed actions recurred after diagnosis without a source-frontier change."
                            if same_source_diagnostic else None), "diagnostic": feedback}))
                return base
        def measure_page(page, *, prefix_results=(), action_work_id="", action=None):
            observed = [copy.deepcopy(dict(row)) for row in prefix_results if isinstance(row, Mapping)]
            observed.append({"action_work_id": action_work_id, "kind": "analysis", "state": "completed",
                "result": {"operation": "read_context_segment", **copy.deepcopy(dict(page))}})
            rendered_page = {"requested_decision": dict(decision), "observed_action_results": observed}
            candidate_stdout = base["stdout"] + "\n" + _json(rendered_page)
            if callable(context_read_measure):
                return int(context_read_measure(candidate_stdout))
            return len(candidate_stdout)
        action_result = self.execute_decision_actions(episode, actions, gpu_devices=gpu_devices,
            submission=submission, defer_gpu=defer_gpu,
            context_read_measure=measure_page,
            context_response_capacity=self._context_response_capacity(client) if client is not None else None,
            context_response_capacity_sources=copy.deepcopy(getattr(self, "_context_capacity_sources", [])))
        action_rows = list(action_result.get("actions", []))
        page_diagnostic = self._context_read_no_progress(str(item["work_id"]), payload, action_rows)
        if page_diagnostic:
            prior_observation = action_result.get("no_progress_observation")
            observation = {**(dict(prior_observation) if isinstance(prior_observation, Mapping) else {}),
                **page_diagnostic["no_progress_observation"]}
            action_result = {**action_result,
                "state": "paused_no_progress" if page_diagnostic["state"] == "paused_no_progress" else "completed",
                "no_progress_observation": observation,
                "response_validation_feedback": page_diagnostic.get("response_validation_feedback"),
                "pause_reason": page_diagnostic.get("pause_reason"),
                "source_frontier": page_diagnostic["source_frontier"]}
            action_rows = list(action_result.get("actions", []))
        pending_ids = [str(row["action_work_id"]) for row in action_rows
                       if row.get("state") in {"retryable", "awaiting_reconciliation"} and row.get("action_work_id")]
        verified = self._aide_verified_metrics(payload, action_rows)
        rendered = {"requested_decision": dict(decision), "observed_action_results": action_rows}
        if action_result.get("response_validation_feedback"):
            rendered["no_progress_feedback"] = action_result["response_validation_feedback"]
        base.update(state=action_result["state"], action_result=action_result,
                    action_results=action_rows,
                    action_work_ids=[str(row["action_work_id"]) for row in action_rows if row.get("action_work_id")],
                    pending_work_ids=pending_ids, verified_metrics=verified,
                    no_progress_observation=action_result.get("no_progress_observation"),
                    source_frontier=action_result.get("source_frontier"),
                    response_validation_feedback=action_result.get("response_validation_feedback"),
                    stdout=base["stdout"] + "\n" + _json(rendered))
        return base

    def _record_active_interval(self, work_id: str, seconds: float, source: str) -> None:
        if seconds <= 0:
            return
        finished = time.time()
        with self.ledger.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(forest_study_active_intervals)")}
            if "category" in columns:
                db.execute("INSERT INTO forest_study_active_intervals(interval_id,campaign_id,parent_work_id,action_work_id,category,started_at,finished_at,elapsed_seconds,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), self.campaign_id, work_id, source + ":" + str(uuid.uuid4()),
                     source, finished - float(seconds), finished, float(seconds), finished))
            elif "source" in columns:
                db.execute("INSERT INTO forest_study_active_intervals(campaign_id,parent_work_id,source,elapsed_seconds,recorded_at) VALUES(?,?,?,?,?)",
                    (self.campaign_id, work_id, source, float(seconds), finished))

    def _aide_action_history(self, work_id: str) -> list[dict[str, Any]]:
        with self.ledger.connect() as db:
            row = db.execute("SELECT state_json FROM forest_study_aide_sessions WHERE campaign_id=? AND work_id=?",
                (self.campaign_id, work_id)).fetchone()
        if not row:
            return []
        return list(json.loads(row[0]).get("action_history", []))

    def _context_read_no_progress(self, work_id: str, payload: Mapping[str, Any],
                                  actions: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        history = self._aide_action_history(work_id)
        frontier = _episode_source_frontier(payload, history)
        diagnostic = _context_page_no_progress(actions, history, frontier)
        if not diagnostic:
            return None
        nonread_rows = [row for row in actions
            if isinstance(row, Mapping) and not _is_context_read_action(row)]
        previous_results = {str(row.get("action_work_id")): row.get("result")
            for turn in history for row in turn.get("actions", [])
            if isinstance(row, Mapping) and row.get("state") == "completed"
            and row.get("action_work_id") and not _is_context_read_action(row)}
        science_unchanged = all(row.get("state") == "completed"
            and str(row.get("action_work_id")) in previous_results
            and row.get("result") == previous_results[str(row.get("action_work_id"))]
            for row in nonread_rows)
        return {**diagnostic, "source_frontier": frontier} if science_unchanged else None

    @staticmethod
    def _aide_verified_metrics(payload: Mapping[str, Any], actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        task = payload.get("task_contract") or {}
        metric_info = dict((payload.get("factual_changes") or {}).get("C1_revision") or {})
        metric = str(metric_info.get("revised_primary_metric") or task.get("revised_primary_metric") or "")
        direction = str(metric_info.get("revised_direction") or task.get("revised_direction") or "")
        if not metric:
            primary = task.get("primary_metric") or {}
            if isinstance(primary, Mapping):
                metric = str(primary.get("name") or "")
                direction = str(primary.get("direction") or direction)
            else:
                metric = str(primary)
                direction = str(task.get("primary_direction") or direction)
        maximize = direction.lower() in {"maximize", "max", "higher"}
        results = []
        for action in actions:
            if action.get("state") != "completed":
                continue
            result = action.get("result") or {}
            binding = next((value for value in payload.get("action_bindings", [])
                            if isinstance(value, Mapping) and value.get("work_id") == action.get("action_work_id")), {})
            evidence_sources: list[tuple[str, str | None, Any, Any]] = []
            action_kind = str(action.get("kind", binding.get("kind", "")))
            if action_kind == "rescore":
                source_receipt = binding.get("reference_receipt_path")
                result_path = binding.get("output_path")
                if source_receipt and result_path:
                    try:
                        scored = _read_json(Path(str(result_path)))
                        source = _read_json(Path(str(source_receipt)))
                        score = (scored.get("metrics") or {}).get(metric)
                        actual = (source.get("validation_metrics") or {}).get(metric)
                        if (scored.get("status") == "SUCCEEDED" and scored.get("hidden_test_opened") is False
                                and actual is not None and score is not None
                                and math.isclose(float(score), float(actual), rel_tol=1e-6, abs_tol=1e-8)):
                            evidence_sources.append((str(source_receipt), str(binding.get("configuration_id", "")),
                                                     binding.get("seed"), float(score)))
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
            elif action_kind == "fit":
                output = result.get("output_directory") or binding.get("output_directory")
                path = Path(str(output)) / "execution_receipt.json" if output else None
                if path and path.is_file():
                    evidence_sources.append((str(path), str(binding.get("configuration_id", "")),
                                             binding.get("seed"), None))
            elif action_kind == "infer":
                spec = binding.get("inference_spec") or {}
                output = result.get("output_directory") or spec.get("output_directory")
                path = Path(str(output)) / "inference_receipt.json" if output else None
                if path and path.is_file():
                    evidence_sources.append((str(path), str(binding.get("configuration_id", "")),
                                             binding.get("seed"), None))
            elif action_kind == "analysis":
                for entry in binding.get("evidence", []):
                    if isinstance(entry, Mapping) and entry.get("receipt_path"):
                        evidence_sources.append((str(entry["receipt_path"]),
                            str(entry.get("configuration_id", "")), entry.get("seed"), None))
            for source_path, configuration_id, seed, rescored_value in evidence_sources:
                try:
                    source = _read_json(Path(source_path))
                    if source.get("hidden_test_opened") is not False or source.get("status") != "SUCCEEDED":
                        continue
                    source_metrics = source.get("validation_metrics") or source.get("metrics") or {}
                    value = rescored_value if rescored_value is not None else source_metrics.get(metric)
                    if value is None or not math.isfinite(float(value)):
                        continue
                    if binding.get("configuration_id") and source.get("configuration_id") not in (
                            None, binding.get("configuration_id")):
                        continue
                    results.append({"value": float(value), "maximize": maximize,
                        "configuration_id": configuration_id or action.get("configuration_id"),
                        "seed": seed if seed is not None else action.get("seed"),
                        "receipt_path": source_path})
                except (OSError, ValueError, TypeError):
                    continue
        return results

    def _episode_rescore_binding(self, episode: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
        config = row["config"]
        parent = str(episode.get("work_id", "episode"))
        work_id = _action_work_id(parent, "score", str(config["configuration_id"]), str(config.get("seed", "")))
        receipt_path = Path(row["receipt_path"])
        receipt = row["receipt"]
        return {"kind": "rescore", "action_name": "score", "work_id": work_id,
            "configuration_id": config["configuration_id"], "seed": config.get("seed"),
            "campaign_path": str(self.campaign), "config_path": str(receipt_path.parent / "effective_config.json"),
            "prediction_path": str(receipt_path.parent / str(receipt["validation_prediction_file"])),
            "reference_receipt_path": str(receipt_path),
            "data_root": str(self.campaign / "data" / "researcher"),
            "output_path": str(self.campaign / "runs" / "episodes" / str(episode.get("work_id")) /
                               "rescored" / f"{config['configuration_id']}-{config.get('seed')}.json")}

    def execute_fit(self, item: Mapping[str, Any], *, gpu_devices: Sequence[str] = ()) -> dict[str, Any]:
        from .gpu_supervisor import ComputeSupervisor
        payload = dict(item["payload"])
        config_path = Path(str(payload["configuration_path"])).resolve()
        output_base = Path(str(payload["output_directory"])).resolve()
        config = _read_json(config_path)
        if config.get("work_id") != item["work_id"]:
            raise ValueError("The saved fit config work_id differs from the registered work item")
        # Queue metadata may duplicate config fields. When it does, require exact
        # values, then pass the original saved full config to the training CLI.
        for key in config:
            if key in payload and payload[key] != config[key]:
                raise ValueError(f"Manifest/config disagreement for {item['work_id']} field {key}")
        phase = str(config["phase"])
        if phase not in ("pilot", "calibration", "formal"):
            raise ValueError(f"Unsupported real fit phase: {phase}")
        if not gpu_devices:
            raise ValueError("Scientific fits require an explicit physical --gpu-device so the original supervisor meters GPU allocation")
        module = str(self.manifest.get("commands", {}).get("module", "research.experiments.training_runtime"))
        python = str(self.manifest.get("commands", {}).get("fitter_python") or sys.executable)
        source_root = Path(str(self.manifest.get("commands", {}).get("source_root") or Path(__file__).resolve().parents[2])).resolve()
        if not source_root.is_dir():
            raise FileNotFoundError(f"Manifest training source root is missing: {source_root}")
        output_base.mkdir(parents=True, exist_ok=True)
        supervisor = ComputeSupervisor(self.ledger_path, self.ledger_id)
        logical_id = str(item["work_id"])
        with self.ledger.connect() as db:
            attempts = db.execute("SELECT * FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id=? ORDER BY attempt_number",
                                  (self.campaign_id, logical_id)).fetchall()
            latest = dict(attempts[-1]) if attempts else None
            legacy_exists = _supervisor_has_work(self.ledger_path, logical_id)
            now = time.time()
            if latest is None:
                # Existing accepted work keeps its original physical identity. A
                # fresh logical fit also starts with that identity for continuity
                # with the registered queue and study receipt.
                db.execute("INSERT INTO forest_study_fit_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                    (self.campaign_id, logical_id, 0, logical_id, str(output_base),
                     "accepted" if legacy_exists else "prepared", None, now, now))
                latest = dict(db.execute("SELECT * FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id=? AND attempt_number=0",
                    (self.campaign_id, logical_id)).fetchone())
        physical_id = str(latest["supervisor_work_id"])
        output = Path(str(latest["output_directory"])).resolve()
        output.mkdir(parents=True, exist_ok=True)
        command = [python, "-m", module, "--campaign", str(self.campaign), "--phase", phase,
                   "--config", str(config_path), "--output", str(output), "--work-id", logical_id]
        attempt_ids = [str(row["supervisor_work_id"]) for row in attempts]
        if not attempt_ids:
            attempt_ids = [logical_id]
        with self.ledger.connect() as db:
            try:
                placeholders = ",".join("?" for _ in attempt_ids)
                used_gpu_ms = int(db.execute(
                    f"SELECT COALESCE(SUM(quantity),0) FROM forest_compute_usage WHERE ledger_id=? AND resource_type='gpu_allocated_ms' AND work_id IN ({placeholders})",
                    [self.ledger_id, *attempt_ids]).fetchone()[0])
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                used_gpu_ms = 0
        # Every retry shares the original logical fit allowance. A new physical
        # ID gives the supervisor safe attempt provenance, not a fresh budget.
        allotted_gpu_ms = int(float(payload["fit_timeout_seconds"]) * 1000 * len(gpu_devices))
        remaining_gpu_ms = allotted_gpu_ms - used_gpu_ms
        attempt_timeout = min(float(payload["fit_timeout_seconds"]),
                              remaining_gpu_ms / 1000 / max(1, len(gpu_devices)))

        def submit_current():
            if remaining_gpu_ms <= 0 or attempt_timeout <= 0:
                return {"work_id": physical_id, "status": "budget_exhausted",
                        "error": "Logical fit GPU allowance is exhausted across physical attempts",
                        "elapsed_ms": used_gpu_ms, "allowance_ms": allotted_gpu_ms}
            return supervisor.submit(command, output, work_id=physical_id,
                configuration={"phase": phase, "effective_config": config,
                    "source_files": [str((source_root / "research/experiments/training_runtime.py").resolve()), str(config_path)]},
                timeout_seconds=attempt_timeout, gpu_devices=list(gpu_devices),
                cwd=".", env={"PYTHONPATH": str(source_root)}, phase=phase)
        if _supervisor_has_work(self.ledger_path, physical_id):
            status = supervisor.status(physical_id)
            current = str(status.get("status", "unknown"))
            prior_receipt_path = output / "execution_receipt.json"
            can_recover = (current == "failed" and int(latest["attempt_number"]) == 0
                           and _known_source_preflight_failure(supervisor, physical_id)
                           and not prior_receipt_path.exists())
            if can_recover:
                # A terminal failed physical attempt is preserved as evidence.
                # Retry only by creating a distinct, durable physical attempt;
                # the logical work ID and full scientific config stay unchanged.
                # This one bounded recovery is limited to the observed source
                # preflight failure, which occurs before the trainer starts.
                with self.ledger.connect() as db:
                    db.execute("UPDATE forest_study_fit_attempts SET state=?,result_json=?,updated_at=? WHERE campaign_id=? AND logical_work_id=? AND attempt_number=?",
                        (current, _json(status), time.time(), self.campaign_id, logical_id, int(latest["attempt_number"])))
                    number = int(latest["attempt_number"]) + 1
                    physical_id = f"{logical_id}--attempt-{number:02d}"
                    output = output_base / "attempts" / physical_id
                    db.execute("INSERT INTO forest_study_fit_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                        (self.campaign_id, logical_id, number, physical_id, str(output), "prepared", None,
                         time.time(), time.time()))
                output.mkdir(parents=True, exist_ok=True)
                command = [python, "-m", module, "--campaign", str(self.campaign), "--phase", phase,
                           "--config", str(config_path), "--output", str(output), "--work-id", logical_id]
                status = submit_current()
            elif current == "failed" and prior_receipt_path.exists():
                # A receipt alongside a failed supervisor observation is
                # ambiguous and must be reconciled, never treated as permission
                # to launch another physical fit.
                status = {**status, "status": "reconciliation",
                          "error": "A fit output receipt exists for a failed supervisor attempt; reconcile before retry"}
            # A failed attempt may have completed without a receipt. The next
            # invocation reconciles this new attempt before any further launch.
        else:
            status = submit_current()
        if status.get("status") not in ("completed", "failed", "cancelled", "budget_exhausted", "reconciliation"):
            status = supervisor.wait(physical_id, timeout=1.0)
        receipt_path = output / "execution_receipt.json"
        receipt = _read_json(receipt_path) if receipt_path.is_file() else None
        if status.get("status") == "completed":
            if not receipt or receipt.get("status") != "SUCCEEDED":
                raise RuntimeError("Supervisor reports completion without a successful training receipt")
            if receipt.get("effective_config") != config:
                raise ValueError("Training receipt does not preserve the exact submitted config")
            if receipt.get("hidden_test_opened") is not False:
                raise ValueError("Fit receipt did not affirm researcher-only validation protocol")
        with self.ledger.connect() as db:
            db.execute("UPDATE forest_study_fit_attempts SET state=?,result_json=?,updated_at=? WHERE campaign_id=? AND logical_work_id=? AND supervisor_work_id=?",
                (str(status.get("status", "unknown")), _json(status), time.time(), self.campaign_id, logical_id, physical_id))
        return {"supervisor": status, "supervisor_work_id": physical_id, "attempt_number": int(latest["attempt_number"] if physical_id == latest["supervisor_work_id"] else number),
                "receipt": receipt, "configuration_path": str(config_path), "output_directory": str(output)}

    def execute_model_episode(self, item: Mapping[str, Any], *, client: Any,
                              gpu_devices: Sequence[str] = (), history: Sequence[Mapping[str, Any]] = (),
                              defer_gpu: bool = False) -> dict[str, Any]:
        """Obtain and validate one actual structured scientific action proposal.

        Deterministic execution is separately routed through the registered
        ``action_work_id``; a generated recommendation is never itself a result.
        """
        client = self._budgeted_episode_client(item, client)
        payload = dict(item["payload"])
        prior_result = item.get("result_json")
        if isinstance(prior_result, str):
            try:
                prior_result = json.loads(prior_result)
            except ValueError:
                prior_result = {}
        elif not isinstance(prior_result, Mapping):
            prior_result = {}
        validation_feedback = prior_result.get("response_validation_feedback")
        saved_prepared = prior_result.get("prepared_episode_payload")
        if (validation_feedback and isinstance(saved_prepared, Mapping)
                and not prior_result.get("no_progress_observation")):
            payload = dict(saved_prepared)
        if validation_feedback:
            payload["response_validation_feedback"] = validation_feedback
        if not payload.get("messages") or not isinstance(payload.get("decision_schema"), dict):
            # This is a fresh model turn, so rebuild a dynamic workflow frontier
            # from current receipts. Pending saved decisions are resumed in
            # run_one without entering this method and keep their original IDs.
            payload = self.build_episode_request({**payload, "work_id": str(item["work_id"])})
        payload = self._attach_public_context({**payload, "work_id": str(item["work_id"])}, payload, history=history)
        is_replay = _is_replay_episode(payload) or _is_replay_episode(item)
        if is_replay:
            client = self._replay_request_client(item, client)
            episode_item = {**dict(item), "payload": payload}
        episode_item = {**dict(item), "payload": payload}
        request_id = str(uuid.uuid4())
        from research.agents.policy import ensure_research_messages
        output_stage = str(payload.get("output_stage", "experiment"))
        delivered_messages = copy.deepcopy(payload["messages"])
        if history:
            arm = str(payload.get("arm", payload.get("design_arm", ""))).upper()
            context = _treatment_history_view(arm, history)
            delivered_messages.append({"role": "user", "content": "Observed results from completed source-bound operations in this episode:\n" + _json(context)})
        if payload.get("response_validation_feedback"):
            delivered_messages.append({"role": "user", "content":
                "The previous actual response was saved in the episode ledger but rejected before any action was executed. "
                "Correct the response using the original registered context. Validation feedback: " +
                _json(payload["response_validation_feedback"])})
        if delivered_messages:
            schema_note = "\n\nReturn exactly one JSON object satisfying this decision contract:\n" + _json(payload["decision_schema"])
            delivered_messages[-1]["content"] = str(delivered_messages[-1].get("content", "")) + schema_note
        delivered_messages = ensure_research_messages(delivered_messages, client.config, output_stage)
        source_messages = copy.deepcopy(delivered_messages)
        delivered_messages, context_encoding = _intern_delivered_messages(delivered_messages)
        max_output_tokens = payload.get("max_output_tokens")
        if max_output_tokens is None:
            from research.agents.output_budget import output_allocation
            allocation = output_allocation(client.config, delivered_messages, stage=output_stage)
            max_output_tokens = allocation["tokens"]
        else:
            allocation = {"tokens": int(max_output_tokens), "source": "registered_request"}
        request_client = copy.copy(client)
        client_config = getattr(client, "config", None)
        codex_transport = ((getattr(client, "provider", {}) or {}).get("kind") == "codex_cli"
                           or getattr(client, "api", None) == "codex_cli")
        transport_schema = (_codex_strict_schema(payload["decision_schema"]) if codex_transport
                            else copy.deepcopy(payload["decision_schema"]))
        transport_name = (getattr(request_client, "api", None)
                          or request_client.provider.get("kind") or "unknown")
        trace_reference = _transport_trace_reference(
            self.campaign, request_id, request_client.provider)
        if isinstance(client_config, Mapping):
            request_client.config = copy.deepcopy(dict(client_config))
            request_client.config["_forest_action_schema"] = transport_schema
            request_client.config["output_stage"] = output_stage
        elif trace_reference:
            request_client.config = {}
        if trace_reference:
            request_client.config["_forest_transport_trace_path"] = trace_reference
        transport_endpoint = None
        transport_payload = None
        build_request = getattr(request_client, "build_request", None)
        if callable(build_request):
            transport_endpoint, transport_payload = build_request(delivered_messages,
                json_mode=True, max_output_tokens=int(max_output_tokens))
        elif transport_name == "codex_cli":
            from research.agents.codex_transport import request_payload as codex_request_payload
            transport_payload = codex_request_payload(delivered_messages, True,
                request_client.config.get("_forest_action_schema"))
        request = {"request_id": request_id, "work_id": item["work_id"], "provider_id": client.provider.get("id"),
                   "model": client.provider.get("model"), "stage": output_stage,
                   "messages": delivered_messages, "original_messages": source_messages,
                   "context_encoding": context_encoding,
                   "decision_schema": payload["decision_schema"],
                   "transport_decision_schema": transport_schema,
                   "provider_snapshot": {"id": client.provider.get("id"),
                       "kind": client.provider.get("kind"), "model": client.provider.get("model"),
                       "allow_paid": client.provider.get("allow_paid")},
                   "provider_api": transport_name,
                   "effective_config": _audit_config(getattr(request_client, "config", {})),
                   "reasoning_effort": getattr(request_client, "config", {}).get("reasoning_effort")
                       if isinstance(getattr(request_client, "config", {}), Mapping) else None,
                   "timeout_seconds": getattr(request_client, "timeout", None),
                   "output_allocation": allocation,
                   "transport_trace_path": trace_reference,
                   "transport_endpoint": transport_endpoint,
                   "transport_payload": transport_payload}
        with self.ledger.connect() as db:
            started_at = time.time()
            started_clock = time.monotonic()
            db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,response_json,usage_json,status,recorded_at,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (request_id, self.campaign_id, str(item["work_id"]), _json(request), None, None, "requested", started_at, started_at))
        try:
            result = request_client.complete(delivered_messages, json_mode=True,
                max_output_tokens=int(max_output_tokens),
                output_stage=output_stage)
            text = result.get("text")
            usage = result.get("usage") or {"input_tokens": None, "output_tokens": None, "cost": None,
                                             "cost_source": "unknown"}
            finished_at = time.time()
            elapsed_seconds = time.monotonic() - started_clock
            response_record = {"text": text, "model": result.get("model"),
                               "response_id": result.get("response_id")}
            # Preserve every real provider response and its actual usage before
            # parsing or judging it. Malformed/invalid outputs remain auditable.
            with self.ledger.connect() as db:
                db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                    (_json(response_record), _json(usage), finished_at, elapsed_seconds, request_id))
            parsed_decision = None
            try:
                if not isinstance(text, str):
                    raise ValueError("Actual model response did not contain structured text")
                decision = _restore_strict_optional_nulls(json.loads(text), payload["decision_schema"])
                parsed_decision = decision
                schema = payload["decision_schema"]
                _validate_episode_schema(decision, schema)
                choices = payload.get("allowed_decisions")
                requested_actions = decision.get("actions")
                if requested_actions is not None:
                    if not isinstance(requested_actions, list):
                        raise ValueError("Model actions must be a list of declared operation requests")
                    if not isinstance(decision.get("submission"), Mapping):
                        raise ValueError("Multi-action episode response needs a measured submission object")
                    mode = str(decision.get("mode", "action"))
                    if mode not in {"action", "submit"}:
                        raise ValueError("Episode mode must be action or submit")
                    if mode == "action" and not requested_actions:
                        raise ValueError("An action-mode response needs a nonempty list of declared operations")
                    if mode == "submit" and requested_actions:
                        raise ValueError("A submit-mode response cannot add unexecuted operations")
                    requested_kinds = [str(action.get("kind", "")) for action in requested_actions if isinstance(action, Mapping)]
                    if len(requested_kinds) != len(requested_actions) or any(not kind for kind in requested_kinds):
                        raise ValueError("Every submitted action must have a kind")
                    if choices is not None and any(kind not in choices for kind in requested_kinds):
                        raise ValueError("Model proposed an action outside the explicit manifest choices")
                    _validate_action_bindings(payload, requested_actions)
                else:
                    requested_actions = [{key: value for key, value in decision.items() if key in ("action", "configuration_id", "seed")}]
                    if choices is not None and decision.get("action") not in choices:
                        raise ValueError("Model proposed an action outside the explicit manifest choices")
            except (ValueError, TypeError, KeyError) as validation_error:
                frontier = _episode_source_frontier(payload, history)
                proposal = _semantic_raw_proposal(text)
                prior_rejection = prior_result.get("validation_rejection")
                same_rejection = (isinstance(prior_rejection, Mapping)
                    and prior_rejection.get("proposal") == proposal
                    and prior_rejection.get("reason") == str(validation_error)
                    and _json(prior_rejection.get("source_frontier")) == _json(frontier))
                diagnosed = bool(same_rejection and prior_rejection.get("diagnostic_delivered"))
                feedback = {"error_type": type(validation_error).__name__, "reason": str(validation_error),
                            "request_id": request_id, "raw_response_persisted": True}
                rejection = {"proposal": proposal, "reason": str(validation_error),
                    "source_frontier": frontier, "diagnostic_delivered": True,
                    "previous_request_id": request_id}
                no_progress = {"same_source_bound_results": True, "new_evidence_added": False,
                    "validation_error": str(validation_error), "diagnostic_previously_delivered": diagnosed}
                with self.ledger.connect() as db:
                    db.execute("UPDATE forest_study_work SET state=?,result_json=?,decision_json=NULL,updated_at=? WHERE campaign_id=? AND work_id=?",
                        ("paused_no_progress" if diagnosed else "retryable",
                         _json({"request_id": request_id,
                                "response_validation_feedback": None if diagnosed else feedback,
                                "validation_rejection": rejection,
                                "no_progress_observation": no_progress if diagnosed else None,
                                "prepared_episode_payload": payload, "action_history": list(history)}),
                         finished_at, self.campaign_id, str(item["work_id"])))
                return {"request_id": request_id, "decision": {}, "usage": usage,
                        "model": result.get("model"), "response_id": result.get("response_id"),
                        "action_result": {"state": "paused_no_progress" if diagnosed else "retryable", "valid": False,
                            "request_new_decision": not diagnosed, "reason": str(validation_error),
                            "pause_reason": "The same invalid proposal recurred after diagnosis without a source-frontier change." if diagnosed else None,
                            "raw_response_persisted": True,
                            "no_progress_observation": no_progress if diagnosed else None},
                        "response_validation_feedback": None if diagnosed else feedback,
                        "no_progress_observation": no_progress if diagnosed else None,
                        "validation_rejection": rejection,
                        "prepared_episode_payload": payload}
            with self.ledger.connect() as db:
                db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                    (_json(response_record), _json(usage), finished_at, elapsed_seconds, request_id))
                db.execute("UPDATE forest_study_work SET state='running',result_json=?,decision_json=?,updated_at=? WHERE campaign_id=? AND work_id=?",
                    (_json({"request_id": request_id, "decision_received": True, "action_history": list(history),
                            "prepared_episode_payload": payload}),
                     _json(decision), finished_at, self.campaign_id, str(item["work_id"])))
            if decision.get("mode") == "submit":
                submission = decision.get("submission") if isinstance(decision.get("submission"), Mapping) else {}
                if _is_replay_episode(payload) or _is_replay_episode(item):
                    from .mechanism_replay import verify_replay_submission
                    action_result = verify_replay_submission(self, episode_item, history, submission)
                else:
                    action_result = verify_episode_submission(payload, history, submission, runtime=self)
                if payload.get("arm") != "FULL_NO_GATE" and action_result.get("valid") is not True:
                    frontier = _episode_source_frontier(payload, history)
                    semantic_submission = _semantic_submission(submission)
                    repeated_prior = [turn for turn in history
                        if _semantic_submission(turn.get("submission", {})) == semantic_submission
                        and _json(turn.get("source_frontier")) == _json(frontier)
                        and isinstance(turn.get("submission_validation"), Mapping)
                        and turn["submission_validation"].get("valid") is False
                        and _json(turn["submission_validation"].get("source_validation_signature",
                            _validation_signature(turn["submission_validation"])))
                            == _json(_validation_signature(action_result))]
                    if repeated_prior:
                        already_diagnosed = any(
                            isinstance(turn.get("no_progress_observation"), Mapping)
                            and _semantic_submission(turn["no_progress_observation"].get("repeated_submission", {})) == semantic_submission
                            and _json(turn.get("source_frontier")) == _json(frontier)
                            for turn in repeated_prior)
                        feedback = ("The same final proposal received the same source-validation result against unchanged evidence. "
                            "Revise the submission using the validation feedback, execute a different available action, "
                            "or identify what new source evidence is required.")
                        action_result = {**action_result,
                            "state": "paused_no_progress" if already_diagnosed else "retryable",
                            "request_new_decision": not already_diagnosed,
                            "source_validation_reason": action_result.get("reason"),
                            "source_validation_signature": _validation_signature(action_result),
                            "reason": "repeated_invalid_submission_without_new_evidence",
                            "response_validation_feedback": None if already_diagnosed else feedback,
                            "pause_reason": ("The same invalid proposal recurred after a diagnostic without a source-frontier change. "
                                "This episode is paused until new evidence arrives or an operator resumes it.") if already_diagnosed else None,
                            "no_progress_observation": {"action_work_ids": [],
                                "same_source_bound_results": True, "new_evidence_added": False,
                                "repeated_submission": semantic_submission,
                                "diagnostic_previously_delivered": already_diagnosed}}
            else:
                page_binding_id = next((str(action.get("action_work_id")) for action in requested_actions
                    if isinstance(action, Mapping) and action.get("kind") == "read_context_segment"), "")
                page_measure = self._public_page_measure_callback(request_client, payload, history, page_binding_id)
                action_result = self.execute_decision_actions(episode_item, requested_actions, gpu_devices=gpu_devices,
                    submission=decision.get("submission") if isinstance(decision.get("submission"), Mapping) else None,
                    defer_gpu=defer_gpu, context_read_measure=page_measure,
                    context_response_capacity=self._context_response_capacity(request_client),
                    context_response_capacity_sources=copy.deepcopy(getattr(self, "_context_capacity_sources", [])))
                previous_results = {
                    str(row.get("action_work_id")): row.get("result")
                    for turn in history for row in turn.get("actions", [])
                    if isinstance(row, Mapping) and row.get("state") == "completed"
                    and row.get("action_work_id") and isinstance(row.get("result"), Mapping)
                    and not _is_context_read_action(row)
                }
                current_rows = [row for row in action_result.get("actions", [])
                    if isinstance(row, Mapping) and not _is_context_read_action(row)]
                repeated = [row for row in current_rows if isinstance(row, Mapping)
                    and row.get("state") == "completed"
                    and str(row.get("action_work_id")) in previous_results
                    and row.get("result") == previous_results[str(row.get("action_work_id"))]]
                if current_rows and repeated and len(repeated) == len(current_rows):
                    identities = sorted(str(row.get("action_work_id")) for row in repeated)
                    frontier = _episode_source_frontier(payload, history)
                    already_diagnosed = any(
                        isinstance(turn.get("no_progress_observation"), Mapping)
                        and turn["no_progress_observation"].get("action_work_ids") == identities
                        and _json(turn.get("source_frontier")) == _json(frontier)
                        for turn in history)
                    feedback = ("These registered actions already completed with the same source-bound results: "
                        + ", ".join(identities)
                        + ". Repeating them produced no new evidence. Choose a different currently available action, "
                        + "submit a conclusion supported by the existing evidence, or explain which new source evidence is needed.")
                    pause_reason = ("The same completed action and source evidence recurred after a no-progress diagnostic. "
                                    "This episode is paused until its evidence frontier changes or an operator resumes it.")
                    action_result = {**action_result,
                        "state": "paused_no_progress" if already_diagnosed else "retryable",
                        "request_new_decision": not already_diagnosed,
                        "reason": "repeated_action_without_new_evidence",
                        "response_validation_feedback": None if already_diagnosed else feedback,
                        "pause_reason": pause_reason if already_diagnosed else None,
                        "no_progress_observation": {"action_work_ids": identities,
                            "same_source_bound_results": True, "new_evidence_added": False,
                            "diagnostic_previously_delivered": already_diagnosed}}
            frontier = _episode_source_frontier(payload, history)
            page_diagnostic = _context_page_no_progress(action_result.get("actions", []), history, frontier)
            if page_diagnostic:
                nonread_rows = [row for row in action_result.get("actions", [])
                    if isinstance(row, Mapping) and not _is_context_read_action(row)]
                previous_results = {str(row.get("action_work_id")): row.get("result")
                    for turn in history for row in turn.get("actions", [])
                    if isinstance(row, Mapping) and row.get("state") == "completed"
                    and row.get("action_work_id") and not _is_context_read_action(row)}
                science_unchanged = all(row.get("state") == "completed"
                    and str(row.get("action_work_id")) in previous_results
                    and row.get("result") == previous_results[str(row.get("action_work_id"))]
                    for row in nonread_rows)
                if science_unchanged:
                    prior_observation = action_result.get("no_progress_observation")
                    observation = {**(dict(prior_observation) if isinstance(prior_observation, Mapping) else {}),
                        **page_diagnostic["no_progress_observation"]}
                    feedback = page_diagnostic.get("response_validation_feedback")
                    existing_feedback = action_result.get("response_validation_feedback")
                    if existing_feedback and feedback:
                        feedback = str(existing_feedback) + " " + str(feedback)
                    action_result = {**action_result, **page_diagnostic,
                        "no_progress_observation": observation,
                        "response_validation_feedback": feedback}
            if is_replay:
                action_result = {**action_result, "observed_request_usage": self._replay_token_budget(str(item["work_id"]))}
            return {"request_id": request_id, "decision": decision, "usage": usage,
                    "model": result.get("model"), "response_id": result.get("response_id"),
                    "action_result": action_result, "prepared_episode_payload": payload,
                    "source_frontier": _episode_source_frontier(payload, history),
                    "response_validation_feedback": action_result.get("response_validation_feedback")}
        except Exception as exc:
            elapsed_seconds = time.monotonic() - started_clock
            finished_at = time.time()
            error_record = {"error_type": type(exc).__name__, "message": str(exc)}
            for name in ("code", "request_id", "response_id", "http_status", "retryable", "ambiguous",
                         "incomplete_reason", "requested_output_tokens", "partial_output",
                         "transport_diagnostics", "return_code"):
                value = getattr(exc, name, None)
                if value is not None:
                    error_record[name] = value
            error_usage = getattr(exc, "usage", None)
            preserve_response = False
            with self.ledger.connect() as db:
                saved = db.execute("SELECT status,response_json FROM forest_study_model_calls WHERE request_id=?",
                                   (request_id,)).fetchone()
                preserve_response = bool(saved and saved["status"] == "completed" and saved["response_json"] is not None)
                if not preserve_response:
                    db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=COALESCE(?,usage_json),status='failed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                        (_json({"transport_error": error_record}), _json(error_usage) if error_usage is not None else None,
                         finished_at, elapsed_seconds, request_id))
            if preserve_response:
                self.ledger.event(self.campaign_id, "model_call_post_response_error",
                    {"request_id": request_id, "error": error_record}, str(item["work_id"]))
            raise

    def execute_decision_actions(self, episode: Mapping[str, Any], actions: Sequence[Mapping[str, Any]], *,
                                 gpu_devices=(), submission: Mapping[str, Any] | None = None,
                                 completed_action_ids: Sequence[str] = (), defer_gpu: bool = False,
                                 context_read_measure=None, context_response_capacity: int | None = None,
                                 context_response_capacity_sources: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        # Validate identity, uniqueness and source binding for the full batch
        # before a first operation can create effects or a child work item.
        _validate_action_bindings(episode["payload"], actions)
        results = []
        completed_ids = set(map(str, completed_action_ids))
        seen: set[tuple[str, str, str]] = set()
        for action in actions:
            match = next((binding for binding in episode["payload"].get("action_bindings", [])
                          if isinstance(binding, Mapping)
                          and binding.get("action_name", action.get("action", action.get("kind"))) == action.get("action", action.get("kind"))
                          and (action.get("action_work_id") is None or str(binding.get("work_id")) == str(action.get("action_work_id")))
                          and (action.get("configuration_id") is None or binding.get("configuration_id") == action.get("configuration_id"))
                          and (action.get("seed") is None or binding.get("seed") == action.get("seed"))), None)
            observational_read = bool(match and match.get("analysis_operation") == "read_context_segment")
            if match and str(match.get("work_id")) in completed_ids and not observational_read:
                continue
            key = (str(action.get("kind", "")), str(action.get("configuration_id", "")),
                   str(action.get("seed", "")), str(action.get("action_work_id", "")))
            if key in seen:
                raise ValueError("A single episode cannot submit the same operation/configuration/seed twice")
            seen.add(key)
            if observational_read:
                page_measure = None
                if callable(context_read_measure):
                    page_measure = lambda page, context_read_measure=context_read_measure, prefix=tuple(results), action=action, binding=match: context_read_measure(
                        page, prefix_results=prefix,
                        action_work_id=str(binding.get("work_id", "")) if isinstance(binding, Mapping) else "",
                        action=action)
                result = self.execute_decision_action(episode, action, gpu_devices=gpu_devices,
                    defer_gpu=defer_gpu, context_read_measure=page_measure,
                    context_response_capacity=context_response_capacity)
            elif _is_replay_episode(episode) or _is_replay_episode(episode.get("payload") or {}):
                from .mechanism_replay import execute_replay_action
                result = execute_replay_action(self, episode, action)
            else:
                result = self.execute_decision_action(episode, action, gpu_devices=gpu_devices, defer_gpu=defer_gpu)
            results.append(result)
            if result["state"] in {"retryable", "awaiting_reconciliation"}:
                # The model episode releases its worker while physical work is
                # queued/running. Later actions remain in the saved proposal and
                # are resumed in order after this operation settles.
                break
        # A read may share a legal action batch with operations whose observed
        # receipts also enter the next prompt. Refit the page against the full
        # completed batch before exposing or checkpointing any result.
        if context_response_capacity is not None and callable(context_read_measure):
            for index, row in enumerate(list(results)):
                if not _is_context_read_action(row):
                    continue
                matching_binding = next((binding for binding in episode["payload"].get("action_bindings", [])
                    if isinstance(binding, Mapping) and binding.get("work_id") == row.get("action_work_id")
                    and binding.get("analysis_operation") == "read_context_segment"), None)
                page = row.get("result") or {}
                if matching_binding is None or not isinstance(page, Mapping):
                    continue
                action = next((candidate for candidate in actions if isinstance(candidate, Mapping)
                    and str(candidate.get("action_work_id", "")) == str(row.get("action_work_id", ""))), {})
                prefix = tuple(candidate for other_index, candidate in enumerate(results) if other_index != index)
                measurement = lambda candidate, context_read_measure=context_read_measure, prefix=prefix, action=action, binding=matching_binding: context_read_measure(
                    candidate, prefix_results=prefix, action_work_id=str(binding.get("work_id", "")), action=action)
                memory_workspace = Path(str(matching_binding.get("memory_workspace", ""))).resolve()
                if memory_workspace.name == ".forest-context":
                    memory_workspace = memory_workspace.parent
                from .study_memory import ContextPageCapacityError, StudyMemory
                memory = StudyMemory(memory_workspace)
                requested_limit = int(action.get("context_limit") or page.get("requested_limit") or 6000)
                try:
                    fitted = memory.read_json(str(page["segment_id"]), pointer=str(page.get("pointer", "")),
                        offset=int(page.get("offset", 0)), limit=requested_limit,
                        response_capacity=context_response_capacity, measure_response=measurement)
                    row["result"] = {"operation": "read_context_segment", **fitted}
                except ContextPageCapacityError as exc:
                    offset = int(page.get("offset", 0))
                    row["result"] = {key: page[key] for key in (
                        "segment_id", "pointer", "total_characters", "complete") if key in page}
                    row["result"].update({"operation": "read_context_segment", "offset": offset,
                        "next_offset": offset, "requested_limit": requested_limit,
                        "returned_characters": 0, "content": None, "delivery_status": "not_delivered",
                        "delivery_reason": "The exact source page could not fit alongside the complete current evidence and request controls.",
                        "response_capacity": context_response_capacity,
                        "minimum_response_characters": getattr(exc, "minimum_response_characters", None)})
                    row["result"]["complete"] = offset >= int(row["result"].get("total_characters", offset))
        for row in results:
            if not _is_context_read_action(row):
                continue
            page = row.get("result") or {}
            if isinstance(page, Mapping):
                self.ledger.event(self.campaign_id, "context_page_read", {
                    "action_work_id": row.get("action_work_id"), "segment_id": page.get("segment_id"),
                    "pointer": page.get("pointer"), "offset": page.get("offset"),
                    "next_offset": page.get("next_offset"), "total_characters": page.get("total_characters"),
                    "requested_limit": page.get("requested_limit"), "returned_characters": page.get("returned_characters"),
                    "complete": page.get("complete"), "capacity_limited": page.get("capacity_limited"),
                    "response_capacity": page.get("response_capacity"),
                    "response_capacity_sources": list(context_response_capacity_sources), "result": page},
                    str(episode.get("work_id", "")))
        selected = (submission or {}).get("selected_configuration_id")
        selection_results = [r for r in results if r["kind"] == "analysis" and r.get("result", {}).get("operation") == "select"]
        validation = None
        if selection_results:
            actual = selection_results[-1]["result"].get("selected_configuration_id")
            validation = {"submitted_selected_configuration_id": selected,
                          "source_recomputed_selected_configuration_id": actual,
                          "selection_matches": selected == actual}
        states = {r["state"] for r in results}
        state = ("failed" if "failed" in states else "budget_exhausted" if "budget_exhausted" in states
                 else "awaiting_reconciliation" if "awaiting_reconciliation" in states
                 else "retryable" if "retryable" in states else "completed")
        response = {"state": state, "actions": results, "submission": dict(submission or {}),
                    "source_validation": validation}
        if len(results) == 1:
            response["result"] = results[0].get("result")
        return response

    def execute_decision_action(self, episode: Mapping[str, Any], decision: Mapping[str, Any], *, gpu_devices=(),
                                defer_gpu: bool = False, context_read_measure=None,
                                context_response_capacity: int | None = None) -> dict[str, Any]:
        """Run only a complete action binding explicitly provided by the manifest."""
        bindings = episode["payload"].get("action_bindings") or []
        if not isinstance(bindings, list):
            raise ValueError("action_bindings must be a list of complete source-bound operations")
        action = str(decision.get("action", decision.get("kind", "")))
        kind_for_action = {"train": "fit", "infer": "infer", "score": "rescore",
                           "select": "analysis", "compare": "analysis", "inspect": "analysis",
                           "read_context_segment": "analysis"}.get(action)
        if kind_for_action is None:
            raise ValueError(f"Unsupported scientific action kind: {action}")
        candidates = [entry for entry in bindings if isinstance(entry, Mapping)
                      and entry.get("kind") == kind_for_action
                      and entry.get("action_name", action) == action
                      and (decision.get("action_work_id") is None or entry.get("work_id") == decision.get("action_work_id"))
                      and (decision.get("configuration_id") is None or entry.get("configuration_id") == decision.get("configuration_id"))
                      and (decision.get("seed") is None or entry.get("seed") == decision.get("seed"))]
        if len(candidates) != 1:
            raise ValueError("The model decision must select exactly one predeclared action binding")
        binding = dict(candidates[0])
        work_id = str(binding.get("work_id", ""))
        if not work_id:
            raise ValueError("Each action binding needs its predeclared action work_id")
        if binding.get("analysis_operation") == "read_context_segment":
            from .study_memory import ContextPageCapacityError, StudyMemory
            requested_workspace = Path(str(binding.get("memory_workspace", ""))).resolve()
            campaign_root = self.campaign.resolve()
            try:
                requested_workspace.relative_to(campaign_root)
            except ValueError:
                raise ValueError("Context read binding points outside the campaign") from None
            store_workspace = requested_workspace.parent if requested_workspace.name == ".forest-context" else requested_workspace
            memory = StudyMemory(store_workspace)
            segment_id = decision.get("context_segment_id")
            if segment_id is None:
                catalog = memory.catalog()
                segment_id = catalog.get("current", {}).get("study-public:public-context")
            available = set(map(str, binding.get("available_segment_ids", [])))
            if not isinstance(segment_id, str) or segment_id not in available:
                raise ValueError("Context read must name an available arm-public segment ID")
            page_limit = 6000 if decision.get("context_limit") is None else decision.get("context_limit")
            measure = context_read_measure
            pointer = decision.get("context_pointer") or ""
            offset = 0 if decision.get("context_offset") is None else decision.get("context_offset")
            try:
                page = memory.read_json(segment_id, pointer=pointer, offset=offset,
                    limit=page_limit, response_capacity=context_response_capacity, measure_response=measure)
            except ContextPageCapacityError as exc:
                metadata = memory.read_json(segment_id, pointer=pointer, offset=offset, limit=1)
                page = {key: metadata[key] for key in ("segment_id", "pointer", "total_characters", "complete")
                        if key in metadata}
                page.update({"offset": offset, "next_offset": offset, "requested_limit": page_limit,
                    "returned_characters": 0, "content": None, "delivery_status": "not_delivered",
                    "delivery_reason": "The exact source page could not fit alongside the complete current evidence and request controls.",
                    "response_capacity": context_response_capacity,
                    "minimum_response_characters": getattr(exc, "minimum_response_characters", None)})
                page["complete"] = int(offset) >= int(page.get("total_characters", offset))
            return {"action_work_id": work_id, "kind": "analysis", "state": "completed",
                "result": {"operation": "read_context_segment", **page}}
        with self.ledger.connect() as db:
            prior = db.execute("SELECT payload_json,state,result_json FROM forest_study_work WHERE campaign_id=? AND work_id=?",
                (self.campaign_id, work_id)).fetchone()
        if prior:
            saved = json.loads(prior["payload_json"])
            def without_timeout(value):
                value = dict(value)
                value.pop("fit_timeout_seconds", None)
                if isinstance(value.get("inference_spec"), Mapping):
                    spec = dict(value["inference_spec"])
                    spec.pop("fit_timeout_seconds", None)
                    value["inference_spec"] = spec
                return value
            if without_timeout(saved) != without_timeout(binding):
                raise ValueError(f"Action work ID {work_id} was reused with different source-bound conditions")
            binding = saved
            if prior["state"] == "completed":
                return {"action_work_id": work_id, "kind": binding["kind"], "state": "completed",
                        "result": json.loads(prior["result_json"] or "{}")}
            if prior["state"] in {"failed", "cancelled", "budget_exhausted"}:
                return {"action_work_id": work_id, "kind": binding["kind"], "state": prior["state"],
                        "result": json.loads(prior["result_json"] or "{}")}
        else:
            budget = self.episode_budget_status(episode)
            if binding["kind"] in {"fit", "infer"}:
                if _is_replay_episode(episode):
                    return {"action_work_id": work_id, "kind": binding["kind"], "state": "budget_exhausted",
                            "reason": "Replay cells cannot launch scientific GPU work", "budget": budget}
                remaining_gpu = budget["remaining"]["gpu_seconds"]
                if remaining_gpu <= 0:
                    return {"action_work_id": work_id, "kind": binding["kind"], "state": "budget_exhausted",
                            "reason": "Episode GPU allowance is exhausted", "budget": budget}
                if binding["kind"] == "fit":
                    binding["fit_timeout_seconds"] = min(float(binding.get("fit_timeout_seconds", remaining_gpu)),
                        remaining_gpu)
                else:
                    spec = dict(binding.get("inference_spec") or {})
                    spec["fit_timeout_seconds"] = min(float(spec.get("fit_timeout_seconds", remaining_gpu)),
                        remaining_gpu)
                    binding["inference_spec"] = spec
        if binding.get("analysis_operation") == "compare" and binding.get("selection_work_id"):
            with self.ledger.connect() as db:
                selection = db.execute("SELECT state,result_json FROM forest_study_work WHERE campaign_id=? AND work_id=?",
                    (self.campaign_id, str(binding["selection_work_id"]))).fetchone()
            target = (binding.get("compared_configuration_ids") or [None, None])[-1]
            selected = json.loads(selection["result_json"] or "{}") if selection and selection["state"] == "completed" else {}
            if not selected:
                return {"action_work_id": work_id, "kind": binding["kind"], "state": "retryable",
                    "request_new_decision": True, "reason": "source_selection_must_complete_before_comparison",
                    "selection_work_id": binding["selection_work_id"]}
            if str(selected.get("selected_configuration_id")) != str(target):
                return {"action_work_id": work_id, "kind": binding["kind"], "state": "retryable",
                    "request_new_decision": True, "reason": "comparison_must_use_source_selected_configuration",
                    "selection_work_id": binding["selection_work_id"],
                    "source_selected_configuration_id": selected.get("selected_configuration_id"),
                    "proposed_comparison_configuration_id": target}
        self.ledger.register_action(self.campaign_id, work_id, binding, parent_id=str(episode["work_id"]))
        if defer_gpu and binding["kind"] in {"fit", "infer"}:
            return {"action_work_id": work_id, "kind": binding["kind"], "state": "retryable",
                "result": {"deferred_to_gpu_queue": True, "work_id": work_id}}
        self.ledger.set_state(self.campaign_id, work_id, "running")
        try:
            if binding["kind"] == "fit":
                result = self.execute_fit({"work_id": work_id, "payload": binding}, gpu_devices=gpu_devices)
                state = result["supervisor"].get("status")
                terminal = "completed" if state == "completed" else "awaiting_reconciliation" if state == "reconciliation" else "failed" if state in ("failed", "cancelled", "budget_exhausted") else "retryable"
                self.ledger.set_state(self.campaign_id, work_id, terminal, result=result,
                                      supervisor_work_id=result.get("supervisor_work_id", work_id),
                                      error=result["supervisor"].get("error") if terminal == "failed" else None)
                return {"action_work_id": work_id, "kind": "fit", "state": terminal, "result": result}
            if binding["kind"] == "rescore":
                result = rescore_binding({**binding, "campaign_path": str(self.campaign)})
                self.ledger.set_state(self.campaign_id, work_id, "completed", result=result)
                return {"action_work_id": work_id, "kind": "rescore", "state": "completed", "result": result}
            if binding["kind"] == "infer":
                result = self.execute_inference(binding, gpu_devices=gpu_devices)
                supervisor_state = result["supervisor"].get("status")
                state = "completed" if supervisor_state == "completed" else "awaiting_reconciliation" if supervisor_state == "reconciliation" else "failed" if supervisor_state in ("failed", "cancelled", "budget_exhausted") else "retryable"
                self.ledger.set_state(self.campaign_id, work_id, state, result=result,
                                      supervisor_work_id=work_id,
                                      error=result["supervisor"].get("error") if state == "failed" else None)
                return {"action_work_id": work_id, "kind": "infer", "state": state, "result": result}
            if binding["kind"] == "analysis":
                result = execute_registered_analysis(binding)
                if binding.get("analysis_operation") == "compare":
                    result = {**result,
                        "compared_configuration_ids": list(binding.get("compared_configuration_ids") or []),
                        "compared_seeds": list(binding.get("compared_seeds") or [])}
                self.ledger.set_state(self.campaign_id, work_id, "completed", result=result)
                return {"action_work_id": work_id, "kind": "analysis", "state": "completed", "result": result}
            raise ValueError("No deterministic executor exists for selected action")
        except Exception as exc:
            self.ledger.set_state(self.campaign_id, work_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise

    def execute_calibration_decision(self, item: Mapping[str, Any], *, client: Any) -> dict[str, Any]:
        """Ask the saved model to select a legal update count from real receipts."""
        payload = dict(item["payload"])
        allowed_by_family = payload.get("allowed_update_counts")
        current_counts = payload.get("current_update_counts") or {}
        required_roles = payload.get("required_roles_by_family") or {
            "darcy": ["anchor", "slowest_candidate", "C3_revision", "C4_revision"],
            "bloodmnist": ["anchor", "slowest_candidate", "C3_revision", "C4_revision"],
            "etth1": ["anchor", "slowest_candidate", "C3_revision", "C4_revision"],
        }
        primary = payload.get("primary_metric_by_family") or {
            "darcy": {"metric": "relative_l2", "direction": "minimize"},
            "bloodmnist": {"metric": "macro_f1", "direction": "maximize"},
            "etth1": {"metric": "mae", "direction": "minimize"},
        }
        if not isinstance(allowed_by_family, Mapping) or not isinstance(required_roles, Mapping):
            raise ValueError("Calibration decision must declare allowed counts and required role coverage")
        from .gpu_supervisor import ComputeSupervisor
        supervisor = ComputeSupervisor(self.ledger_path, self.ledger_id)
        fit_items = [x for x in _manifest_work_items(self.manifest)
                     if x.get("kind") == "fit" and x.get("phase") == "calibration"]
        actual_rows = []
        for fit_item in fit_items:
            config_path = Path(str(fit_item.get("configuration_path", "")))
            output_path = Path(str(fit_item.get("output_directory", "")))
            attempt = _fit_attempt_record(self.ledger_path, self.campaign_id, str(fit_item["work_id"]))
            physical_id = str(attempt["supervisor_work_id"]) if attempt else str(fit_item["work_id"])
            if attempt:
                output_path = Path(str(attempt["output_directory"]))
            if not config_path.is_file():
                actual_rows.append({"work_id": fit_item["work_id"], "state": "config_missing"})
                continue
            config = _read_json(config_path)
            status = supervisor.status(physical_id) if _supervisor_has_work(self.ledger_path, physical_id) else {"status": "not_run"}
            receipt_path = output_path / "execution_receipt.json"
            if not receipt_path.is_file():
                actual_rows.append({"work_id": fit_item["work_id"], "family": config.get("family"),
                    "role": config.get("calibration_role"), "updates": config.get("optimizer_updates"),
                    "state": status.get("status", "receipt_missing"), "gpu_seconds": _measured_gpu_seconds(self.ledger_path, str(fit_item["work_id"]), self.campaign_id, self.ledger_id)})
                continue
            receipt = _read_json(receipt_path)
            if (status.get("status") != "completed" or receipt.get("status") != "SUCCEEDED" or receipt.get("effective_config") != config
                    or receipt.get("hidden_test_opened") is not False
                    or receipt.get("formal_score_eligible") is not False):
                actual_rows.append({"work_id": fit_item["work_id"], "family": config.get("family"),
                    "role": config.get("calibration_role"), "updates": config.get("optimizer_updates"),
                    "state": "invalid_receipt", "gpu_seconds": _measured_gpu_seconds(self.ledger_path, str(fit_item["work_id"]), self.campaign_id, self.ledger_id)})
                continue
            gpu_seconds = _measured_gpu_seconds(self.ledger_path, str(fit_item["work_id"]), self.campaign_id, self.ledger_id)
            pred_path = output_path / str(receipt.get("validation_prediction_file", ""))
            # Independently recompute calibration metrics from exact researcher
            # validation targets and the saved predictions before exposing them.
            try:
                derived_path = self.campaign / "development" / "calibration_verification" / f"{fit_item['work_id']}.json"
                checked = rescore_binding({"campaign_path": str(self.campaign), "config_path": str(config_path),
                    "prediction_path": str(pred_path), "reference_receipt_path": str(receipt_path),
                    "data_root": str(self.campaign / "data/researcher"), "output_path": str(derived_path),
                    "work_id": _action_work_id(str(fit_item["work_id"]), "calibration-rescore"),
                    "configuration_id": config.get("configuration_id"), "seed": config.get("seed")})
                actual_rows.append({"work_id": fit_item["work_id"], "family": config["family"],
                    "role": config["calibration_role"], "updates": int(config["optimizer_updates"]),
                    "seed": int(config["seed"]), "state": "completed", "gpu_seconds": gpu_seconds,
                    "metrics": checked["metrics"], "configuration_id": config["configuration_id"]})
            except Exception as exc:
                actual_rows.append({"work_id": fit_item["work_id"], "family": config.get("family"),
                    "role": config.get("calibration_role"), "updates": config.get("optimizer_updates"),
                    "state": "verification_failed", "error": f"{type(exc).__name__}: {exc}", "gpu_seconds": gpu_seconds})
        candidates = calibration_update_candidates(actual_rows, allowed_by_family, required_roles, primary)
        messages = _calibration_decision_messages(payload, actual_rows, candidates, required_roles, current_counts)
        family_update_properties = {str(family): {"type": ["integer", "null"],
            "enum": sorted({int(value) for value in counts}) + [None]}
            for family, counts in allowed_by_family.items()}
        schema = {"type": "object", "required": ["family_updates", "readiness", "rationale"],
            "properties": {"family_updates": {"type": "object", "required": sorted(family_update_properties),
                "properties": family_update_properties},
                "readiness": {"type": "string", "enum": ["continue", "stop", "incomplete"]},
                "rationale": {"type": "string"}}}
        delivered = _assemble_model_messages(client, messages, "experiment")
        original_delivered = copy.deepcopy(delivered)
        delivered, context_encoding = _intern_delivered_messages(delivered)
        request_id = str(uuid.uuid4())
        response_client = copy.copy(client)
        config = getattr(client, "config", None)
        codex_transport = ((getattr(client, "provider", {}) or {}).get("kind") == "codex_cli"
                           or getattr(client, "api", None) == "codex_cli")
        transport_schema = _codex_strict_schema(schema) if codex_transport else copy.deepcopy(schema)
        if isinstance(config, Mapping):
            response_client.config = copy.deepcopy(dict(config))
            response_client.config["_forest_action_schema"] = copy.deepcopy(transport_schema)
        trace_reference = _transport_trace_reference(self.campaign, request_id, response_client.provider)
        if trace_reference:
            if not isinstance(getattr(response_client, "config", None), Mapping):
                response_client.config = {}
            response_client.config["_forest_transport_trace_path"] = trace_reference
        from research.agents.output_budget import output_allocation
        explicit_output = payload.get("max_output_tokens")
        allocation = output_allocation(response_client.config, delivered, stage="experiment",
                                       requested=explicit_output)
        max_output_tokens = int(explicit_output) if explicit_output is not None else int(allocation["tokens"])
        build_request = getattr(response_client, "build_request", None)
        if callable(build_request):
            endpoint, transport_payload = build_request(delivered, json_mode=True,
                max_output_tokens=max_output_tokens)
        else:
            # Scripted infrastructure fixtures need not emulate transport
            # serialization. Real providers expose this before the side effect.
            endpoint, transport_payload = None, None
        request_record = {"request_id": request_id, "work_id": item["work_id"],
            "provider_id": client.provider.get("id"), "model": client.provider.get("model"),
            "reasoning_effort": response_client.config.get("reasoning_effort"),
            "provider_snapshot": _audit_config(client.provider),
            "effective_provider_config": _audit_config(response_client.config),
            "effective_timeout_seconds": getattr(response_client, "timeout", None),
            "stage": "experiment", "messages": delivered, "original_messages": original_delivered,
            "context_encoding": context_encoding, "decision_schema": schema,
            "transport_decision_schema": transport_schema,
            "source_output_allocation": allocation, "max_output_tokens": max_output_tokens,
            "endpoint": endpoint, "request_payload": transport_payload,
            "transport_payload_available": callable(build_request),
            "transport_trace_path": trace_reference}
        with self.ledger.connect() as db:
            started_at = time.time()
            started_clock = time.monotonic()
            db.execute("INSERT INTO forest_study_model_calls(request_id,campaign_id,work_id,request_json,response_json,usage_json,status,recorded_at,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (request_id, self.campaign_id, str(item["work_id"]), _json(request_record), None, None, "requested", started_at, started_at))
        try:
            response = response_client.complete(delivered, json_mode=True,
                max_output_tokens=max_output_tokens, output_stage="experiment")
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
            elapsed_seconds = time.monotonic() - started_clock
            with self.ledger.connect() as db:
                row = db.execute("SELECT status,response_json FROM forest_study_model_calls WHERE request_id=?",
                                 (request_id,)).fetchone()
                if not (row and row["status"] == "completed" and row["response_json"] is not None):
                    db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=COALESCE(?,usage_json),status='failed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                        (_json({"transport_error": error_record}), _json(error_usage) if error_usage is not None else None,
                         finished_at, elapsed_seconds, request_id))
            self.ledger.event(self.campaign_id, "calibration_model_failure",
                {"request_id": request_id, "error": error_record}, str(item["work_id"]))
            raise
        elapsed_seconds = time.monotonic() - started_clock
        finished_at = time.time()
        text = response.get("text")
        usage = response.get("usage") or {"input_tokens": None, "output_tokens": None,
            "cost": None, "cost_source": "unknown"}
        with self.ledger.connect() as db:
            db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                (_json({"text": text, "model": response.get("model"),
                        "response_id": response.get("response_id")}), _json(usage), finished_at,
                 elapsed_seconds, request_id))
        if not isinstance(text, str):
            raise ValueError("Calibration model response has no JSON text")
        choice = _restore_strict_optional_nulls(json.loads(text), schema)
        _validate_episode_schema(choice, schema)
        valid, verification = verify_calibration_choice(choice, candidates, allowed_by_family, required_roles)
        outcome = {"request_id": request_id, "model_decision": choice, "usage": usage,
            "observed_rows": actual_rows, "candidates": candidates,
            "source_validation": {"valid": valid, **verification},
            "resolved_updates_path": payload.get("resolved_updates_path") if valid else None}
        with self.ledger.connect() as db:
            db.execute("UPDATE forest_study_model_calls SET response_json=?,usage_json=?,status='completed',finished_at=?,elapsed_seconds=? WHERE request_id=?",
                (_json({"text": text, "model": response.get("model"), "response_id": response.get("response_id")}), _json(usage), finished_at, elapsed_seconds, request_id))
            db.execute("UPDATE forest_study_work SET state=?,result_json=?,decision_json=?,updated_at=? WHERE campaign_id=? AND work_id=?",
                ("completed" if valid else "failed", _json(outcome), _json(choice), finished_at, self.campaign_id, str(item["work_id"])))
        if valid:
            resolved_path = Path(str(payload["resolved_updates_path"]))
            resolved = {"campaign_id": self.campaign_id, "source_work_id": item["work_id"],
                "request_id": request_id, "model": response.get("model"), "usage": usage,
                "family_update_counts": choice["family_updates"], "readiness": choice["readiness"],
                "model_rationale": choice["rationale"], "observed_evidence": candidates,
                "source_validation": {"valid": valid, **verification}, "formal_score_eligible": False}
            _atomic_json_file(resolved_path, resolved)
        return outcome

    def execute_snapshot_stage(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Build the 72 complete snapshot configs from validated calibration choices."""
        payload = dict(item["payload"])
        plan_path = Path(payload["snapshot_plan_path"])
        if plan_path.is_file():
            plan = _read_json(plan_path)
            configs = plan.get("configs")
            if not isinstance(configs, list) or not configs:
                raise ValueError("Existing snapshot plan has no registered fit configs")
            from .gpu_supervisor import ComputeSupervisor
            supervisor = ComputeSupervisor(self.ledger_path, self.ledger_id)
            pending = []
            unresolved = []
            valid_work_ids = []
            hard_failures = []
            for config in configs:
                work_id = str(config["work_id"])
                family = str(config.get("family", "unknown"))
                attempt = _fit_attempt_record(self.ledger_path, self.campaign_id, work_id)
                physical_id = str(attempt["supervisor_work_id"]) if attempt else work_id
                output = Path(str(attempt["output_directory"])) if attempt else Path(str(config["output_directory"]))
                if not _supervisor_has_work(self.ledger_path, physical_id):
                    pending.append(work_id)
                    continue
                status = supervisor.status(physical_id)
                receipt_path = output / "execution_receipt.json"
                if status.get("status") in {"failed", "cancelled", "budget_exhausted"}:
                    hard_failures.append({"work_id": work_id, "family": family,
                        "supervisor_status": status.get("status"), "error": status.get("error")})
                    continue
                if status.get("status") == "reconciliation":
                    unresolved.append({"work_id": work_id, "family": family,
                        "error": status.get("error")})
                    continue
                if status.get("status") != "completed":
                    pending.append(work_id)
                    continue
                if not receipt_path.is_file():
                    hard_failures.append({"work_id": work_id, "family": family,
                        "supervisor_status": "completed", "error": "successful supervisor completion has no fit receipt"})
                    continue
                receipt = _read_json(receipt_path)
                if (receipt.get("status") != "SUCCEEDED" or receipt.get("effective_config") != _read_json(Path(config["configuration_path"]))
                        or receipt.get("hidden_test_opened") is not False or receipt.get("formal_score_eligible") is not True):
                    hard_failures.append({"work_id": work_id, "family": family,
                        "supervisor_status": "completed", "error": "snapshot fit receipt failed source validation"})
                else:
                    valid_work_ids.append(work_id)
            by_family = {}
            for failure in hard_failures:
                by_family.setdefault(failure["family"], []).append(failure)
            stage_state = ("failed" if hard_failures else "awaiting_reconciliation" if unresolved
                           else "retryable" if pending else "completed")
            return {"phase": "shared_snapshot", "fit_count": len(configs),
                "plan_path": str(plan_path), "work_ids": [str(x["work_id"]) for x in configs],
                "pending_work_ids": pending, "unresolved_work": unresolved,
                "valid_work_ids": valid_work_ids, "hard_failures": hard_failures,
                "hard_failures_by_family": by_family, "stage_state": stage_state}
        from .training_runtime import build_snapshot_fit_configs
        resolved = _read_json(Path(payload["resolved_updates_path"]))
        if resolved.get("formal_score_eligible") is not False:
            raise ValueError("Calibration update selection must remain ineligible for formal scoring")
        if not resolved.get("source_validation", {}).get("valid"):
            raise ValueError("Snapshot generation requires a source-validated calibration decision")
        data_ids = _read_json(Path(payload["data_selection_reference"]))
        dataset_versions = _read_json(Path(payload["dataset_versions_reference"]))
        raw_updates = resolved.get("family_update_counts")
        if not isinstance(raw_updates, Mapping):
            raise ValueError("Resolved calibration artifact has no family update counts")
        configs = build_snapshot_fit_configs(source_spec_dir=Path(payload["source_spec_dir"]),
            dataset_version_ids=dataset_versions, data_ids_by_family=data_ids,
            update_counts_by_family={str(k): int(v) for k, v in raw_updates.items()},
            seeds=tuple(map(int, payload.get("training_seeds", (1001, 1002)))) )
        config_dir = plan_path.parent / "snapshot_configs"
        plan_configs = []
        now_items = []
        for config in configs:
            work_id = str(uuid.uuid4())
            family, task_id, candidate_id = config["family"], config["task_id"], config["configuration_id"]
            output = Path(payload["campaign_directory"]) / "runs" / "shared_snapshot" / task_id / candidate_id / f"seed-{config['seed']}" / work_id
            config["work_id"] = work_id
            config["output_reference"] = str(output)
            config["source_revision_reference"] = str(payload.get("source_revision_reference", "registered source_spec + current training_runtime"))
            config_path = config_dir / f"{work_id}.json"
            _atomic_json_file(config_path, config)
            queue = {**config, "configuration_path": str(config_path), "output_directory": str(output),
                     "fit_timeout_seconds": float(payload.get("timeout_seconds", 90))}
            plan_configs.append(queue)
            now_items.append({"work_id": work_id, "kind": "fit", "phase": "formal", "stage": "shared_snapshot",
                "derived_from": [str(item["work_id"])],
                "payload": {"configuration_path": str(config_path), "output_directory": str(output),
                    "timeout_seconds": float(payload.get("timeout_seconds", 90)),
                    "fit_timeout_seconds": float(payload.get("timeout_seconds", 90)),
                    "phase": "formal", "effective_config": config,
                    "source_files": [str(Path(__file__).resolve()), str(Path(payload["source_spec_dir"]) / "models.json"),
                                     str(config_path)],
                    "source_root": str(payload["commands"]["source_root"]),
                    "fitter_python": str(payload["commands"]["fitter_python"])}})
        _atomic_json_file(plan_path, {"phase": "shared_snapshot", "fit_count": len(plan_configs),
            "formal_score_eligible": True, "resolved_updates_reference": str(payload["resolved_updates_path"]),
            "configs": plan_configs})
        self._append_manifest_work(plan_configs, now_items)
        return {"phase": "shared_snapshot", "fit_count": len(plan_configs),
                "plan_path": str(plan_path), "configuration_directory": str(config_dir),
                "work_ids": [item["work_id"] for item in now_items],
                "pending_work_ids": [item["work_id"] for item in now_items], "stage_state": "retryable"}

    def _append_manifest_work(self, task_queue: Sequence[Mapping[str, Any]], work_items: Sequence[Mapping[str, Any]]):
        """Atomically add generated configs without replacing concurrent manifest edits."""
        import fcntl
        lock = self.manifest_path.with_suffix(self.manifest_path.suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            latest = _read_json(self.manifest_path)
            if latest.get("manifest_id") != self.manifest.get("manifest_id"):
                raise ValueError("Manifest identity changed before snapshot work registration")
            state = latest.setdefault("execution_state", {})
            queue = state.setdefault("task_queue", [])
            registered = state.setdefault("work_items", [])
            known = {str(entry.get("work_id")) for entry in registered if isinstance(entry, Mapping)}
            for item in task_queue:
                if not any(str(value.get("work_id")) == str(item["work_id"]) for value in queue):
                    queue.append(dict(item))
            for item in work_items:
                if str(item["work_id"]) not in known:
                    registered.append(dict(item))
            latest["manifest_version"] = int(latest.get("manifest_version", 0)) + 1
            _atomic_json_file(self.manifest_path, latest)
            fcntl.flock(stream, fcntl.LOCK_UN)
        self.manifest = _read_json(self.manifest_path)

    def execute_inference(self, binding: Mapping[str, Any], *, gpu_devices: Sequence[str] = ()) -> dict[str, Any]:
        from .gpu_supervisor import ComputeSupervisor
        spec = dict(binding.get("inference_spec") or {})
        required = ("config_path", "checkpoint_path", "output_directory", "fit_timeout_seconds")
        if any(not spec.get(key) for key in required):
            raise ValueError("Inference requires explicit config, checkpoint, output, and timeout references")
        spec_path = self.campaign / "development" / "execution_plans" / f"{binding['work_id']}-inference.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        python = str(self.manifest.get("commands", {}).get("fitter_python") or sys.executable)
        source_root = Path(str(self.manifest.get("commands", {}).get("source_root") or Path(__file__).resolve().parents[2])).resolve()
        command = [python, "-m", "research.experiments.study_runtime", "worker-infer", "--spec", str(spec_path)]
        output = Path(str(spec["output_directory"]))
        output.mkdir(parents=True, exist_ok=True)
        supervisor = ComputeSupervisor(self.ledger_path, self.ledger_id)
        if _supervisor_has_work(self.ledger_path, str(binding["work_id"])):
            if not spec_path.is_file():
                raise ValueError("Accepted inference work has no installed source specification to reconcile")
            installed = _read_json(spec_path)
            if (installed.get("campaign") != str(self.campaign)
                    or installed.get("work_id") != str(binding["work_id"])):
                raise ValueError("Accepted inference specification belongs to a different campaign or work ID")
            installed_spec = {key: value for key, value in installed.items()
                              if key not in {"campaign", "work_id"}}
            if any(installed_spec.get(key) != value for key, value in spec.items()
                   if key != "gpu_devices"):
                raise ValueError("Accepted inference specification differs from its registered action")
            status = supervisor.status(str(binding["work_id"]))
            accepted_config = status.get("configuration") or {}
            accepted_requested = accepted_config.get("requested_gpu_devices")
            if accepted_requested is None:
                # Older supervisor records stored only the already-resolved UUID list.
                accepted_requested = accepted_config.get("gpu_devices")
            if list(accepted_requested or []) != list(installed_spec.get("gpu_devices") or []):
                raise ValueError("Accepted inference GPU device differs from the installed inference specification")
            spec = installed_spec
        else:
            if gpu_devices:
                spec["gpu_devices"] = list(gpu_devices)
            if not spec.get("gpu_devices"):
                raise ValueError("Inference requires the scheduler's explicit physical GPU device selection")
            _atomic_json_file(spec_path, {**spec, "campaign": str(self.campaign), "work_id": binding["work_id"]})
            status = supervisor.submit(command, output, work_id=str(binding["work_id"]),
                configuration={"phase": str(spec.get("phase", "formal")), "source_files": [
                    str((source_root / "research/experiments/training_runtime.py").resolve()), str(Path(spec["config_path"]).resolve())]},
                timeout_seconds=float(spec["fit_timeout_seconds"]), gpu_devices=list(spec.get("gpu_devices", [])),
                cwd=".", env={"PYTHONPATH": str(source_root)}, phase=str(spec.get("phase", "formal")))
        if status.get("status") not in ("completed", "failed", "cancelled", "budget_exhausted", "reconciliation"):
            status = supervisor.wait(str(binding["work_id"]), timeout=1.0)
        result = {"supervisor": status, "spec_path": str(spec_path), "output_directory": str(spec["output_directory"])}
        if status.get("status") == "completed":
            receipt_path = Path(spec["output_directory"]) / "inference_receipt.json"
            receipt = _read_json(receipt_path)
            if (receipt.get("status") != "SUCCEEDED" or receipt.get("work_id") != binding["work_id"]
                    or receipt.get("optimizer_updates") != 0 or receipt.get("hidden_test_opened") is not False):
                raise ValueError("Inference process did not produce a valid no-update researcher-only receipt")
            result["receipt"] = receipt
        return result

    def claim_work(self, owner: str, *, work_id: str | None = None,
                   work_ids: Sequence[str] | None = None,
                   kinds: Sequence[str] | None = None) -> dict[str, Any] | None:
        """Atomically claim one eligible work item, optionally by scheduler assignment."""
        self.reload_manifest()
        return self.ledger.claim(self.campaign_id, owner,
            work_ids=[work_id] if work_id is not None else work_ids, kinds=kinds)

    def run_one(self, *, owner: str, gpu_devices: Sequence[str] = (), client: Any = None,
                work_id: str | None = None, kinds: Sequence[str] | None = None,
                defer_gpu: bool = False) -> dict[str, Any] | None:
        item = self.claim_work(owner, work_id=work_id, kinds=kinds)
        if item is None:
            return None
        return self.run_claimed(item, owner=owner, gpu_devices=gpu_devices, client=client, defer_gpu=defer_gpu)

    def run_claimed(self, item: Mapping[str, Any], *, owner: str | None = None,
                    gpu_devices: Sequence[str] = (), client: Any = None,
                    defer_gpu: bool = False) -> dict[str, Any]:
        """Execute an item already leased by claim_work without another claim."""
        owner = str(owner or item.get("lease_owner") or "")
        if item.get("state") != "claimed" or item.get("lease_owner") != owner:
            raise ValueError("run_claimed requires a work item leased to this owner")
        self.reload_manifest()
        self.ledger.heartbeat(self.campaign_id, str(item["work_id"]), owner)
        self.ledger.set_state(self.campaign_id, item["work_id"], "running")
        try:
            if item["kind"] == "fit":
                outcome = self.execute_fit(item, gpu_devices=gpu_devices)
                status = outcome["supervisor"].get("status")
                if status == "completed":
                    self.ledger.set_state(self.campaign_id, item["work_id"], "completed", result=outcome,
                                          supervisor_work_id=str(outcome.get("supervisor_work_id", item["work_id"])))
                elif status == "reconciliation":
                    self.ledger.set_state(self.campaign_id, item["work_id"], "awaiting_reconciliation", result=outcome,
                                          supervisor_work_id=str(outcome.get("supervisor_work_id", item["work_id"])), error=outcome["supervisor"].get("error"))
                elif status in ("failed", "cancelled", "budget_exhausted"):
                    self.ledger.set_state(self.campaign_id, item["work_id"], "failed", result=outcome,
                                          supervisor_work_id=str(outcome.get("supervisor_work_id", item["work_id"])), error=outcome["supervisor"].get("error"))
                else:
                    self.ledger.set_state(self.campaign_id, item["work_id"], "retryable", result=outcome,
                                          supervisor_work_id=str(outcome.get("supervisor_work_id", item["work_id"])))
                return {"work_id": item["work_id"], "kind": "fit", **outcome}
            if item["kind"] == "rescore":
                outcome = rescore_binding({**item["payload"], "campaign_path": str(self.campaign)})
                self.ledger.set_state(self.campaign_id, item["work_id"], "completed", result=outcome)
                return {"work_id": item["work_id"], "kind": "rescore", "result": outcome}
            if item["kind"] == "analysis":
                outcome = execute_registered_analysis(item["payload"])
                self.ledger.set_state(self.campaign_id, item["work_id"], "completed", result=outcome)
                return {"work_id": item["work_id"], "kind": "analysis", "result": outcome}
            if item["kind"] == "infer":
                outcome = self.execute_inference(item["payload"], gpu_devices=gpu_devices)
                state = outcome["supervisor"].get("status")
                final = "completed" if state == "completed" else "awaiting_reconciliation" if state == "reconciliation" else "failed" if state in ("failed", "cancelled", "budget_exhausted") else "retryable"
                self.ledger.set_state(self.campaign_id, item["work_id"], final, result=outcome,
                                      supervisor_work_id=str(item["work_id"]))
                return {"work_id": item["work_id"], "kind": "infer", "result": outcome}
            if item["kind"] == "calibration_decision":
                if client is None:
                    spec = self._model_provider_spec(item)
                    client = load_saved_codex_client(str(spec["id"]), model=str(spec.get("model", "gpt-6-luna")),
                        reasoning_effort=str((spec.get("config") or {}).get("reasoning_effort", "medium")))
                outcome = self.execute_calibration_decision(item, client=client)
                state = "completed" if outcome["source_validation"]["valid"] else "failed"
                self.ledger.set_state(self.campaign_id, item["work_id"], state, result=outcome,
                                      decision=outcome["model_decision"],
                                      error=None if state == "completed" else "Model calibration choice did not match the declared observed-data rule")
                return {"work_id": item["work_id"], "kind": "calibration_decision", **outcome}
            if item["kind"] == "snapshot_stage":
                outcome = self.execute_snapshot_stage(item)
                self.ledger.set_state(self.campaign_id, item["work_id"], outcome["stage_state"], result=outcome)
                self.reload_manifest()
                return {"work_id": item["work_id"], "kind": "snapshot_stage", **outcome}
            if item["kind"] in {"episode", "replay"}:
                if client is None:
                    spec = self._model_provider_spec(item)
                    if not spec.get("id"):
                        raise ValueError("Manifest must identify the saved local model provider for decision work")
                    client = load_saved_codex_client(str(spec["id"]), model=str(spec.get("model", "gpt-6-luna")),
                        reasoning_effort=str((spec.get("config") or {}).get("reasoning_effort", "medium")))
                raw_episode_payload = item.get("payload") or {}
                nested_episode = raw_episode_payload.get("episode") if isinstance(raw_episode_payload, Mapping) else None
                payload_arm = str((raw_episode_payload.get("arm") or raw_episode_payload.get("design_arm") or
                                   ((nested_episode or {}).get("arm") if isinstance(nested_episode, Mapping) else ""))).upper()
                if payload_arm == "AIDE":
                    from .aide_runtime import run_aide_episode
                    if _is_replay_episode(item) or _is_replay_episode(raw_episode_payload):
                        client = self._budgeted_episode_client(item, client)
                        client = self._replay_request_client(item, client)
                    self._active_episode_started[str(item["work_id"])] = time.monotonic()
                    try:
                        outcome = run_aide_episode(self, item, client=client,
                            gpu_devices=gpu_devices, defer_gpu=defer_gpu)
                    finally:
                        self._active_episode_started.pop(str(item["work_id"]), None)
                    state = str(outcome.get("state", "failed"))
                    if state not in {"completed", "failed", "retryable", "awaiting_reconciliation", "budget_exhausted", "paused_no_progress"}:
                        raise ValueError(f"Native AIDE returned unknown episode state: {state}")
                    self.ledger.set_state(self.campaign_id, item["work_id"], state, result=outcome,
                        error=outcome.get("reason") if state in {"failed", "budget_exhausted"} else None)
                    return {"work_id": item["work_id"], "kind": item["kind"], **outcome}
                prior_decision = json.loads(item["decision_json"]) if item.get("decision_json") else None
                prior_result = json.loads(item["result_json"]) if item.get("result_json") else {}
                history = list(prior_result.get("action_history", []))
                previous_action_result = prior_result.get("action_result") or {}
                pending_retry = bool(prior_decision and (previous_action_result.get("state") in
                                     {"retryable", "awaiting_reconciliation"} or
                                     (prior_result.get("decision_received") and "action_result" not in prior_result))
                                     and not previous_action_result.get("request_new_decision"))
                if pending_retry:
                    episode_payload = dict(item["payload"])
                    prepared = prior_result.get("prepared_episode_payload")
                    if isinstance(prepared, Mapping):
                        episode_payload = dict(prepared)
                    elif not episode_payload.get("action_bindings"):
                        episode_payload = self.build_episode_request({**episode_payload, "work_id": str(item["work_id"])})
                    episode_item = {**item, "payload": episode_payload}
                    saved_actions = prior_decision.get("actions") or [prior_decision]
                    page_action_id = next((str(action.get("action_work_id")) for action in saved_actions
                        if isinstance(action, Mapping) and action.get("kind") == "read_context_segment"), "")
                    page_measure = (self._public_page_measure_callback(client, episode_payload, history,
                        page_action_id) if client is not None else lambda page, **_: len(_json(page)))
                    completed_ids = [str(action.get("action_work_id")) for record in history
                                     for action in record.get("actions", [])
                                     if action.get("state") == "completed" and action.get("action_work_id")]
                    action_result = self.execute_decision_actions(episode_item, saved_actions,
                        gpu_devices=gpu_devices, submission=prior_decision.get("submission"),
                        completed_action_ids=completed_ids, defer_gpu=defer_gpu,
                        context_read_measure=page_measure,
                        context_response_capacity=self._context_response_capacity(client) if client is not None else None,
                        context_response_capacity_sources=copy.deepcopy(getattr(self, "_context_capacity_sources", [])))
                    outcome = {"decision": prior_decision, "action_result": action_result,
                               "request_id": None, "usage": None, "resumed_saved_decision": True}
                else:
                    outcome = self.execute_model_episode(item, client=client,
                        gpu_devices=gpu_devices, history=history, defer_gpu=defer_gpu)
                action_result = outcome["action_result"]
                decision = outcome["decision"]
                if decision.get("mode") == "submit":
                    if action_result.get("state") == "paused_no_progress":
                        final_state = "paused_no_progress"
                    elif action_result.get("state") == "budget_exhausted":
                        final_state = "budget_exhausted"
                    elif payload_arm == "FULL_NO_GATE":
                        final_state = "completed"
                        action_result = {**action_result, "gate_advisory": True,
                            "advisory_valid": action_result.get("valid") is True}
                    elif action_result.get("valid") is True:
                        final_state = "completed"
                    else:
                        final_state = "retryable"
                elif action_result.get("state") in {"retryable", "awaiting_reconciliation", "failed", "budget_exhausted", "paused_no_progress"}:
                    final_state = action_result["state"]
                else:
                    final_state = "retryable"  # Return for an evidence-aware follow-up model turn.
                old_history = list(history)
                if action_result.get("actions"):
                    if pending_retry and old_history:
                        merged = {str(action.get("action_work_id")): dict(action)
                                  for action in old_history[-1].get("actions", [])}
                        merged.update({str(action.get("action_work_id")): dict(action)
                                       for action in action_result["actions"]})
                        old_history[-1] = {"actions": list(merged.values()),
                            "submission": action_result.get("submission", old_history[-1].get("submission", {}))}
                    else:
                        turn_record = {"actions": action_result["actions"],
                            "submission": action_result.get("submission", {}),
                            "source_frontier": outcome.get("source_frontier") or _episode_source_frontier(
                                outcome.get("prepared_episode_payload") or item["payload"], history)}
                        if action_result.get("no_progress_observation"):
                            turn_record["no_progress_observation"] = action_result["no_progress_observation"]
                        old_history.append(turn_record)
                elif decision.get("mode") == "submit":
                    turn_record = {"actions": [], "submission": decision.get("submission", {}),
                        "source_frontier": outcome.get("source_frontier") or _episode_source_frontier(
                            outcome.get("prepared_episode_payload") or item["payload"], history),
                        "submission_validation": {key: value for key, value in action_result.items()
                                                  if key in {"valid", "reason", "source_validation_reason", "state", "gate_advisory", "advisory_valid"}}}
                    turn_record["submission_validation"]["source_validation_signature"] = _validation_signature(action_result)
                    if action_result.get("no_progress_observation"):
                        turn_record["no_progress_observation"] = action_result["no_progress_observation"]
                    old_history.append(turn_record)
                if client is not None:
                    context_payload = (outcome.get("prepared_episode_payload")
                        or locals().get("episode_payload") or item.get("payload") or {})
                    old_history = self._refit_latest_context_page(context_payload, old_history, client)
                outcome["action_history"] = old_history
                if action_result.get("response_validation_feedback"):
                    outcome["response_validation_feedback"] = action_result["response_validation_feedback"]
                if action_result.get("no_progress_observation"):
                    outcome["no_progress_observation"] = action_result["no_progress_observation"]
                outcome["state"] = final_state
                self.ledger.set_state(self.campaign_id, item["work_id"], final_state,
                                      result=outcome, decision=decision if decision else None,
                                      error=(action_result.get("reason") if final_state == "failed" else None))
                return {"work_id": item["work_id"], "kind": item["kind"], **outcome}
            raise NotImplementedError(f"No factual executor is registered for {item['kind']}")
        except EpisodeBudgetExhausted as exc:
            self.ledger.set_state(self.campaign_id, item["work_id"], "budget_exhausted",
                                  result={"budget": exc.budget}, error=str(exc))
            return {"work_id": item["work_id"], "kind": item["kind"],
                    "state": "budget_exhausted", "budget": exc.budget}
        except Exception as exc:
            self.ledger.set_state(self.campaign_id, item["work_id"], "failed", error=f"{type(exc).__name__}: {exc}")
            raise


def load_saved_codex_client(provider_id: str, *, model: str, reasoning_effort: str):
    """Load the user's saved provider; reject nonlocal/paid or mismatched models."""
    from services.api.db import Provider, Session
    with Session() as session:
        provider = session.get(Provider, provider_id)
        if provider is None:
            raise ValueError(f"Unknown saved provider ID: {provider_id}")
        snapshot = {"id": provider.id, "kind": provider.kind, "base_url": provider.base_url,
                    "model": provider.model, "allow_paid": provider.allow_paid,
                    "config": dict(provider.config or {})}
    if snapshot["kind"] != "codex_cli" or snapshot["model"] != model:
        raise ValueError("Study decisions require the saved Codex CLI provider and exact requested model")
    if snapshot["allow_paid"]:
        raise ValueError("Paid provider authorization must remain disabled for this study")
    if (snapshot["config"].get("reasoning_effort") or "medium") != reasoning_effort:
        raise ValueError("Saved provider reasoning effort differs from requested study setting")
    from research.agents.provider import ModelClient
    return ModelClient(snapshot, allow_paid=False)


def _action_work_id(parent_id: str, action: str, *parts: str) -> str:
    """Return a stable, readable child ID without hashing scientific content."""
    clean = lambda value: "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))[:48]
    return "episode--" + "--".join(clean(value) for value in (parent_id, action, *parts) if str(value))


def _is_replay_episode(value: Mapping[str, Any]) -> bool:
    """Recognize the registered replay families without rewriting provider identity."""
    nested = value.get("episode")
    nested_category = nested.get("category", "") if isinstance(nested, Mapping) else ""
    category = str(value.get("category", nested_category)).upper()
    kind = str(value.get("kind", "")).lower()
    return category in {"REPLAY", "SECOND_MODEL_REPLAY"} or kind == "replay"


def _episode_metric_contract(task: Mapping[str, Any], episode: Mapping[str, Any],
                             mutation: str) -> dict[str, Any]:
    """Use a revision only when that episode declares the revised objective."""
    factual = episode.get("factual_changes") or {}
    explicit = factual.get("evaluation_contract") or factual.get("C1_revision")
    if isinstance(explicit, Mapping):
        return dict(explicit)
    if mutation == "C1":
        revision = task.get("C1_revision")
        if isinstance(revision, Mapping):
            return dict(revision)
    primary = task.get("primary_metric", {})
    if isinstance(primary, Mapping):
        name = primary.get("name", primary.get("metric", ""))
        direction = primary.get("direction", task.get("primary_direction", ""))
    else:
        name = primary
        direction = task.get("primary_direction", "")
    return {"revised_primary_metric": name, "revised_direction": direction,
            "revised_side_constraints": task.get("side_constraints", [])}


def _episode_source_frontier(payload: Mapping[str, Any], history: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Persist the exact public evidence surface used for a no-progress check."""
    def stable(value):
        if isinstance(value, Mapping):
            return {str(key): stable(item) for key, item in value.items()
                if str(key) not in {"started_at", "finished_at", "elapsed_seconds", "latency_seconds",
                    "request_id", "response_id", "observed_request_usage"}}
        if isinstance(value, (list, tuple)):
            return [stable(item) for item in value]
        return copy.deepcopy(value)
    observed = {}
    for turn in history:
        if not isinstance(turn, Mapping):
            continue
        for action in turn.get("actions", []):
            if isinstance(action, Mapping) and action.get("state") == "completed" and action.get("action_work_id"):
                if _is_context_read_action(action):
                    continue
                key = str(action["action_work_id"])
                observed[key] = {"kind": action.get("kind"), "result": stable(action.get("result", {}))}
    observational_prefixes = ("Arm-public context catalog (metadata only): ",
        "Previously retrieved exact source pages (references only): ")
    messages = [copy.deepcopy(message) for message in payload.get("messages", [])
        if not (isinstance(message, Mapping) and message.get("role") == "system"
            and str(message.get("content", "")).startswith(observational_prefixes))]
    action_bindings = [copy.deepcopy(binding) for binding in payload.get("action_bindings", [])
        if not isinstance(binding, Mapping) or binding.get("analysis_operation") != "read_context_segment"]
    return {"messages": messages,
            "action_bindings": action_bindings,
            "task_contract": copy.deepcopy(payload.get("task_contract")),
            "original_pair_selection": copy.deepcopy(payload.get("original_pair_selection")),
            "factual_changes": copy.deepcopy(payload.get("factual_changes", {})),
            "completed_source_actions": observed}


def _semantic_submission(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep decision-bearing fields while ignoring prose that does not change a claim."""
    prose = {"claim", "rationale", "explanation", "notes", "justification"}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): copy.deepcopy(item) for key, item in value.items() if str(key) not in prose}


def _validation_signature(value: Mapping[str, Any]) -> dict[str, Any]:
    volatile = {"state", "reason", "response_validation_feedback", "request_new_decision",
        "pause_reason", "no_progress_observation", "source_validation_reason",
        "source_validation_signature", "observed_request_usage"}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): copy.deepcopy(item) for key, item in value.items() if str(key) not in volatile}


def _semantic_action_proposal(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    actions = value.get("actions")
    return {"mode": value.get("mode"),
        "actions": [{key: copy.deepcopy(action[key]) for key in
            ("kind", "action", "action_work_id", "configuration_id", "seed",
             "context_segment_id", "context_pointer", "context_offset", "context_limit") if key in action}
            for action in actions if isinstance(action, Mapping)] if isinstance(actions, list) else None,
        "submission": _semantic_submission(value.get("submission", {}))}


def _is_context_read_action(action: Mapping[str, Any]) -> bool:
    result = action.get("result") or {}
    return (isinstance(result, Mapping) and result.get("operation") == "read_context_segment") or \
        action.get("action_name") == "read_context_segment"


def _context_page_interval(action: Mapping[str, Any]) -> dict[str, Any] | None:
    """Identify only source characters that were actually returned to the caller."""
    result = action.get("result")
    if (action.get("state") != "completed" or not isinstance(result, Mapping)
            or result.get("delivery_status") == "not_delivered"
            or not isinstance(result.get("content"), str)):
        return None
    try:
        start, end = int(result["offset"]), int(result["next_offset"])
        returned = int(result.get("returned_characters", end - start))
        requested_limit = int(result.get("requested_limit", returned))
    except (KeyError, TypeError, ValueError):
        return None
    segment_id, pointer = result.get("segment_id"), result.get("pointer", "")
    content = result["content"]
    if (not isinstance(segment_id, str) or not segment_id or not isinstance(pointer, str)
            or start < 0 or end <= start or returned != end - start or len(content) != returned
            or requested_limit < returned):
        return None
    return {"segment_id": segment_id, "pointer": pointer, "start": start, "end": end,
        "requested_limit": requested_limit}


def _repeated_context_page_ranges(actions: Sequence[Mapping[str, Any]],
                                  history: Sequence[Mapping[str, Any]],
                                  frontier: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return covered read ranges and whether this turn returned any new source characters."""
    intervals: dict[tuple[str, str], list[tuple[int, int]]] = {}
    frontier_json = _json(frontier)
    for turn in history:
        if not isinstance(turn, Mapping) or _json(turn.get("source_frontier")) != frontier_json:
            continue
        for prior in turn.get("actions", []):
            if not isinstance(prior, Mapping) or not _is_context_read_action(prior):
                continue
            page = _context_page_interval(prior)
            if page is not None:
                key = (page["segment_id"], page["pointer"])
                intervals.setdefault(key, []).append((page["start"], page["end"]))

    repeated: list[dict[str, Any]] = []
    new_characters = False
    for action in actions:
        if not isinstance(action, Mapping) or not _is_context_read_action(action):
            continue
        page = _context_page_interval(action)
        if page is None:
            continue
        key = (page["segment_id"], page["pointer"])
        existing = sorted(intervals.get(key, []))
        cursor = page["start"]
        for start, end in existing:
            if end <= cursor:
                continue
            if start > cursor:
                break
            cursor = max(cursor, end)
            if cursor >= page["end"]:
                break
        if cursor >= page["end"]:
            repeated.append(page)
        else:
            new_characters = True
        intervals.setdefault(key, []).append((page["start"], page["end"]))
    return repeated, new_characters


def _context_page_no_progress(actions: Sequence[Mapping[str, Any]],
                              history: Sequence[Mapping[str, Any]],
                              frontier: Mapping[str, Any]) -> dict[str, Any] | None:
    repeated, new_characters = _repeated_context_page_ranges(actions, history, frontier)
    if not repeated or new_characters:
        return None
    ranges = sorted({(row["segment_id"], row["pointer"], row["start"], row["end"])
                     for row in repeated})
    normalized = [{"segment_id": segment, "pointer": pointer, "start": start, "end": end}
                  for segment, pointer, start, end in ranges]
    requests = sorted({(row["segment_id"], row["pointer"], row["start"], row["requested_limit"])
                       for row in repeated})
    normalized_requests = [{"segment_id": segment, "pointer": pointer,
        "offset": offset, "requested_limit": limit}
        for segment, pointer, offset, limit in requests]
    already_diagnosed = any(isinstance(turn.get("no_progress_observation"), Mapping)
        and turn["no_progress_observation"].get("context_page_requests") == normalized_requests
        and _json(turn.get("source_frontier")) == _json(frontier)
        for turn in history if isinstance(turn, Mapping))
    return {"state": "paused_no_progress" if already_diagnosed else "retryable",
        "request_new_decision": not already_diagnosed,
        "reason": "repeated_context_page_without_new_source_characters",
        "response_validation_feedback": None if already_diagnosed else (
            "This page only repeated source characters already delivered under the unchanged evidence frontier. "
            "Continue from an unseen offset, act on the available evidence, or identify a changed source question."),
        "pause_reason": ("The same source range recurred after a no-progress diagnostic without an evidence change. "
            "This episode is paused until the source frontier changes or an operator resumes it."
            if already_diagnosed else None),
        "no_progress_observation": {"context_page_ranges": normalized,
            "context_page_requests": normalized_requests,
            "same_source_bound_results": True, "new_evidence_added": False,
            "diagnostic_previously_delivered": already_diagnosed}}


def _scientific_action_history(history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove observational reads before scientific validation or progress accounting."""
    filtered = []
    for turn in history:
        if not isinstance(turn, Mapping):
            continue
        copied = copy.deepcopy(dict(turn))
        copied["actions"] = [copy.deepcopy(dict(action)) for action in turn.get("actions", [])
                             if isinstance(action, Mapping) and not _is_context_read_action(action)]
        if copied["actions"] or turn.get("submission_validation") or turn.get("submission"):
            filtered.append(copied)
    return filtered


def _semantic_raw_proposal(text: Any) -> Any:
    if not isinstance(text, str):
        return {"raw": repr(text)}
    try:
        value = json.loads(text)
    except ValueError:
        return {"raw": text}
    return _semantic_action_proposal(value)


def _normalize_episode_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the manifest's typed episode envelope without dropping provenance."""
    result = copy.deepcopy(dict(payload))
    nested = result.get("episode")
    if not isinstance(nested, Mapping):
        return result
    for key, value in nested.items():
        result.setdefault(key, copy.deepcopy(value))
    category = str(nested.get("category", result.get("category", ""))).upper()
    result["category"] = category
    result["kind"] = "replay" if category in {"REPLAY", "SECOND_MODEL_REPLAY"} else result.get("kind", "episode")
    result["task_id"] = str(nested.get("task_id", result.get("task_id", "")))
    result["family"] = str(nested.get("family", result.get("family", "")))
    result["arm"] = str(nested.get("arm", result.get("arm", result.get("design_arm", "")))).upper()
    result["stream"] = str(nested.get("stream", result.get("stream", "REPLAY" if category in {"REPLAY", "SECOND_MODEL_REPLAY"} else ""))).upper()
    result["case_id"] = nested.get("case_id", result.get("case_id"))
    result["repeat"] = nested.get("repeat", result.get("repeat"))
    result["factual_changes"] = copy.deepcopy(result.get("factual_changes") or {})
    result["source_snapshot_plan"] = result.get("source_snapshot_plan") or result.get("snapshot_plan_path")
    result["snapshot_plan_path"] = result.get("source_snapshot_plan")
    result["dataset_root"] = result.get("source_data_root") or result.get("dataset_root")
    result["source_spec_dir"] = result.get("source_spec_dir")
    result["data_ids_reference"] = result.get("data_ids_reference")
    result["dataset_version_ids"] = result.get("dataset_version_ids")
    return result


def _episode_mutation_id(episode: Mapping[str, Any]) -> str:
    factual = episode.get("factual_changes") or {}
    if not isinstance(factual, Mapping):
        factual = {}
    explicit = factual.get("mutation_id", episode.get("mutation_id", episode.get("mutation")))
    if isinstance(explicit, Mapping):
        explicit = explicit.get("id", explicit.get("mutation_id"))
    if explicit:
        return str(explicit).upper()
    nested = episode.get("episode") or {}
    explicit = nested.get("controller_mutation_id") if isinstance(nested, Mapping) else None
    if explicit:
        return str(explicit).upper()
    keys = {"evaluation_contract": "C1", "validation_input_config": "C2",
            "training_data_revision": "C3", "training_recipe_revision": "C4"}
    present = [mutation for key, mutation in keys.items() if key in factual]
    if "C1" in present and "C2" in present:
        return "C5"
    return present[0] if present else "C0"


def _task_anchor_configuration_id(task: Mapping[str, Any]) -> str | None:
    explicit = task.get("anchor_configuration_id")
    if explicit:
        return str(explicit)
    candidates = task.get("snapshot_configurations") or []
    for candidate in candidates:
        if isinstance(candidate, Mapping) and candidate.get("is_anchor") is True:
            value = candidate.get("configuration_id")
            return str(value) if value else None
    return None


def _same_registered_mutation_selection(actual: Mapping[str, Any], declared: Mapping[str, Any]) -> bool:
    """Compare the registered selected IDs and values, ignoring only runtime library version."""
    if not isinstance(actual, Mapping) or not isinstance(declared, Mapping):
        return False
    ignored = {"numpy_version"}
    return {key: value for key, value in actual.items() if key not in ignored} == {
        key: value for key, value in declared.items() if key not in ignored}


def _c2_evaluation_overrides(family: str, noise: Any, factual: Mapping[str, Any], revision_id: Any) -> dict[str, Any]:
    """Map the declared C2 perturbation to the training loader's eval-only fields."""
    if not isinstance(noise, Mapping):
        noise = {}
    explicit = factual.get("evaluation_config_overrides")
    if explicit is not None:
        if not isinstance(explicit, Mapping):
            raise ValueError("evaluation_config_overrides must be an object")
        allowed = {"validation_noise_std", "validation_noise_seed", "validation_noise_std_in_training_sd"}
        if set(explicit) - allowed:
            raise ValueError("C2 can override only registered researcher validation-noise fields")
        overrides = dict(explicit)
    else:
        seed = int(noise.get("seed", noise.get("validation_noise_seed", factual.get("noise_seed", 0))))
        std = float(noise.get("sd", noise.get("std", noise.get("noise_std",
                    noise.get("validation_noise_std", 0.02)))))
        if family == "etth1":
            overrides = {"validation_noise_std_in_training_sd": std, "validation_noise_seed": seed}
        elif family in {"darcy", "bloodmnist"}:
            overrides = {"validation_noise_std": std, "validation_noise_seed": seed}
        else:
            raise ValueError(f"No registered C2 validation transform exists for {family}")
    if revision_id is not None:
        overrides["evaluation_revision_id"] = str(revision_id)
    return overrides


def verify_episode_submission(episode: Mapping[str, Any], history: Sequence[Mapping[str, Any]],
                              submission: Mapping[str, Any], *, runtime: Any = None) -> dict[str, Any]:
    """Accept an episode only after matching its final claim to actual operations."""
    history = _scientific_action_history(history)
    from .end_to_end_runtime import is_end_to_end, verify_end_to_end_submission
    if is_end_to_end(episode):
        if runtime is None:
            return {"state": "failed", "valid": False, "reason": "end_to_end_runtime_context_missing"}
        result = verify_end_to_end_submission(runtime, episode, history, submission)
        return _verify_required_controller_restart(runtime, episode, result)
    actions = [dict(action) for record in history for action in record.get("actions", [])
               if isinstance(action, Mapping)]
    completed = [action for action in actions if action.get("state") == "completed"]
    category = str(episode.get("category", (episode.get("episode") or {}).get("category", ""))).upper()
    is_replay = category in {"REPLAY", "SECOND_MODEL_REPLAY"} or str(episode.get("kind", "")) == "replay"
    if is_replay:
        if any(action.get("kind") in {"fit", "infer"} for action in actions):
            return {"state": "failed", "valid": False, "reason": "replay_attempted_physical_scientific_work"}
        if not completed or any(action.get("kind") not in {"analysis", "rescore", "replay_transition"} for action in completed):
            return {"state": "failed", "valid": False, "reason": "replay_has_no_completed_registered_operation"}
        mutation = _episode_mutation_id(episode)
        selected = submission.get("selected_configuration_id")
        selection_results = [action.get("result") for action in completed
                             if action.get("kind") == "analysis"
                             and (action.get("result") or {}).get("operation") == "select"]
        comparisons = [action for action in completed if (action.get("result") or {}).get("operation") == "compare"]
        if mutation == "C1" and (not selection_results or not comparisons):
            return {"state": "failed", "valid": False, "reason": "replay_c1_requires_rescore_select_and_paired_compare"}
        if selected is not None and not any(result.get("selected_configuration_id") == selected for result in selection_results):
            return {"state": "failed", "valid": False,
                    "reason": "replay_selection_not_independently_recomputed"}
        return {"state": "completed", "valid": True, "replay_only": True,
                "completed_action_work_ids": [action.get("action_work_id") for action in completed],
                "selected_configuration_id": selected, "claim": submission.get("claim")}
    if not completed:
        return {"state": "failed", "valid": False, "reason": "no_completed_source_bound_action"}
    selected = submission.get("selected_configuration_id")
    selection_results = [action.get("result") for action in completed
                         if action.get("kind") == "analysis"
                         and (action.get("result") or {}).get("operation") == "select"]
    if selected is not None and not any(result.get("selected_configuration_id") == selected
                                        for result in selection_results):
        return {"state": "failed", "valid": False,
            "reason": "selected_configuration_not_independently_recomputed",
            "submitted_selected_configuration_id": selected,
            "observed_selection_ids": [r.get("selected_configuration_id") for r in selection_results]}
    stream = str(episode.get("stream", episode.get("update_type", ""))).upper()
    mutation = str((episode.get("factual_changes") or {}).get("mutation_id",
                    episode.get("mutation_id", episode.get("mutation", "")))).upper()
    completed_by_id = {str(action.get("action_work_id")): action for action in completed}
    bindings = [dict(binding) for binding in episode.get("action_bindings", []) if isinstance(binding, Mapping)]
    if stream in {"EVAL_UPDATE", "TRAIN_UPDATE"}:
        operation_ids = {str(binding.get("work_id")): binding for binding in bindings}
        scientific_bindings = [binding for binding in bindings if binding.get("kind") in {"fit", "infer", "rescore"}]
        missing = [binding.get("work_id") for binding in scientific_bindings
                   if str(binding.get("work_id")) not in completed_by_id]
        if missing:
            return {"state": "failed", "valid": False, "reason": "required_pairwise_scientific_operations_incomplete",
                    "missing_action_work_ids": missing}
        for binding in scientific_bindings:
            action = completed_by_id[str(binding["work_id"])]
            result = action.get("result") or {}
            receipt = result.get("receipt") or result.get("inference_receipt") or result
            if binding.get("kind") == "fit" and not (receipt.get("status") == "SUCCEEDED"
                    and receipt.get("formal_score_eligible") is True and receipt.get("hidden_test_opened") is False):
                return {"state": "failed", "valid": False, "reason": "training_update_receipt_failed_source_validation",
                        "action_work_id": binding["work_id"]}
            if binding.get("kind") == "infer" and not (result.get("supervisor", {}).get("status") == "completed"
                    and result.get("receipt", {}).get("hidden_test_opened") is False):
                return {"state": "failed", "valid": False, "reason": "evaluation_inference_receipt_failed_source_validation",
                        "action_work_id": binding["work_id"]}
            if binding.get("kind") == "rescore" and not (result.get("hidden_test_opened") is False
                    and isinstance(result.get("metrics"), Mapping) and result.get("metrics")):
                return {"state": "failed", "valid": False, "reason": "evaluation_rescore_failed_source_validation",
                        "action_work_id": binding["work_id"]}
        required_analysis = {binding.get("action_name") for binding in bindings
                             if binding.get("action_name") in {"select", "compare"}}
        completed_analysis = {((action.get("result") or {}).get("operation")) for action in completed
                              if action.get("kind") == "analysis"}
        absent_analysis = sorted(required_analysis - completed_analysis)
        if "select" not in completed_analysis or "compare" not in completed_analysis or absent_analysis:
            return {"state": "failed", "valid": False,
                    "reason": "required_pairwise_selection_and_comparison_incomplete",
                    "missing_operations": absent_analysis or sorted({"select", "compare"} - completed_analysis)}
        comparisons = [action.get("result") or {} for action in completed
                       if action.get("kind") == "analysis" and (action.get("result") or {}).get("operation") == "compare"]
        if selected is None or not selection_results:
            return {"state": "failed", "valid": False, "reason": "submission_missing_recomputed_selection"}
        task_id = str(episode.get("task_id", ""))
        task = episode.get("task_contract") or next((row for row in (runtime.manifest.get("tasks", []) if runtime else [])
                     if str(row.get("task_id")) == task_id), {})
        selection_binding = next((row for row in episode.get("action_bindings", [])
            if isinstance(row, Mapping) and row.get("action_name") == "select"), {})
        anchor_id = _task_anchor_configuration_id(task) if task else selection_binding.get("anchor_configuration_id")
        expected_seeds = sorted(map(str, (episode.get("original_pair_selection") or {}).get("paired_seeds", [])))
        if not expected_seeds:
            compare_binding = next((row for row in episode.get("action_bindings", [])
                if isinstance(row, Mapping) and row.get("action_name") == "compare"), {})
            expected_seeds = sorted(map(str, compare_binding.get("compared_seeds", [])))
        valid_comparison = any(
            int(result.get("pair_count", 0)) == 2
            and len(result.get("paired_differences_right_minus_left", [])) == 2
            and list(map(str, result.get("compared_configuration_ids", []))) == [str(anchor_id), str(selected)]
            and sorted(map(str, result.get("compared_seeds", []))) == expected_seeds
            for result in comparisons)
        if not valid_comparison:
            return {"state": "failed", "valid": False,
                "reason": "paired_comparison_must_match_source_selection_and_both_registered_seeds",
                "selected_configuration_id": selected, "anchor_configuration_id": anchor_id,
                "expected_seeds": expected_seeds,
                "observed_comparisons": [{key: result.get(key) for key in
                    ("compared_configuration_ids", "compared_seeds", "pair_count")} for result in comparisons]}
    return _verify_required_controller_restart(runtime, episode, {"state": "completed", "valid": True,
        "completed_action_work_ids": [action.get("action_work_id") for action in completed],
        "selected_configuration_id": selected, "selected_work_ids": [r.get("selected_work_id") for r in selection_results],
        "claim": submission.get("claim")})


def _verify_required_controller_restart(runtime: Any, episode: Mapping[str, Any],
                                         result: Mapping[str, Any]) -> dict[str, Any]:
    policy = episode.get("controller_restart")
    if not isinstance(policy, Mapping):
        nested = episode.get("episode")
        policy = nested.get("controller_restart") if isinstance(nested, Mapping) else None
    if not isinstance(policy, Mapping) or policy.get("required") is not True or result.get("valid") is not True:
        return dict(result)
    if runtime is None or not hasattr(runtime, "ledger"):
        return {**dict(result), "state": "failed", "valid": False,
                "reason": "required_controller_restart_evidence_missing_runtime"}
    with runtime.ledger.connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_study_controller_restarts'").fetchone()
        if not exists:
            return {**dict(result), "state": "failed", "valid": False,
                    "reason": "required_controller_restart_table_missing"}
        rows = db.execute("SELECT * FROM forest_study_controller_restarts WHERE campaign_id=? AND work_id=?",
                          (runtime.campaign_id, str(episode.get("work_id", "")))).fetchall()
    if len(rows) != 1:
        return {**dict(result), "state": "failed", "valid": False,
                "reason": "required_controller_restart_receipt_missing_or_duplicated"}
    try:
        row = dict(rows[0])
        saved_policy = json.loads(row["policy_json"])
        receipt = json.loads(row["receipt_json"])
    except (TypeError, ValueError):
        return {**dict(result), "state": "failed", "valid": False,
                "reason": "required_controller_restart_receipt_invalid_json"}
    milestone = str(policy.get("milestone", ""))
    previous_exit = receipt.get("previous_exit") if isinstance(receipt, Mapping) else None
    previous_worker = receipt.get("previous_worker") if isinstance(receipt, Mapping) else None
    resumed_worker = receipt.get("resumed_worker") if isinstance(receipt, Mapping) else None
    proof = receipt.get("reload_proof") if isinstance(receipt, Mapping) else None
    checkpoint_receipt = receipt.get("checkpoint_receipt") if isinstance(receipt, Mapping) else None
    completion = previous_exit.get("completion") if isinstance(previous_exit, Mapping) else None
    previous_identity = previous_exit.get("identity") if isinstance(previous_exit, Mapping) else None
    if isinstance(previous_exit, Mapping):
        completion = completion or previous_exit.get("completion_receipt")
        previous_identity = previous_identity or previous_exit
    def process_key(value):
        if not isinstance(value, Mapping):
            return None
        pid = value.get("pid", value.get("process_id"))
        created = value.get("process_created", value.get("create_time"))
        return (pid, created) if pid is not None and created is not None else None
    def same_worker(value, pid, created):
        key = process_key(value)
        return key == (pid, created)
    checkpoint_valid = False
    if isinstance(checkpoint_receipt, Mapping) and checkpoint_receipt.get("path") and checkpoint_receipt.get("artifact"):
        try:
            checkpoint_path = Path(str(checkpoint_receipt["path"]))
            from research.agents.processes import artifact_matches
            publication = _read_json(checkpoint_path)
            artifact = checkpoint_receipt["artifact"]
            saved_checkpoint = json.loads(row["checkpoint_json"])
            checkpoint_valid = (
                checkpoint_path.is_file() and artifact_matches(checkpoint_path, artifact)
                and checkpoint_receipt.get("checkpoint") == saved_checkpoint
                and publication.get("checkpoint") == saved_checkpoint
                and publication.get("policy") == dict(policy)
                and publication.get("request_id") == row["request_id"]
                and publication.get("dispatch_id") == row["request_dispatch_id"]
                and publication.get("worker") == {"pid": row["request_pid"],
                    "process_created": row["request_process_created"]}
                and artifact in (previous_exit.get("artifacts") or [])
            )
        except (OSError, ValueError, TypeError):
            checkpoint_valid = False
    proof_valid = (isinstance(proof, Mapping)
        and isinstance(proof.get("action_history"), list)
        and isinstance(proof.get("model_request_ids"), list)
        and ((proof.get("arm") == "AIDE" and isinstance(proof.get("native_nodes"), list)
              and proof.get("rng_state") is not None and isinstance(proof.get("configuration"), Mapping))
             or proof.get("arm") == "FULL"))
    restart_valid = (
        saved_policy == dict(policy)
        and row["state"] == "resumed"
        and receipt.get("milestone") == milestone
        and receipt.get("checkpoint_reloaded") is True
        and proof_valid and checkpoint_valid
        and isinstance(previous_exit, Mapping)
        and str(previous_exit.get("status", "")).lower() == "completed"
        and isinstance(completion, Mapping) and completion.get("exit_code") == 0
        and isinstance(previous_identity, Mapping)
        and same_worker(previous_identity, row["request_pid"], row["request_process_created"])
        and same_worker(previous_worker, row["request_pid"], row["request_process_created"])
        and same_worker(resumed_worker, row["resume_pid"], row["resume_process_created"])
        and receipt.get("request_id") == row["request_id"]
        and receipt.get("resume_dispatch_id") == row["resume_dispatch_id"]
        and process_key(previous_worker) is not None and process_key(resumed_worker) is not None
        and process_key(previous_worker) != process_key(resumed_worker)
    )
    if not restart_valid:
        return {**dict(result), "state": "failed", "valid": False,
                "reason": "required_controller_restart_receipt_failed_source_validation"}
    return {**dict(result), "controller_restart": {"state": row["state"],
        "milestone": milestone, "previous_worker": dict(previous_worker),
        "resumed_worker": dict(resumed_worker), "checkpoint_reloaded": True}}


def _treatment_evidence_view(arm: str, records: Sequence[Mapping[str, Any]],
                             nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Render the same evidence as typed graph, ID-only graph, or flat artifacts."""
    observations = [dict(record) for record in records]
    if arm in {"FULL", "FULL_NO_GATE"}:
        return {"view": "typed_graph", "nodes": list(nodes),
                "edges": list(edges), "observations": observations}
    if arm == "DAG_ID":
        return {"view": "id_only_graph", "node_ids": [str(node["id"]) for node in nodes],
                "edges": [{"from": str(edge["from"]), "to": str(edge["to"])} for edge in edges],
                "observations": observations}
    if arm == "AIDE":
        return {"view": "native_agent_public_artifact_index",
                "artifacts": observations, "observations": observations}
    if arm == "ID_ONLY":
        # Preserve factual observations and operation records while removing
        # graph relationships from this treatment arm.
        return {"view": "identifier_only", "node_ids": [str(node["id"]) for node in nodes],
                "observations": observations}
    if arm == "RERUN_ALL":
        return {"view": "rerun_all_flat_artifact_index", "artifacts": observations,
            "observations": observations,
            "instruction": "Recompute permitted metrics from the saved researcher validation predictions; no GPU work is available in replay."}
    if arm != "FLAT":
        raise ValueError(f"Unknown evidence treatment arm: {arm}")
    artifacts = []
    raw_logs = []
    for record in records:
        receipt_ref = record.get("receipt_path")
        artifact = dict(record)
        if receipt_ref:
            receipt_path = Path(str(receipt_ref))
            log_path = receipt_path.parent / "training.jsonl"
            if log_path.is_file():
                raw_logs.append({"configuration_id": record.get("configuration_id"),
                    "seed": record.get("seed"), "path": str(log_path),
                    "lines": log_path.read_text(encoding="utf-8").splitlines()})
        artifacts.append(artifact)
    return {"view": "flat_artifact_index", "artifacts": artifacts,
            "raw_training_logs": raw_logs, "observations": observations}


def _treatment_history_view(arm: str, history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    nodes = []
    edges = []
    observational_context_reads = []
    all_reads = []
    for episode_turn, turn in enumerate(history):
        if turn.get("submission_validation"):
            nodes.append({"id": f"turn-{episode_turn}-submission", "kind": "submission_validation",
                "state": (turn.get("submission_validation") or {}).get("state"),
                "result": dict(turn.get("submission_validation") or {})})
        for operation in turn.get("actions", []):
            if not isinstance(operation, Mapping):
                continue
            action_id = str(operation.get("action_work_id", f"turn-{episode_turn}"))
            result = dict(operation.get("result") or {})
            if _is_context_read_action(operation):
                all_reads.append({"id": action_id, "episode_turn": episode_turn, "result": result})
                continue
            nodes.append({"id": action_id, "kind": operation.get("kind"),
                "state": operation.get("state"), "result": result})
            edges.append({"from": str(episode_turn), "to": action_id,
                          "relation": "observed_after"})
    for index, row in enumerate(all_reads):
        result = dict(row["result"])
        content = result.pop("content", None)
        reference = {key: result[key] for key in (
            "segment_id", "pointer", "offset", "next_offset", "total_characters",
            "returned_characters", "requested_limit", "complete", "capacity_limited")
            if key in result}
        reference.update({"id": row["id"], "operation": "read_context_segment",
            "scientific_progress_eligible": False})
        if result.get("delivery_status"):
            reference["delivery_status"] = result["delivery_status"]
        if result.get("delivery_reason"):
            reference["delivery_reason"] = result["delivery_reason"]
        if index == len(all_reads) - 1 and row["episode_turn"] == len(history) - 1 \
                and result.get("delivery_status") != "not_delivered" and isinstance(content, str):
            # Only the current page body enters a fresh model prompt. The
            # complete immutable segment and every action result remain in the
            # ledger and can be fetched by the next exact offset.
            reference["content"] = content
        observational_context_reads.append(reference)
    if arm in {"FULL", "FULL_NO_GATE"}:
        return {"view": "typed_operation_graph", "nodes": nodes, "edges": edges,
                "observational_context_reads": observational_context_reads}
    if arm == "DAG_ID":
        return {"view": "operation_id_graph", "node_ids": [node["id"] for node in nodes],
            "edges": [{"from": edge["from"], "to": edge["to"]} for edge in edges],
            "results": [node["result"] for node in nodes],
            "observational_context_reads": observational_context_reads}
    if arm == "FLAT":
        return {"view": "flat_operation_artifacts", "operations": nodes,
            "raw_training_log_lines": [line for node in nodes for line in
                (node["result"].get("receipt", {}).get("training_log_lines", []))],
            "observational_context_reads": observational_context_reads}
    if arm == "AIDE":
        return {"view": "native_agent_operation_artifacts", "operations": nodes,
                "observational_context_reads": observational_context_reads}
    if arm == "ID_ONLY":
        # The treatment removes graph propagation, but retains the registered
        # evidence identities and their observed records for information parity.
        return {"view": "identifier_records_operation_history",
            "operations": [{"id": node["id"], "result": node["result"]} for node in nodes],
            "results": [node["result"] for node in nodes],
            "observational_context_reads": observational_context_reads}
    if arm == "RERUN_ALL":
        return {"view": "rerun_all_operation_artifacts", "operations": nodes,
                "observational_context_reads": observational_context_reads}
    raise ValueError(f"Unknown evidence treatment arm: {arm}")


def numpy_score_predictions(family: str, predictions, validation_targets, metadata: Mapping[str, Any]) -> dict[str, float]:
    """Independent NumPy implementation of the registered validation metrics."""
    import numpy as np
    pred = np.asarray(predictions, dtype=np.float64)
    target = np.asarray(validation_targets)
    if len(pred) != len(target) or not np.isfinite(pred).all():
        raise ValueError("Predictions must be finite and aligned with validation targets")
    if family == "darcy":
        mean, std = float(metadata["target_mean"]), float(metadata["target_std"])
        actual = target.astype(np.float64) * std + mean
        delta = pred - actual
        denom = np.maximum(np.linalg.norm(actual.reshape(len(actual), -1), axis=1), 1e-12)
        relative_l2 = float(np.mean(np.linalg.norm(delta.reshape(len(delta), -1), axis=1) / denom))
        pred_dx, true_dx = np.diff(pred, axis=-2), np.diff(actual, axis=-2)
        pred_dy, true_dy = np.diff(pred, axis=-1), np.diff(actual, axis=-1)
        ratios = []
        for index in range(len(actual)):
            error = np.concatenate(((pred_dx[index] - true_dx[index]).ravel(),
                                    (pred_dy[index] - true_dy[index]).ravel()))
            truth = np.concatenate((true_dx[index].ravel(), true_dy[index].ravel()))
            ratios.append(np.linalg.norm(error) / max(float(np.linalg.norm(truth)), 1e-12))
        relative_grad = float(np.mean(ratios))
        return {"relative_l2": relative_l2, "relative_gradient_l2": relative_grad}
    if family == "bloodmnist":
        labels = target.astype(np.int64).reshape(-1)
        if pred.ndim != 2 or pred.shape[1] != 8 or np.any((labels < 0) | (labels > 7)):
            raise ValueError("BloodMNIST predictions/labels must use fixed eight-class output")
        predicted = np.argmax(pred, axis=1)
        confusion = np.zeros((8, 8), dtype=np.int64)
        np.add.at(confusion, (labels, predicted), 1)
        row = confusion.sum(axis=1)
        if np.any(row == 0):
            raise ValueError("BloodMNIST validation split lacks a class needed for balanced accuracy")
        tp = np.diag(confusion).astype(np.float64)
        precision_recall_den = 2 * tp + confusion.sum(axis=0) + row - 2 * tp
        f1 = np.divide(2 * tp, precision_recall_den, out=np.zeros(8, dtype=np.float64), where=precision_recall_den > 0)
        shifted = pred - pred.max(axis=1, keepdims=True)
        log_norm = np.log(np.exp(shifted).sum(axis=1))
        nll = float(np.mean(log_norm - shifted[np.arange(len(labels)), labels]))
        return {"accuracy": float(np.mean(predicted == labels)), "macro_f1": float(f1.mean()),
                "balanced_accuracy": float(np.mean(tp / row)), "nll": nll}
    if family == "etth1":
        mean = float(metadata["normalization_mean"][6])
        std = float(metadata["normalization_std_population"][6])
        actual = target.astype(np.float64) * std + mean
        difference = pred - actual
        return {"mae": float(np.mean(np.abs(difference))), "rmse": float(np.sqrt(np.mean(difference ** 2)))}
    raise ValueError(f"Unsupported family for scoring: {family}")


def rescore_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np
    from .training_runtime import prepare_data
    required = ("campaign_path", "config_path", "prediction_path", "reference_receipt_path", "data_root", "output_path")
    if any(not binding.get(key) for key in required):
        raise ValueError("Rescore binding needs explicit config, predictions, researcher data, and output paths")
    config = _read_json(Path(binding["config_path"]))
    campaign = Path(binding["campaign_path"]).resolve()
    data_root = Path(binding["data_root"]).resolve()
    if data_root != (campaign / "data" / "researcher").resolve():
        raise ValueError("Rescore data_root must be this campaign's explicit researcher-only data directory")
    data = prepare_data(str(config["family"]), data_root, config)
    with np.load(binding["prediction_path"], allow_pickle=False) as archive:
        predictions = archive["predictions"]
        ids = archive["sample_ids"].astype(str).tolist()
    expected_ids = [str(x) for x in data.metadata["validation_sample_ids"]]
    if ids != expected_ids:
        raise ValueError("Saved prediction sample IDs differ from the exact researcher validation order")
    metrics = numpy_score_predictions(str(config["family"]), predictions, data.validation_y.numpy(), data.metadata)
    receipt = _read_json(Path(binding["reference_receipt_path"]))
    expected_prediction = (Path(binding["reference_receipt_path"]).resolve().parent /
                          str(receipt.get("validation_prediction_file", ""))).resolve()
    if (receipt.get("effective_config") != config or receipt.get("hidden_test_opened") is not False
            or Path(binding["prediction_path"]).resolve() != expected_prediction):
        raise ValueError("Reference receipt does not certify this exact config/prediction artifact")
    recorded = receipt.get("validation_metrics") or {}
    for key, value in metrics.items():
        if key not in recorded or not math.isclose(float(recorded[key]), value, rel_tol=1e-6, abs_tol=1e-8):
            raise ValueError(f"Independent rescore disagrees with fit receipt metric {key}")
    result = {"status": "SUCCEEDED", "action_work_id": binding["work_id"],
              "source_config_path": str(Path(binding["config_path"]).resolve()),
              "prediction_path": str(Path(binding["prediction_path"]).resolve()),
              "validation_sample_ids": expected_ids, "metrics": metrics,
              "scoring_implementation": "independent_numpy_v1", "hidden_test_opened": False}
    Path(binding["output_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(binding["output_path"]).write_text(_json(result) + "\n", encoding="utf-8")
    return result


def execute_registered_analysis(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Run manifest-defined selection/comparison over actual receipt files."""
    operation = binding.get("analysis_operation")
    if operation in {"end_to_end_select", "end_to_end_compare"}:
        from .end_to_end_runtime import execute_end_to_end_analysis
        return execute_end_to_end_analysis(binding)
    entries = binding.get("evidence", [])
    if operation not in {"select", "compare", "inspect"} or not isinstance(entries, list) or not entries:
        raise ValueError("Analysis requires select/compare/inspect and explicit evidence references")
    loaded = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Every analysis evidence entry must be an object")
        if not entry.get("receipt_path"):
            if operation == "inspect":
                loaded.append({"work_id": entry.get("work_id"), "configuration_id": entry.get("configuration_id"),
                    "seed": entry.get("seed"), "state": entry.get("state"), "metrics": dict(entry.get("metrics") or {}),
                    "fit_gpu_seconds": entry.get("fit_gpu_seconds"), "receipt": None})
                continue
            raise ValueError("Every selection/comparison evidence entry needs its original receipt path")
        receipt = _read_json(Path(entry["receipt_path"]))
        if entry.get("evidence_kind") == "independent_rescore":
            metrics = receipt.get("metrics")
            if (receipt.get("hidden_test_opened") is not False or not isinstance(metrics, Mapping)
                    or not metrics):
                raise ValueError("Independent rescore evidence is missing its source-validated metrics")
            gpu_seconds = entry.get("fit_gpu_seconds")
            if gpu_seconds is None and binding.get("ledger_path") and entry.get("configuration_id"):
                matching_fit = next((candidate for candidate in binding.get("source_fit_work_ids", [])
                                     if str(candidate.get("configuration_id")) == str(entry.get("configuration_id"))
                                     and str(candidate.get("seed")) == str(entry.get("seed"))), None)
                if matching_fit:
                    gpu_seconds = _measured_gpu_seconds(Path(binding["ledger_path"]), str(matching_fit["work_id"]),
                        str(binding.get("campaign_id", "")), str(binding.get("ledger_id", "")))
            loaded.append({"work_id": entry.get("work_id"),
                "configuration_id": entry.get("configuration_id"), "seed": entry.get("seed"),
                "metrics": dict(metrics), "fit_gpu_seconds": gpu_seconds,
                "receipt": {"status": "SUCCEEDED", "hidden_test_opened": False,
                            "formal_score_eligible": True, "rescore_source": str(entry["receipt_path"])}})
            continue
        if receipt.get("status") != "SUCCEEDED":
            if operation == "inspect":
                loaded.append({"work_id": entry.get("work_id"), "configuration_id": entry.get("configuration_id"),
                    "seed": entry.get("seed"), "state": receipt.get("status", "invalid"), "metrics": {},
                    "fit_gpu_seconds": entry.get("fit_gpu_seconds"), "receipt": receipt})
                continue
            raise ValueError("Analysis cannot treat an incomplete fit as evidence")
        metrics = receipt.get("validation_metrics")
        if not isinstance(metrics, Mapping) or not metrics:
            raise ValueError("Successful receipt has no validation metrics")
        fit_gpu_seconds = entry.get("fit_gpu_seconds")
        if fit_gpu_seconds is None and binding.get("ledger_path") and entry.get("work_id"):
            fit_gpu_seconds = _measured_gpu_seconds(Path(binding["ledger_path"]), str(entry["work_id"]),
                str(binding.get("campaign_id", "")), str(binding.get("ledger_id", "")))
        loaded.append({"work_id": entry.get("work_id", receipt.get("work_id")),
            "configuration_id": entry.get("configuration_id", receipt.get("configuration_id")),
            "seed": entry.get("seed", receipt.get("seed")), "metrics": dict(metrics),
            "fit_gpu_seconds": fit_gpu_seconds, "receipt": receipt})
    if operation == "inspect":
        result = {"status": "SUCCEEDED", "operation": "inspect", "evidence": [
            {"work_id": row["work_id"], "configuration_id": row["configuration_id"], "seed": row["seed"],
             "state": row.get("state", "completed" if row["receipt"] else "not_run"),
             "metrics": row["metrics"], "fit_gpu_seconds": row["fit_gpu_seconds"],
             "hidden_test_opened": (row["receipt"] or {}).get("hidden_test_opened")}
            for row in loaded]}
        output = binding.get("output_path")
        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(_json(result) + "\n", encoding="utf-8")
        return result
    metric = str(binding.get("metric", ""))
    if operation == "select":
        direction = binding.get("direction")
        if direction not in {"minimize", "maximize"}:
            raise ValueError("Selection direction must be explicitly minimize or maximize")
        eligible = []
        def metric_value(row, name=metric):
            return row.get("fit_gpu_seconds") if name == "fit_gpu_seconds" else row.get("metrics", {}).get(name)
        anchor_id = binding.get("anchor_configuration_id")
        by_configuration: dict[str, list[dict[str, Any]]] = {}
        for row in loaded:
            by_configuration.setdefault(str(row["configuration_id"]), []).append(row)
        anchor_rows = by_configuration.get(str(anchor_id), []) if anchor_id is not None else []
        if binding.get("side_constraints") and (not anchor_rows or any(metric_value(row) is None for row in anchor_rows)):
            raise ValueError("Declared side constraints need the actual fixed-anchor receipt and metric")
        anchor_by_seed = {str(row.get("seed")): row for row in anchor_rows}
        for configuration_id, group in by_configuration.items():
            if binding.get("exclude_anchor") and configuration_id == str(anchor_id):
                continue
            if not group or any(metric_value(row) is None or not math.isfinite(float(metric_value(row))) for row in group):
                continue
            if any(row["receipt"].get("formal_score_eligible") is False and binding.get("require_formal", False)
                   for row in group):
                continue
            if any(row["receipt"].get("hidden_test_opened") is not False for row in group):
                continue
            if binding.get("side_constraints") and configuration_id != str(anchor_id):
                passes = True
                candidate_by_seed = {str(row.get("seed")): row for row in group}
                for seed, anchor in anchor_by_seed.items():
                    candidate = candidate_by_seed.get(seed)
                    if candidate is None:
                        passes = False
                        break
                    for constraint in binding["side_constraints"]:
                        key = str(constraint["metric"])
                        margin = float(constraint["margin"])
                        candidate_value = float(metric_value(candidate, key) if metric_value(candidate, key) is not None else float("nan"))
                        anchor_value = float(metric_value(anchor, key) if metric_value(anchor, key) is not None else float("nan"))
                        if not (math.isfinite(candidate_value) and math.isfinite(anchor_value)):
                            passes = False
                            break
                        if constraint.get("allowed_direction") == "at_most_anchor_times" and candidate_value > margin * anchor_value:
                            passes = False
                            break
                        if constraint.get("allowed_direction") == "at_least_anchor_times" and candidate_value < margin * anchor_value:
                            passes = False
                            break
                        if constraint.get("allowed_direction") == "no_more_than_absolute_drop" and candidate_value < anchor_value - margin:
                            passes = False
                            break
                    if not passes:
                        break
                if not passes:
                    continue
            # Selection is over the paired-seed mean, not an arbitrary first
            # seed or an order inherited from manifest serialization.
            primary_mean = sum(float(metric_value(row)) for row in group) / len(group)
            eligible.append({"configuration_id": configuration_id, "rows": group,
                             "selected_value": primary_mean,
                             "work_id": min(str(row["work_id"]) for row in group)})
        constraint_failure = False
        if binding.get("side_constraints") and not any(row["configuration_id"] != anchor_id for row in eligible):
            # Preserve the preregistered fallback: report no eligible non-anchor,
            # then identify the best observed non-anchor without claiming gain.
            non_anchor = [row for row in loaded if str(row["configuration_id"]) != str(anchor_id)
                          and metric_value(row) is not None and row["receipt"]
                          and row["receipt"].get("hidden_test_opened") is False]
            if non_anchor:
                grouped = {}
                for row in non_anchor:
                    grouped.setdefault(str(row["configuration_id"]), []).append(row)
                eligible = [{"configuration_id": key, "rows": rows,
                    "selected_value": sum(float(metric_value(row)) for row in rows) / len(rows),
                    "work_id": min(str(row["work_id"]) for row in rows)} for key, rows in grouped.items()]
                constraint_failure = True
        if not eligible:
            raise ValueError("No receipt satisfies the declared selection metric and eligibility requirements")
        optimum = (min if direction == "minimize" else max)(float(row["selected_value"]) for row in eligible)
        tied = sorted((row for row in eligible if math.isclose(float(row["selected_value"]), optimum,
            rel_tol=1e-12, abs_tol=1e-15)), key=lambda row: str(row["configuration_id"]))
        best = tied[0]
        result = {"status": "SUCCEEDED", "operation": operation, "metric": metric,
                  "direction": direction, "selected_work_id": best["work_id"],
                  "selected_configuration_id": best["configuration_id"],
                  "selected_value": float(best["selected_value"]),
                  "selected_configuration_ids_tied": [row["configuration_id"] for row in tied],
                  "constraint_satisfied": not constraint_failure,
                  "eligible_configuration_ids": sorted(row["configuration_id"] for row in eligible),
                  "eligible_work_ids": sorted(row["work_id"] for row in eligible),
                  "seed_values": {str(row["seed"]): float(metric_value(row)) for row in best["rows"]}}
    else:
        pairs = binding.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            raise ValueError("Comparison requires explicit paired work-ID entries")
        by_id = {row["work_id"]: row for row in loaded}
        differences = []
        for pair in pairs:
            if not isinstance(pair, Mapping) or pair.get("left") not in by_id or pair.get("right") not in by_id:
                raise ValueError("A comparison pair references missing receipts")
            left, right = by_id[pair["left"]], by_id[pair["right"]]
            left_value, right_value = (left.get("fit_gpu_seconds"), right.get("fit_gpu_seconds")) if metric == "fit_gpu_seconds" else (left["metrics"].get(metric), right["metrics"].get(metric))
            if left_value is None or right_value is None:
                raise ValueError("Comparison metric is absent from a paired receipt")
            differences.append(float(right_value) - float(left_value))
        result = {"status": "SUCCEEDED", "operation": operation, "metric": metric,
                  "pair_count": len(differences), "paired_differences_right_minus_left": differences,
                  "mean_paired_difference": float(sum(differences) / len(differences))}
    output = binding.get("output_path")
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(_json(result) + "\n", encoding="utf-8")
    return result


def inference_worker(spec_path: Path) -> dict[str, Any]:
    """Load existing weights and score the explicitly supplied researcher split."""
    import numpy as np
    import torch
    from .training_runtime import build_model, evaluate, prepare_data
    spec = _read_json(spec_path)
    config = _read_json(Path(spec["config_path"]))
    checkpoint = Path(spec["checkpoint_path"])
    original_config_path = checkpoint.parent / "effective_config.json"
    original_config = _read_json(original_config_path)
    allowed = set(spec.get("allowed_config_changes", []))
    changes = {key for key in set(config) | set(original_config) if config.get(key) != original_config.get(key)}
    if changes - allowed:
        raise ValueError(f"Inference changes training/recipe config fields outside the declared evaluation override list: {sorted(changes - allowed)}")
    state = torch.load(checkpoint, map_location=spec.get("device", "cuda"), weights_only=False)
    if state.get("effective_config") != original_config:
        raise ValueError("Checkpoint state and original effective config do not agree")
    data = prepare_data(str(config["family"]), Path(spec["campaign"]) / "data/researcher", config)
    device = torch.device(spec.get("device", "cuda"))
    model = build_model(str(config["family"]), config).to(device)
    model.load_state_dict(state["model"])
    metrics, predictions = evaluate(str(config["family"]), model, data, config, device)
    output = Path(spec["output_directory"])
    output.mkdir(parents=True, exist_ok=True)
    from .training_runtime import atomic_npz, atomic_json
    atomic_npz(output / "validation_predictions.npz", predictions=predictions,
               sample_ids=np.asarray(data.metadata["validation_sample_ids"], dtype=str))
    receipt = {"status": "SUCCEEDED", "work_id": spec["work_id"], "kind": "inference",
        "source_checkpoint": str(checkpoint.resolve()), "source_fit_config": str(original_config_path.resolve()),
        "effective_config": config, "validation_metrics": metrics,
        "validation_sample_ids": list(data.metadata["validation_sample_ids"]),
        "hidden_test_opened": False, "optimizer_updates": 0,
        "formal_score_eligible": bool(config.get("formal_score_eligible", config.get("phase") == "formal"))}
    atomic_json(output / "inference_receipt.json", receipt)
    independent = numpy_score_predictions(str(config["family"]), predictions, data.validation_y.numpy(), data.metadata)
    for key, value in independent.items():
        if not math.isclose(float(metrics[key]), value, rel_tol=1e-6, abs_tol=1e-8):
            raise ValueError(f"Independent inference metric check disagrees for {key}")
    return receipt


def _atomic_json_file(path: Path, value: Mapping[str, Any]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _json(dict(value)) + "\n"
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _assemble_model_messages(client: Any, messages: Sequence[Mapping[str, Any]], stage: str):
    from research.agents.policy import ensure_research_messages
    delivered = copy.deepcopy(list(messages))
    return ensure_research_messages(delivered, client.config, stage)


def _fit_attempt_record(ledger: Path, campaign_id: str, work_id: str) -> dict[str, Any] | None:
    if not work_id:
        return None
    with sqlite3.connect(Path(ledger)) as db:
        try:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id=? ORDER BY attempt_number DESC LIMIT 1",
                             (campaign_id, work_id)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
    return dict(row) if row else None


def _measured_gpu_seconds(ledger: Path, work_id: str, campaign_id: str | None = None,
                          ledger_id: str | None = None) -> float | None:
    with sqlite3.connect(Path(ledger)) as db:
        try:
            ids = [work_id]
            if campaign_id is not None:
                ids.extend(row[0] for row in db.execute("SELECT supervisor_work_id FROM forest_study_fit_attempts WHERE campaign_id=? AND logical_work_id=?",
                    (campaign_id, work_id)).fetchall())
            placeholders = ",".join("?" for _ in set(ids))
            condition = "ledger_id=? AND " if ledger_id is not None else ""
            parameters = ([ledger_id] if ledger_id is not None else []) + list(dict.fromkeys(ids))
            row = db.execute(f"SELECT SUM(quantity) FROM forest_compute_usage WHERE {condition}work_id IN ({placeholders}) AND resource_type='gpu_allocated_ms'",
                             parameters).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
    return None if not row or row[0] is None else float(row[0]) / 1000.0


def _supervisor_has_work(ledger: Path, work_id: str) -> bool:
    """Check accepted original supervisor work before asking status()."""
    with sqlite3.connect(Path(ledger)) as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='forest_compute_work'").fetchone()
        if not table:
            return False
        row = db.execute("SELECT 1 FROM forest_compute_work WHERE work_id=?", (work_id,)).fetchone()
    return row is not None


def _known_source_preflight_failure(supervisor: Any, work_id: str) -> bool:
    """Confirm a failed child never passed its source preflight into execution."""
    signature = "command source/configuration changed before execution"
    root = getattr(supervisor, "directory", None)
    if root is None:
        return False
    process_dir = Path(root) / str(work_id) / "processes" / str(work_id)
    identity_path = process_dir / "identity.json"
    completion_path = process_dir / "completion.json"
    stderr_path = process_dir / "stderr.log"
    if identity_path.exists() or not completion_path.is_file() or not stderr_path.is_file():
        return False
    try:
        completion = _read_json(completion_path)
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, UnicodeError):
        return False
    try:
        failed_exit = int(completion.get("exit_code")) != 0
    except (TypeError, ValueError):
        return False
    return completion.get("status") == "failed" and failed_exit and signature in stderr.lower()


def calibration_update_candidates(actual_rows: Sequence[Mapping[str, Any]], allowed_by_family: Mapping[str, Sequence[int]],
                                  required_roles: Mapping[str, Sequence[str]], primary: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """Evaluate only fully observed role×update cells; no interpolation/extrapolation."""
    output = {}
    for family, allowed_values in allowed_by_family.items():
        allowed = sorted({int(value) for value in allowed_values})
        roles = [str(role) for role in required_roles.get(family, ())]
        metric_spec = primary.get(family, {})
        metric, direction = str(metric_spec.get("metric", "")), str(metric_spec.get("direction", ""))
        if not roles or direction not in {"minimize", "maximize"} or not metric:
            output[str(family)] = {"status": "invalid_calibration_contract", "allowed_updates": allowed}
            continue
        family_rows = [dict(row) for row in actual_rows if row.get("family") == family
                       and row.get("state") == "completed" and row.get("role") in roles
                       and row.get("updates") in allowed and isinstance(row.get("metrics"), Mapping)
                       and metric in row["metrics"] and row.get("gpu_seconds") is not None]
        grouped = {updates: {role: [row for row in family_rows if int(row["updates"]) == updates and row["role"] == role]
                             for role in roles} for updates in allowed}
        observed = {}
        for updates, role_rows in grouped.items():
            if any(len(role_rows[role]) != 1 for role in roles):
                continue
            observed[str(updates)] = {role: {"work_id": role_rows[role][0]["work_id"],
                "metric": float(role_rows[role][0]["metrics"][metric]),
                "gpu_seconds": float(role_rows[role][0]["gpu_seconds"])} for role in roles}
        role_best = {}
        for role in roles:
            values = [float(row["metrics"][metric]) for row in family_rows if row.get("role") == role]
            if values:
                role_best[role] = (min(values) if direction == "minimize" else max(values))
        threshold_candidates = []
        hard_candidates = []
        within_by_update = {}
        for updates_s, role_values in observed.items():
            updates = int(updates_s)
            each_within = {}
            for role in roles:
                value, best = role_values[role]["metric"], role_best.get(role)
                if best is None:
                    each_within[role] = False
                elif direction == "minimize":
                    each_within[role] = value <= best + 0.01 * max(abs(best), 1e-12)
                else:
                    each_within[role] = value >= best - 0.01 * max(abs(best), 1e-12)
            within_by_update[updates_s] = each_within
            seconds = [role_values[role]["gpu_seconds"] for role in roles]
            if all(each_within.values()) and all(value <= 75.0 for value in seconds):
                threshold_candidates.append(updates)
            if all(value <= 90.0 for value in seconds):
                hard_candidates.append((sum(seconds) / len(seconds), updates))
        if threshold_candidates:
            expected = min(threshold_candidates)
            criterion = "smallest_common_update_count_within_one_percent_and_each_fit_le_75s"
        elif hard_candidates:
            fastest = min(seconds for seconds, _ in hard_candidates)
            expected = min(updates for seconds, updates in hard_candidates if math.isclose(seconds, fastest, rel_tol=1e-9, abs_tol=1e-9))
            criterion = "fastest_observed_common_update_count_each_fit_le_90s_with_validation_gap"
        else:
            expected = None
            criterion = "no_observed_common_update_count_with_all_required_roles_within_90s"
        output[str(family)] = {"status": "ready" if expected is not None else "incomplete",
            "allowed_updates": allowed, "required_roles": roles, "primary_metric": metric,
            "direction": direction, "role_best_observed": role_best, "observed_complete_update_counts": observed,
            "within_one_percent_by_count": within_by_update, "expected_update_count": expected,
            "selection_criterion": criterion}
    return output


def _calibration_decision_messages(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                                   candidate_info: Mapping[str, Any], required_roles: Mapping[str, Sequence[str]],
                                   current_counts: Mapping[str, Any]) -> list[dict[str, str]]:
    rule = payload.get("decision_rule", {})
    schema = {"family_updates": {family: info.get("expected_update_count")
                                  for family, info in candidate_info.items()},
              "readiness": "continue|stop|incomplete", "rationale": "brief evidence-based reason"}
    content = {
        "task": "Select the shared update count for each family using only completed calibration fits and their independently recomputed validation metrics.",
        "selection_rule": rule.get("family_update_count", ""),
        "readiness_rule": rule.get("continue_to_formal", ""),
        "required_roles": required_roles,
        "currently planned counts (not evidence)": current_counts,
        "actual fit states and measurements": list(rows),
        "per-family fully observed candidates computed from the actual measurements": candidate_info,
        "response contract": schema,
        "constraints": ["Do not infer or extrapolate a missing update-count result.",
            "A candidate count is supported only when all required roles were actually completed at that count and each measured GPU lease stayed within its cap.",
            "If a family has no valid common count, mark readiness incomplete or stop and explain the missing observed evidence.",
            "Use no DEV outcomes as calibration evidence."]}
    return [{"role": "system", "content": "You are deciding a preregistered calibration rule from the supplied observed researcher validation records. Distinguish measured results from missing cells."},
            {"role": "user", "content": _json(content)}]


def verify_calibration_choice(choice: Mapping[str, Any], candidates: Mapping[str, Any],
                              allowed_by_family: Mapping[str, Sequence[int]],
                              required_roles: Mapping[str, Sequence[str]]) -> tuple[bool, dict[str, Any]]:
    if not isinstance(choice, Mapping) or not isinstance(choice.get("family_updates"), Mapping):
        return False, {"reason": "family_updates_missing"}
    expected = {family: info.get("expected_update_count") for family, info in candidates.items()}
    selected = choice["family_updates"]
    reasons = []
    for family, options in allowed_by_family.items():
        value = selected.get(family)
        if candidates.get(family, {}).get("expected_update_count") is None:
            if value is not None:
                reasons.append(f"{family}: no fully observed feasible count exists")
        elif isinstance(value, bool) or value not in {int(x) for x in options}:
            reasons.append(f"{family}: selected count is outside registered choices")
        elif int(value) != int(expected[family]):
            reasons.append(f"{family}: selected count differs from the rule applied to observed results")
    readiness = "continue" if all(value is not None for value in expected.values()) else "incomplete"
    if choice.get("readiness") != readiness:
        reasons.append(f"readiness must be {readiness} from the complete-role evidence")
    return not reasons, {"expected_family_updates": expected, "selected_family_updates": dict(selected),
                         "required_roles": required_roles, "expected_readiness": readiness,
                         "reasons": reasons}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Register and execute manifest work")
    run.add_argument("--campaign", type=Path, required=True)
    run.add_argument("--ledger", type=Path, required=True)
    run.add_argument("--ledger-id", required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--gpu-device", action="append", default=[])
    run.add_argument("--provider-id")
    run.add_argument("--model")
    run.add_argument("--reasoning-effort")
    run.add_argument("--owner", default=f"study-runtime-{os.getpid()}")
    run.add_argument("--daemon", action="store_true", help="Keep reloading dynamic manifest entries until interrupted")
    run.add_argument("--poll-seconds", type=float, default=5.0)
    worker = subparsers.add_parser("worker-infer", help=argparse.SUPPRESS)
    worker.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "worker-infer":
        result = inference_worker(args.spec)
        print(_json(result), flush=True)
        return 0
    runtime = StudyRuntime(campaign=args.campaign, ledger=args.ledger, ledger_id=args.ledger_id, manifest=args.manifest)
    provider_spec = runtime.manifest.get("model_provider") or {}
    provider_id = args.provider_id or provider_spec.get("id")
    requested_model = args.model or provider_spec.get("model", "gpt-6-luna")
    reasoning_effort = args.reasoning_effort or (provider_spec.get("config") or {}).get("reasoning_effort", "medium")
    if provider_id and provider_spec.get("id") and provider_id != provider_spec["id"]:
        raise ValueError("CLI provider ID differs from the manifest's saved provider reference")
    if provider_spec and (requested_model != provider_spec.get("model") or
                          reasoning_effort != (provider_spec.get("config") or {}).get("reasoning_effort", "medium")):
        raise ValueError("CLI model/reasoning differs from the campaign's manifest provider settings")
    # Resolve each item's saved provider in run_claimed. A campaign may contain
    # distinct authorized providers for primary and replay cells; a shared CLI
    # client would silently route those cells through the wrong account/model.
    client = None
    print(_json({"registered": runtime.registered, "counts": runtime.ledger.counts(runtime.campaign_id)}), flush=True)
    while True:
        result = runtime.run_one(owner=args.owner, gpu_devices=args.gpu_device, client=client)
        if result is not None:
            print(_json(result), flush=True)
            continue
        counts = runtime.ledger.counts(runtime.campaign_id)
        print(_json({"idle": True, "counts": counts}), flush=True)
        if not args.daemon:
            return 0
        time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
