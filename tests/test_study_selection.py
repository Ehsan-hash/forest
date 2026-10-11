"""Outcome-independent matrix reduction and real SQLite queue withdrawal."""
import pytest

from research.experiments.study_runtime import StudyLedger
from research.experiments.study_selection import SelectionRule, apply_selection, build_selection, select_contexts


FAMILIES = ("darcy", "bloodmnist", "etth1")
REPLAY_ARMS = ("FULL", "FLAT", "DAG_ID", "ID_ONLY", "RERUN_ALL", "FULL_NO_GATE")
MAIN_ARMS = ("FULL", "FLAT", "DAG_ID")
SECOND_CASES = {"D01": "C0", "D02": "C3", "D03": "C0", "D04": "C1",
                "M01": "C1", "M02": "C4", "M03": "C3", "M04": "C2",
                "T01": "C2", "T02": "C5", "T03": "C4", "T04": "C5"}
TASK_CASES = {"01": ("C0", "C1", "C2"), "02": ("C3", "C4", "C5"),
              "03": ("C0", "C3", "C4"), "04": ("C1", "C2", "C5")}


def matrix():
    tasks = [{"task_id": f"{prefix}{index:02}", "family_id": family}
             for prefix, family in zip(("D", "M", "T"), FAMILIES) for index in range(1, 5)]
    cells = []
    for task in tasks:
        task_id, family = task["task_id"], task["family_id"]
        for repeat in range(2):
            for change in TASK_CASES[task_id[-2:]]:
                for arm in REPLAY_ARMS:
                    cells.append({"category": "REPLAY", "task_id": task_id, "family": family,
                                  "controller_mutation_id": change, "repeat": repeat, "arm": arm})
            for stream in ("EVAL_UPDATE", "TRAIN_UPDATE"):
                for arm in MAIN_ARMS:
                    cells.append({"category": "LIVE_MAIN", "task_id": task_id, "family": family,
                                  "stream": stream, "repeat": repeat, "arm": arm})
            if task_id[-2:] in {"01", "03"}:
                for arm in ("FULL", "AIDE"):
                    # Real manifest derives this category's family from tasks.
                    cells.append({"category": "LIVE_END_TO_END", "task_id": task_id,
                                  "repeat": repeat, "arm": arm})
            for arm in ("FULL", "DAG_ID"):
                cells.append({"category": "SECOND_MODEL_REPLAY", "task_id": task_id, "family": family,
                              "controller_mutation_id": SECOND_CASES[task_id], "repeat": repeat, "arm": arm})
    for index, cell in enumerate(cells):
        cell.update(episode_id=f"work-{index}", matrix_cell_id=f"logical-{index}", status="pending")
    return {"cells": cells, "tasks": tasks}


def plan(*, end_to_end=2, supplement=4):
    common = {"group_field": "family", "group_order": FAMILIES,
              "unique_axes": (("task_id",),), "balance_fields": ("family",)}
    return {
        "selection_revision_id": "smaller-matrix-r1",
        "expected_counts": {"total": 72 + 24 + 2 * end_to_end + 2 * supplement,
                            "core": 72 + 24 + 2 * end_to_end, "supplement": 2 * supplement},
        "rules": [
            {**common, "category": "REPLAY", "context_count": 12, "arms": REPLAY_ARMS,
             "context_fields": ("task_id", "controller_mutation_id", "repeat"),
             "ordinal_fields": ("task_id", "controller_mutation_id", "repeat"),
             "reference_category": "SECOND_MODEL_REPLAY", "reference_fields": ("task_id", "controller_mutation_id"),
             "balance_fields": ("family", "controller_mutation_id")},
            {**common, "category": "LIVE_MAIN", "context_count": 8, "arms": MAIN_ARMS,
             "context_fields": ("task_id", "stream", "repeat"),
             "ordinal_fields": ("task_id", "stream", "repeat"),
             "alternating_field": "stream", "alternating_order": ("EVAL_UPDATE", "TRAIN_UPDATE"),
             "balance_fields": ("family", "stream")},
            {**common, "category": "LIVE_END_TO_END", "context_count": end_to_end, "arms": ("FULL", "AIDE"),
             "context_fields": ("task_id", "repeat"), "ordinal_fields": ("task_id", "repeat"),
             "group_offsets": {"bloodmnist": 1}},
            {**common, "category": "SECOND_MODEL_REPLAY", "context_count": supplement, "arms": ("FULL", "DAG_ID"),
             "context_fields": ("task_id", "controller_mutation_id", "repeat"),
             "ordinal_fields": ("task_id", "controller_mutation_id", "repeat"),
             "unique_axes": (("task_id",), ("controller_mutation_id",)) if supplement <= 6 else (("task_id",),),
             "partition": "supplement",
             "balance_fields": ("family", "controller_mutation_id")},
        ],
    }


