"""Lossless public-context representation fixtures; no model/GPU inference."""
from copy import deepcopy
import json
import math
import random

import pytest

from research.experiments.study_context import (
    FORMAT, intern_json, intern_json_with_stats, reference_segment, restore_json, segment_diagnostics,
)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def repeated_source():
    configuration = {"data": {"train_sample_ids": list(range(250)), "validation_sample_ids": list(range(300, 410))},
        "model": {"width": 32, "layers": 3, "learning_rate": 0.0001}, "seed": 1001,
        "scientific_question": "完整非正式 fixture – preserve every value"}
    return {"observations": [{"work_id": "fixture-"+str(index), "config": deepcopy(configuration),
            "metrics": {"score": index/10}, "receipt_path": "fixture/receipt-"+str(index)+".json"}
            for index in range(12)], "artifacts": [deepcopy(configuration) for _ in range(8)],
            "ordered_seeds": [1001, 1002, 1003], "no_hidden_test": True}


def test_repeated_scientific_json_roundtrips_every_value_and_original_order():
    source = repeated_source()
    original = compact(source)
    document, stats = intern_json_with_stats(source)
    assert document["encoding"] == "interned"
    assert compact(restore_json(document)) == original
    assert compact(source) == original
    assert stats["input_characters"] == len(original)
    assert stats["output_characters"] == len(compact(document))
    assert stats["saved_characters"] > 0
    assert stats["output_to_input_ratio"] < .4
    assert stats["reference_count"] == len(document["references"])
    assert all(value > 0 for value in stats["marginal_reference_savings"].values())


def test_reference_positions_are_exact_and_segment_reads_return_full_values():
    source = repeated_source()
    document = intern_json(source)
    for entry in document["references"]:
        segment = reference_segment(document, entry["id"])
        assert segment["positions"] == entry["positions"]
        for path in segment["positions"]:
            value = source
            for part in path.lstrip("/").split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
            assert compact(segment["value"]) == compact(value)
    configuration = next(entry for entry in document["references"]
        if "/observations/0/config" in entry["positions"])
    assert reference_segment(document, configuration["id"])["value"]["data"]["train_sample_ids"] == list(range(250))


def test_tags_and_source_reference_keys_never_collide():
    collision = {"format": FORMAT, "encoding": "interned", "root": ["ref", "r1"],
        "references": [{"id": "r1", "value": ["object", [["ref", "r2"]]], "positions": ["/x"]}],
        "ref": "r1", "$ref": {"array": ["array", ["ref", "r2"]]}, "object": ["object", []],
        "padding": list(range(200))}
    source = {"items": [deepcopy(collision) for _ in range(8)], "source_scalar": "r1"}
    document = intern_json(source)
    assert document["encoding"] == "interned"
    assert compact(restore_json(document)) == compact(source)


def test_key_order_numeric_types_negative_zero_and_unicode_are_preserved():
    first = {"z": 1.0, "a": 1, "b": True, "n": -0.0, "none": None, "unicode": "🌲\n中文\t", "long": list(range(80))}
    second = {"a": 1, "z": 1.0, "b": True, "n": -0.0, "none": None, "unicode": "🌲\n中文\t", "long": list(range(80))}
    source = [first, second, deepcopy(first), deepcopy(second)]
    restored = restore_json(intern_json(source))
    assert compact(restored) == compact(source)
    assert list(restored[0]) == list(first) and list(restored[1]) == list(second)
    assert type(restored[0]["a"]) is int and type(restored[0]["z"]) is float
    assert math.copysign(1, restored[0]["n"]) == -1


def test_deep_repeated_subtrees_use_exact_bottom_up_identity_and_roundtrip():
    nested = {"value": [1, 1.0, True, "x"], "empty": {}}
    for index in range(180):
        nested = {"level": index, "child": nested}
    source = {"left": deepcopy(nested), "right": deepcopy(nested)}

    document, stats = intern_json_with_stats(source)

    assert document["encoding"] == "interned"
    assert stats["saved_characters"] > 0
    assert compact(restore_json(document)) == compact(source)


def test_stable_reference_ids_follow_first_source_traversal():
    earlier = {"outer": list(range(200)), "name": "earlier"}
    later = {"outer": list(range(400, 600)), "name": "later"}
    source = {"first": [earlier]*5, "second": [later]*5}
    first = intern_json(source)
    second = intern_json(json.loads(compact(source)))
    assert compact(first) == compact(second)
    assert [entry["id"] for entry in first["references"]] == ["r"+str(index+1) for index in range(len(first["references"]))]
    assert first["references"][0]["positions"][0].startswith("/first")


