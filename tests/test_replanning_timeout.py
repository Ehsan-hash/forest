"""Timeout-only edits must not satisfy the required scientific replan gate."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _graph(config=None, *, instructions="Run the baseline computation.", inputs=None):
    return {
        "project_id": "project",
        "goal": "Measure the baseline computation",
        "revision": 4,
        "nodes": [{
            "id": "failed",
            "project_id": "project",
            "branch_id": "main",
            "type": "experiment",
            "title": "Baseline computation",
            "instructions": instructions,
            "revision": 2,
            "config": config or {"kind": "command", "command": ["python", "baseline.py"]},
            "inputs": inputs or [],
            "position": {"x": 0, "y": 0},
            "archived": False,
        }],
        "edges": [],
        "branches": [{"id": "main", "name": "Main", "status": "active", "workspace": ".", "is_main": True}],
    }


def _plan(*, stop="failed", evidence="run-1", commands=None):
    return {
        "action": "continue",
        "rationale": "The repeated route failure requires a changed executable method.",
        "evidence_run_ids": [evidence],
        "replanning": {
            "reason": "The current route repeatedly failed.",
            "new_direction": "Use a changed executable baseline method.",
            "goal_alignment": "Continue measuring the current scientific goal.",
            "stop_node_ids": [stop],
            "evidence_run_ids": [evidence],
        },
        "commands": commands or [],
    }


def _validate(before, after, plan=None, control=None):
    from research.planning.loop import validate_replanning

    return validate_replanning(
        plan or _plan(),
        before,
        after,
        [{"id": "run-1", "node_id": "failed", "status": "failed"}],
        control or {"replan_required": True, "route_replan_node_ids": ["failed"]},
        {"signals": []},
        before["goal"],
    )


@pytest.mark.parametrize(
    ("old_timeout", "new_timeout"),
    [(None, 120), (10, 120), (10, None)],
    ids=["add", "change", "remove"],
)
def test_timeout_only_existing_node_edit_is_not_scientific_replanning(old_timeout, new_timeout):
    config = {"kind": "command", "command": ["python", "baseline.py"]}
    if old_timeout is not None:
        config["timeout"] = old_timeout
    before = _graph(config=deepcopy(config))
    revised = deepcopy(config)
    if new_timeout is None:
        revised.pop("timeout", None)
    else:
        revised["timeout"] = new_timeout
    after = _graph(config=revised)

    with pytest.raises(ValueError, match="materially changed"):
        _validate(before, after)


def test_timeout_only_clone_is_not_a_new_scientific_route():
    before = _graph(config={"kind": "command", "command": ["python", "baseline.py"], "timeout": 10})
    after = deepcopy(before)
    clone = deepcopy(before["nodes"][0])
    clone.update(id="clone", title="Administrative timeout copy", revision=0)
    clone["config"]["timeout"] = 120
    after["nodes"].append(clone)

    with pytest.raises(ValueError, match="materially changed"):
        _validate(before, after)


@pytest.mark.parametrize("change", ["command", "inputs"])
def test_real_method_or_input_edit_is_admissible(change):
    before = _graph(config={"kind": "command", "command": ["python", "baseline.py"], "timeout": 10})
    after = deepcopy(before)
    if change == "command":
        after["nodes"][0]["config"]["command"] = ["python", "revised.py"]
    else:
        after["nodes"][0]["inputs"] = [{
            "node_id": "source",
            "path": "metrics.json",
            "destination": "input.json",
        }]

    report = _validate(before, after)
    assert report["changed_scientific_node_ids"] == ["failed"]


def test_agent_instruction_edit_is_admissible():
    before = _graph(config={"kind": "agent", "role": "Engineer"})
    after = deepcopy(before)
    after["nodes"][0]["instructions"] = "Implement the revised baseline and record measured output."

    report = _validate(before, after)
    assert report["changed_scientific_node_ids"] == ["failed"]


def test_timeout_remains_editable_when_replanning_gate_is_off():
    before = _graph(config={"kind": "command", "command": ["python", "baseline.py"], "timeout": 10})
    after = deepcopy(before)
    after["nodes"][0]["config"]["timeout"] = 120

    assert _validate(before, after, control={"replan_required": False}) is None


def test_scientific_timeout_normalization_does_not_mutate_inputs_or_shared_identity():
    from research.agents.research_path import task_signature

    before = _graph(
        config={"kind": "command", "command": ["python", "baseline.py"], "timeout": 10},
        inputs=[{"node_id": "source", "path": "metrics.json", "destination": "input.json"}],
    )
    after = deepcopy(before)
    after["nodes"][0]["config"]["timeout"] = 120
    before_snapshot = deepcopy(before)
    after_snapshot = deepcopy(after)

    with pytest.raises(ValueError, match="materially changed"):
        _validate(before, after)

    assert before == before_snapshot and after == after_snapshot
    # The shared identity remains operationally sensitive to timeout. Only the
    # required replanning comparison is allowed to ignore that field.
    assert task_signature(before["nodes"][0]) != task_signature(after["nodes"][0])


def test_persisted_apply_plan_rejects_timeout_only_then_admits_method_edit(tmp_path):
    """Exercise rollback and adoption against a disposable persisted project."""
    scenario = r'''
import copy
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
scenario_root = Path(sys.argv[2]).resolve()
database = Path(os.environ["FOREST_DATABASE_URL"].removeprefix("sqlite:///").strip()).resolve()
data = Path(os.environ["FOREST_DATA_DIR"]).resolve()
tmp = Path(os.environ["TMPDIR"]).resolve()
mpl = Path(os.environ["MPLCONFIGDIR"]).resolve()
assert Path(os.environ["PYTHONPATH"]).resolve() == root
assert database.parent == scenario_root
assert tmp.parent == scenario_root and data.parent == scenario_root and mpl.parent == scenario_root
assert len({str(database), str(data), str(tmp), str(mpl)}) == 4

from fastapi.testclient import TestClient
from services.api.common import graph_from_db
from services.api.db import Project, Session, TaskRun, migrate, uid
from services.api.main import app
from research.planning.loop import apply_plan

migrate()
client = TestClient(app, headers={"Authorization": "Bearer forest379-synthetic-owner"})
response = client.post("/api/projects", json={
    "name": "Timeout replan persistence", "goal": "Measure the baseline computation",
    "mode": "manual", "config": {"controller": {"status": "running"}},
})
assert response.status_code == 200, response.text
project = response.json()
pid = project["id"]
graph = client.get("/api/projects/" + pid + "/graph").json()
branch = graph["branches"][0]
node_id = "failed"
node_response = client.post("/api/projects/" + pid + "/graph/commands", json={
    "request_id": uid(), "expected_revision": graph["revision"],
    "operation": "add_node", "params": {
        "id": node_id, "branch_id": branch["id"], "type": "experiment",
        "title": "Baseline computation", "instructions": "Run the baseline computation.",
        "config": {"kind": "command", "command": ["python", "baseline.py"], "timeout": 10},
    },
})
assert node_response.status_code == 200, node_response.text
with Session.begin() as session:
    persisted = session.get(Project, pid)
    persisted.config = {**persisted.config, "controller": {
        "status": "running", "phase": "PLAN", "replan_required": True,
        "route_replan_node_ids": [node_id],
    }}
    failed = TaskRun(
        project_id=pid, node_id=node_id, branch_id=branch["id"],
        request_id=uid(), kind="command", status="failed",
        config={"kind": "command", "command": ["python", "baseline.py"], "timeout": 10},
        node_revision=0, output_path="runs/failed",
        resource={"evidence_label": "FIXTURE", "synthetic": True},
    )
    session.add(failed)
    session.flush()
    evidence_id = failed.id

def proposal(config):
    return {
        "action": "continue",
        "rationale": "The repeated route failure requires a changed executable method.",
        "evidence_run_ids": [evidence_id],
        "replanning": {
            "reason": "The current route repeatedly failed.",
            "new_direction": "Use a changed executable baseline method.",
            "goal_alignment": "Continue measuring the current scientific goal.",
            "stop_node_ids": [node_id], "evidence_run_ids": [evidence_id],
        },
        "commands": [{"operation": "edit_node", "targets": [node_id], "params": {"config": config}}],
    }

with Session() as session:
    persisted = session.get(Project, pid)
    before_revision = persisted.revision
    before_config = copy.deepcopy(persisted.config)
    before_graph = graph_from_db(session, persisted)
try:
    apply_plan(pid, "timeout-only-plan", proposal({
        "kind": "command", "command": ["python", "baseline.py"], "timeout": 120,
    }), before_revision)
except ValueError as error:
    assert "materially changed" in str(error)
else:
    raise AssertionError("timeout-only replanning was admitted")
with Session() as session:
    persisted = session.get(Project, pid)
    assert persisted.revision == before_revision
    assert persisted.config == before_config
    assert graph_from_db(session, persisted) == before_graph

with Session() as session:
    current_revision = session.get(Project, pid).revision
result = apply_plan(pid, "method-change-plan", proposal({
    "kind": "command", "command": ["python", "revised.py"], "timeout": 120,
}), current_revision)
assert result["action"] == "continue" and result["applied_commands"] == 1
with Session() as session:
    persisted = session.get(Project, pid)
    current = graph_from_db(session, persisted)
    node = next(item for item in current["nodes"] if item["id"] == node_id)
    assert node["config"]["command"] == ["python", "revised.py"]
    assert persisted.config["controller"]["replan_required"] is False
    assert persisted.config["controller"]["phase"] == "EXECUTE"
client.close()
'''
    env = {
        "FOREST_DATABASE_URL": f"sqlite:///{tmp_path / 'planning.db'}",
        "FOREST_DATA_DIR": str(tmp_path / "data"),
        "FOREST_MODEL": "",
        "FOREST_OWNER_TOKEN": "forest379-synthetic-owner",
        "FOREST_WORKER_CONCURRENCY": "1",
        "TMPDIR": str(tmp_path / "tmp"),
        "MPLCONFIGDIR": str(tmp_path / "mpl"),
        "PYTHONPATH": str(ROOT),
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    Path(env["FOREST_DATA_DIR"]).mkdir()
    Path(env["TMPDIR"]).mkdir()
    Path(env["MPLCONFIGDIR"]).mkdir()
    result = subprocess.run(
        [sys.executable, "-c", scenario, str(ROOT), str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