def test_original_matrix_reduces_to_108_paired_units():
    manifest = matrix()
    result = build_selection(manifest, plan())
    assert result["original_unit_count"] == 648
    assert result["selected_unit_count"] == 108
    assert result["partition_counts"] == {"core": 100, "supplement": 8}
    by_category = {row["category"]: row for row in result["categories"]}
    assert by_category["REPLAY"]["arm_counts"] == dict.fromkeys(REPLAY_ARMS, 12)
    assert by_category["REPLAY"]["balance"] == {"family": dict.fromkeys(FAMILIES, 4),
                                                "controller_mutation_id": dict.fromkeys(("C0", "C1", "C2", "C3", "C4", "C5"), 2)}
    assert by_category["LIVE_MAIN"]["arm_counts"] == dict.fromkeys(MAIN_ARMS, 8)
    assert by_category["LIVE_MAIN"]["balance"]["stream"] == {"EVAL_UPDATE": 4, "TRAIN_UPDATE": 4}
    assert [(row["task_id"], row["stream"]) for row in by_category["LIVE_MAIN"]["contexts"]] == [
        ("D01", "EVAL_UPDATE"), ("M01", "TRAIN_UPDATE"), ("T01", "EVAL_UPDATE"), ("D02", "TRAIN_UPDATE"),
        ("M02", "EVAL_UPDATE"), ("T02", "TRAIN_UPDATE"), ("D03", "EVAL_UPDATE"), ("M03", "TRAIN_UPDATE")]
    assert [row["task_id"] for row in by_category["LIVE_END_TO_END"]["contexts"]] == ["D01", "M03"]
    assert [row["task_id"] for row in by_category["SECOND_MODEL_REPLAY"]["contexts"]] == ["D01", "M01", "T01", "D02"]
    assert all(cell["repeat"] == 0 for cell in result["selected_cells"])
    assert len(result["excluded_matrix_cell_ids"]) == 540
    assert len(set(result["selected_matrix_cell_ids"]) | set(result["excluded_matrix_cell_ids"])) == 648
    assert manifest == matrix()


@pytest.mark.parametrize("end_to_end,supplement,total,core", [(3, 4, 110, 102), (3, 3, 108, 102)])
def test_expanded_end_to_end_variants_are_declarative(end_to_end, supplement, total, core):
    result = build_selection(matrix(), plan(end_to_end=end_to_end, supplement=supplement))
    assert result["selected_unit_count"] == total
    assert result["partition_counts"]["core"] == core
    e2e = next(row for row in result["categories"] if row["category"] == "LIVE_END_TO_END")
    assert [row["task_id"] for row in e2e["contexts"]] == ["D01", "M03", "T01"]
    assert e2e["balance"]["family"] == dict.fromkeys(FAMILIES, 1)


def test_confirmed_scale_keeps_108_core_and_24_supplement_units():
    result = build_selection(matrix(), plan(end_to_end=6, supplement=12))
    assert result["original_unit_count"] == 648
    assert result["selected_unit_count"] == 132
    assert result["partition_counts"] == {"core": 108, "supplement": 24}
    categories = {row["category"]: row for row in result["categories"]}
    assert categories["LIVE_END_TO_END"]["arm_counts"] == {"FULL": 6, "AIDE": 6}
    assert categories["LIVE_END_TO_END"]["balance"]["family"] == dict.fromkeys(FAMILIES, 2)
    assert {row["task_id"] for row in categories["LIVE_END_TO_END"]["contexts"]} == {
        "D01", "D03", "M01", "M03", "T01", "T03"}
    assert categories["SECOND_MODEL_REPLAY"]["arm_counts"] == {"FULL": 12, "DAG_ID": 12}
    assert categories["SECOND_MODEL_REPLAY"]["balance"] == {
        "family": dict.fromkeys(FAMILIES, 4),
        "controller_mutation_id": dict.fromkeys(("C0", "C1", "C2", "C3", "C4", "C5"), 2)}
    assert len(result["excluded_work_ids"]) == 516


def test_order_results_and_usage_do_not_influence_membership():
    original = build_selection(matrix(), plan())
    changed = matrix()
    changed["cells"].reverse()
    changed["tasks"].reverse()
    for index, cell in enumerate(changed["cells"]):
        cell.update(status="completed" if index % 2 else "failed", result={"score": -index}, tokens=index, gpu_seconds=index)
    actual = build_selection(changed, plan())
    for field in ("selected_matrix_cell_ids", "excluded_matrix_cell_ids", "selected_work_ids", "categories"):
        assert actual[field] == original[field]