def without_reference(document, identifier):
    """Actual serialized counterfactual, not an estimated size formula."""
    result = deepcopy(document)
    definition = next(entry["value"] for entry in result["references"] if entry["id"] == identifier)
    def inline(node):
        if not isinstance(node, list):
            return node
        if node == ["ref", identifier]:
            return deepcopy(definition)
        if node[0] == "array":
            return ["array", [inline(child) for child in node[1]]]
        if node[0] == "object":
            return ["object", [[key, inline(child)] for key, child in node[1]]]
        return node
    result["root"] = inline(result["root"])
    result["references"] = [{**entry, "value": inline(entry["value"])}
        for entry in result["references"] if entry["id"] != identifier]
    return result


def test_every_reference_has_positive_actual_marginal_serialized_savings():
    source = repeated_source()
    # Repeated child/parent structures exercise dependency-aware pruning.
    source["one_shared_child"] = deepcopy(source["observations"][0]["config"]["data"])
    document, stats = intern_json_with_stats(source)
    for entry in document["references"]:
        expanded = without_reference(document, entry["id"])
        delta = len(compact(expanded))-len(compact(document))
        assert delta == stats["marginal_reference_savings"][entry["id"]] > 0
        assert compact(restore_json(expanded)) == compact(source)


@pytest.mark.parametrize("arm", ["FULL", "FLAT", "DAG_ID", "ID_ONLY", "RERUN_ALL", "FULL_NO_GATE", "AIDE"])
def test_all_comparison_views_keep_exact_treatment_information_and_provenance(arm):
    """Exercise actual treatment rendering; these values are nonformal fixtures."""
    from research.experiments.study_runtime import _treatment_evidence_view, _treatment_history_view
    config = {"data_ids": list(range(320)), "dataset_version": "nonformal-source-v1",
              "learning_rate": .001, "seed": 1001}
    records = [{"work_id": "nonformal-producer-"+str(index), "configuration_id": "same-public-config",
        "config": deepcopy(config), "seed": 1001, "receipt_path": None,
        "state": "completed", "metrics": {"source_metric": index/100},
        "result_revision": index, "actual_source_run": "nonformal-run-"+str(index)} for index in range(8)]
    nodes = [{"id": record["work_id"], "kind": "independent_fit_receipt", "source_revision": index}
             for index, record in enumerate(records)]
    edges = [{"from": record["work_id"], "to": "nonformal-current-claim", "relation": "supports_current_comparison"}
             for record in records]
    history = [{"actions": [{"action_work_id": record["work_id"], "kind": "inspect", "state": "completed",
        "result": {"nonformal_fixture": True, "source_run": record["actual_source_run"],
                   "source_revision": record["result_revision"], "config": record["config"]}}]} for record in records]
    view = _treatment_evidence_view(arm, records, nodes, edges)
    payload = {"arm": arm, "evidence_view": view, "history_view": _treatment_history_view(arm, history),
        "current_comparison": {"version": 2, "metric": "source_metric", "configuration": deepcopy(config)},
        "source_access": {"hidden_test_opened": False, "scope": "nonformal fixture public validation only"}}
    document, stats = intern_json_with_stats(payload)
    restored = restore_json(document)
    assert compact(restored) == compact(payload)
    assert document["encoding"] == "interned" and stats["saved_characters"] > 0
    assert restored["evidence_view"]["view"] == view["view"]
    assert restored["evidence_view"]["observations"] == records
    assert [record["actual_source_run"] for record in restored["evidence_view"]["observations"]] == [
        "nonformal-run-"+str(index) for index in range(8)]
    assert [record["result_revision"] for record in restored["evidence_view"]["observations"]] == list(range(8))
    assert restored["current_comparison"]["version"] == 2
    # Packing references describe equal original JSON values and positions;
    # they never introduce treatment-removed scientific edges or node types.
    if arm in {"ID_ONLY", "FLAT", "RERUN_ALL", "AIDE"}:
        assert "edges" not in restored["evidence_view"] and "nodes" not in restored["evidence_view"]
    if arm == "DAG_ID":
        assert "nodes" not in restored["evidence_view"]
        assert all(set(edge) == {"from", "to"} for edge in restored["evidence_view"]["edges"])
    if arm in {"FULL", "FULL_NO_GATE"}:
        assert restored["evidence_view"]["nodes"] == nodes and restored["evidence_view"]["edges"] == edges
    assert all(set(entry) == {"id", "value", "positions"} for entry in document["references"])