def test_incomplete_or_disagreeing_arm_sets_are_rejected():
    manifest = matrix()
    manifest["cells"].pop(0)
    with pytest.raises(ValueError, match="Incomplete comparison"):
        build_selection(manifest, plan())
    manifest = matrix()
    manifest["cells"][0]["family"] = "bloodmnist"
    with pytest.raises(ValueError, match="disagree"):
        build_selection(manifest, plan())


def test_infeasible_or_mismatched_request_fails_without_relaxing_rule():
    public = matrix()["cells"]
    rule = plan()["rules"][0]
    rule["context_count"] = 13
    with pytest.raises(ValueError, match="Only 12"):
        select_contexts(public, SelectionRule.from_dict(rule))
    broken = plan()
    broken["expected_counts"]["total"] += 1
    with pytest.raises(ValueError, match="Declared size"):
        build_selection(matrix(), broken)


def test_duplicate_identities_and_invalid_rule_fields_fail():
    manifest = matrix()
    manifest["cells"][1]["matrix_cell_id"] = manifest["cells"][0]["matrix_cell_id"]
    with pytest.raises(ValueError, match="logical identity"):
        build_selection(manifest, plan())
    broken = plan()
    broken["rules"][0]["random_seed"] = 5
    with pytest.raises(ValueError, match="Unknown selection rule"):
        build_selection(matrix(), broken)


def registered_ledger(tmp_path):
    ledger = StudyLedger(tmp_path / "study.sqlite", "original-ledger")
    items = [{"work_id": name, "kind": "replay", "stage": "formal", "scientific_config": {"seed": 1001}}
             for name in ("keep", "omit", "old-result")]
    ledger.register({"campaign_id": "campaign", "manifest_id": "original-manifest", "manifest_version": 1,
                     "work_items": items})
    ledger.set_state("campaign", "old-result", "completed", result={"valid": True, "artifact": "original.json"})
    return ledger


def small_report():
    return {"selection_revision_id": "scope-r1", "selected_work_ids": ["keep"],
            "excluded_work_ids": ["omit", "old-result"], "rule": {"basis": "ordinal"}}


def test_actual_sqlite_withdrawal_preserves_history_and_prevents_claim(tmp_path):
    ledger = registered_ledger(tmp_path)
    with ledger.connect() as db:
        before = {row["work_id"]: dict(row) for row in db.execute("SELECT * FROM forest_study_work")}
        db.execute("CREATE TABLE original_usage (quantity INTEGER)")
        db.execute("INSERT INTO original_usage VALUES(12345)")
    application = apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    assert application["withdrawn_work_ids"] == ["omit"]
    assert application["preserved_terminal_work_ids"] == ["old-result"]
    with ledger.connect() as db:
        after = {row["work_id"]: dict(row) for row in db.execute("SELECT * FROM forest_study_work")}
        assert db.execute("SELECT quantity FROM original_usage").fetchone()[0] == 12345
        assert db.execute("SELECT count(*) FROM forest_study_selections").fetchone()[0] == 1
    assert after["old-result"] == before["old-result"]
    assert after["keep"] == before["keep"]
    assert after["omit"]["payload_json"] == before["omit"]["payload_json"]
    assert after["omit"]["state"] == "not_run"
    assert "excluded_by_selection_revision=scope-r1" in after["omit"]["error"]
    assert ledger.claim("campaign", "worker", work_ids=["omit"]) is None
    assert ledger.claim("campaign", "worker", work_ids=["keep"])["work_id"] == "keep"
    assert apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())["already_applied"]
    with ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='study_selection_applied'").fetchone()[0] == 1


@pytest.mark.parametrize("state", ["claimed", "running", "awaiting_reconciliation", "retryable", "blocked"])
def test_active_exclusion_rolls_back_entire_revision(tmp_path, state):
    ledger = registered_ledger(tmp_path)
    ledger.set_state("campaign", "omit", state)
    with pytest.raises(ValueError, match="reconciliation"):
        apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    with ledger.connect() as db:
        assert db.execute("SELECT state FROM forest_study_work WHERE work_id='omit'").fetchone()[0] == state
        assert db.execute("SELECT count(*) FROM forest_study_events WHERE event_type='study_selection_applied'").fetchone()[0] == 0
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='forest_study_selections'").fetchone()
        assert not exists or db.execute("SELECT count(*) FROM forest_study_selections").fetchone()[0] == 0