def test_changed_current_configuration_cannot_be_aliased_to_historical_producer():
    historical = {"work_id": "nonformal-old-fit", "configuration_id": "editable-label", "revision": 1,
        "full_config": {"data_ids": list(range(300)), "learning_rate": .001, "seed": 1001},
        "receipt": "nonformal-old-receipt", "actual_source_run": "nonformal-old-run"}
    current = {**deepcopy(historical), "work_id": "nonformal-new-fit", "revision": 2,
        "receipt": "nonformal-new-receipt", "actual_source_run": "nonformal-new-run"}
    current["full_config"]["learning_rate"] = .002
    current["full_config"]["seed"] = 1002
    payload = {"historical": [deepcopy(historical) for _ in range(5)],
               "current": [deepcopy(current) for _ in range(5)]}
    restored = restore_json(intern_json(payload))
    assert compact(restored) == compact(payload)
    assert restored["historical"][0]["full_config"]["learning_rate"] == .001
    assert restored["current"][0]["full_config"]["learning_rate"] == .002
    assert restored["historical"][0]["receipt"] != restored["current"][0]["receipt"]
    restored["current"][0]["full_config"]["data_ids"][0] = "user edit"
    assert restored["historical"][0]["full_config"]["data_ids"][0] == 0
    assert restored["current"][1]["full_config"]["data_ids"][0] == 0


@pytest.mark.parametrize("source", [None, True, 0, 1.0, "[\"ref\",\"r1\"]", [], {}, [{"x": 1}]*50])
def test_small_or_unique_values_use_plain_envelope_when_references_lose_size(source):
    document, stats = intern_json_with_stats(source)
    assert document["encoding"] == "plain" and document["references"] == []
    assert compact(restore_json(document)) == compact(source)
    assert stats["reference_count"] == 0


def test_decoded_reference_occurrences_are_independently_editable():
    document = intern_json(repeated_source())
    first, second = restore_json(document), restore_json(document)
    first["observations"][0]["config"]["data"]["train_sample_ids"][0] = "changed"
    assert first["observations"][1]["config"]["data"]["train_sample_ids"][0] == 0
    assert second["observations"][0]["config"]["data"]["train_sample_ids"][0] == 0
    segment = reference_segment(document, document["references"][0]["id"])
    segment["positions"].append("/changed")
    assert "/changed" not in document["references"][0]["positions"]


def test_plain_fallback_does_not_alias_independent_json_source_positions():
    shared = {"parameter": 1}
    source = {"historical": shared, "current": shared}
    document = intern_json(source)
    assert document["encoding"] == "plain"
    restored = restore_json(document)
    restored["current"]["parameter"] = 2
    assert restored["historical"]["parameter"] == 1
    assert source["current"]["parameter"] == 1
    document["root"]["current"]["parameter"] = 3
    assert document["root"]["historical"]["parameter"] == 1


def test_source_pointer_escaping_and_segment_diagnostics_are_exact():
    repeated = {"data": list(range(180))}
    source = {"~/": repeated, "a/b": deepcopy(repeated), "": deepcopy(repeated), "tail": "unchanged"}
    document = intern_json(source)
    assert ["/~0~1", "/a~1b", "/"] == document["references"][0]["positions"]
    diagnostics = segment_diagnostics(source, depth=1)
    assert diagnostics[0] == {"path": "", "characters": len(compact(source)), "kind": "object"}
    assert diagnostics[1]["path"] == "/~0~1"
    assert diagnostics[1]["characters"] == len(compact(repeated))


def test_random_json_roundtrips_without_input_mutation():
    rng = random.Random(17)
    def tree(depth):
        if depth == 0:
            return rng.choice([None, False, 0, 1, 1.0, -0.0, "text", "中文", "ref"])
        if rng.random() < .5:
            return [tree(depth-1) for _ in range(rng.randrange(5))]
        return {"key/"+str(index): tree(depth-1) for index in range(rng.randrange(5))}
    for _ in range(40):
        source = tree(4)
        original = compact(source)
        assert compact(restore_json(intern_json(source))) == original
        assert compact(source) == original


@pytest.mark.parametrize("source", [{1: "not a JSON key"}, (1, 2), {"value": float("inf")}, {"value": float("nan")}])
def test_non_json_source_values_are_not_silently_coerced(source):
    with pytest.raises((TypeError, ValueError)):
        intern_json(source)


def test_cyclic_source_is_rejected():
    source = []
    source.append(source)
    with pytest.raises(ValueError, match="Cyclic"):
        intern_json(source)


@pytest.mark.parametrize("root, references", [
    (["ref", "missing"], []),
    (["ref", "r1"], [{"id": "r1", "value": ["ref", "r1"], "positions": ["/x"]}]),
    (["array", []], [{"id": "r1", "value": 0, "positions": []}, {"id": "r1", "value": 0, "positions": []}]),
    (["object", [["x", 0], ["x", 1]]], []),
    ({"source_keys_are_not_tagged": True}, []),
    (["bad-tag", []], []),
])
def test_malformed_reference_documents_fail_explicitly(root, references):
    document = {"format": FORMAT, "encoding": "interned", "root": root, "references": references}
    with pytest.raises(ValueError):
        restore_json(document)