def test_touched_queued_work_and_changed_revision_are_rejected(tmp_path):
    ledger = registered_ledger(tmp_path)
    ledger.event("campaign", "work_claimed", {"owner": "previous-worker"}, "omit")
    with pytest.raises(ValueError, match="reconciliation"):
        apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    other = registered_ledger(tmp_path / "other")
    apply_selection(other, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    changed = small_report()
    changed["rule"]["basis"] = "results"
    with pytest.raises(ValueError, match="reused"):
        apply_selection(other, campaign_id="campaign", external_ledger_id="original-ledger", report=changed)


def test_original_ledger_reference_cannot_be_replaced(tmp_path):
    ledger = registered_ledger(tmp_path)
    with pytest.raises(ValueError, match="original external ledger"):
        apply_selection(ledger, campaign_id="campaign", external_ledger_id="new-ledger", report=small_report())


def test_new_revision_can_restore_previously_unexecuted_exclusion(tmp_path):
    ledger = registered_ledger(tmp_path)
    apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    next_report = {"selection_revision_id": "scope-r2", "selected_work_ids": ["keep", "omit"], "excluded_work_ids": ["old-result"]}
    actual = apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=next_report)
    assert actual["restored_work_ids"] == ["omit"]
    assert ledger.claim("campaign", "worker", work_ids=["omit"])["work_id"] == "omit"
    with ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM forest_study_selections").fetchone()[0] == 2


def test_planning_not_run_result_is_retained_during_withdrawal_and_restore(tmp_path):
    ledger = registered_ledger(tmp_path)
    planning_result = {"status": "NOT_RUN", "physical_model_requests": 0, "gpu_actual_seconds": 0,
                       "reason": "Required distinct model is not registered", "distinct_model_evidence": False}
    ledger.set_state("campaign", "omit", "not_run", result=planning_result)
    ledger.event("campaign", "second_model_authorized", {"provider_id": "authorized-existing-provider"}, "omit")
    ledger.set_state("campaign", "omit", "queued")
    with ledger.connect() as db:
        original = db.execute("SELECT result_json FROM forest_study_work WHERE work_id='omit'").fetchone()[0]
    applied = apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    assert applied["withdrawn_work_ids"] == ["omit"]
    assert applied["preserved_planning_result_work_ids"] == ["omit"]
    with ledger.connect() as db:
        row = db.execute("SELECT state,result_json,error FROM forest_study_work WHERE work_id='omit'").fetchone()
        assert row["state"] == "not_run"
        assert row["result_json"] == original
        assert "excluded_by_selection_revision=scope-r1" in row["error"]
        assert db.execute("SELECT count(*) FROM forest_study_events WHERE work_id='omit' AND event_type='second_model_authorized'").fetchone()[0] == 1
    assert ledger.claim("campaign", "worker", work_ids=["omit"]) is None
    restored = apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger",
        report={"selection_revision_id": "scope-r2", "selected_work_ids": ["keep", "omit"], "excluded_work_ids": ["old-result"]})
    assert restored["restored_work_ids"] == ["omit"]
    with ledger.connect() as db:
        assert db.execute("SELECT result_json FROM forest_study_work WHERE work_id='omit'").fetchone()[0] == original


@pytest.mark.parametrize("result", [
    {"status": "NOT_RUN", "physical_model_requests": 1, "gpu_actual_seconds": 0},
    {"status": "NOT_RUN", "physical_model_requests": 0, "gpu_actual_seconds": 1},
    {"status": "NOT_RUN", "gpu_actual_seconds": 0},
    {"status": "NOT_RUN", "physical_model_requests": 0},
    {"status": "NOT_RUN", "physical_model_requests": False, "gpu_actual_seconds": 0},
    {"status": "completed", "physical_model_requests": 0, "gpu_actual_seconds": 0},
    {"valid": True, "artifact": "training-output.json"},
])
def test_ambiguous_or_executed_receipts_cannot_be_withdrawn(tmp_path, result):
    ledger = registered_ledger(tmp_path)
    ledger.set_state("campaign", "omit", "queued", result=result)
    with pytest.raises(ValueError, match="reconciliation"):
        apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
    with ledger.connect() as db:
        assert db.execute("SELECT state FROM forest_study_work WHERE work_id='omit'").fetchone()[0] == "queued"


def test_zero_planning_receipt_does_not_override_actual_dispatch_evidence(tmp_path):
    ledger = registered_ledger(tmp_path)
    ledger.set_state("campaign", "omit", "queued",
        result={"status": "NOT_RUN", "physical_model_requests": 0, "gpu_actual_seconds": 0})
    ledger.event("campaign", "work_claimed", {"owner": "actual-worker"}, "omit")
    with pytest.raises(ValueError, match="reconciliation"):
        apply_selection(ledger, campaign_id="campaign", external_ledger_id="original-ledger", report=small_report())
