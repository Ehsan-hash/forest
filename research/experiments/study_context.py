"""Lossless, self-contained JSON subtree references for public study context.

This changes representation only. It never selects, summarizes, truncates or
removes scientific values. IDs follow first source traversal order and use no
content digest. The complete original request belongs in the calling ledger.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from typing import Any


FORMAT = "forest.lossless-json-subtrees.v1"
INSTRUCTIONS = (
    "This is a lossless JSON reference document, not a summary. In interned encoding, "
    "every object is [\"object\",[[key,node],...]], every array is [\"array\",[node,...]], "
    "and [\"ref\",id] means the exact node in references with that id. Scalars remain "
    "literal JSON values. Object-key and array order are preserved. References may "
    "use other references. Each entry's positions lists all original JSON Pointer "
    "locations of that exact ordered subtree. Read the complete values in the "
    "reference table; expanding all references reconstructs every original value."
)


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _pointer(parent, part):
    return parent + "/" + str(part).replace("~", "~0").replace("/", "~1")


def _json_copy(value):
    """Copy JSON positions independently, even if the Python input aliases."""
    if isinstance(value, dict):
        return {key: _json_copy(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_copy(child) for child in value]
    return value


@dataclass
class _Node:
    kind: str
    value: Any = None
    children: list = field(default_factory=list)
    group: Any = None
    characters: int = 0


@dataclass
class _Group:
    order: int
    node: _Node | None = None
    positions: list[str] = field(default_factory=list)


def _source_tree(value):
    # The complete literal serialization is the equality key. No content hash,
    # shortened fingerprint or source identity is created or substituted.
    groups_by_literal = {}
    groups = []
    active = set()

    def visit(current, path):
        if type(current) not in (dict, list):
            if type(current) not in (str, int, float, bool, type(None)):
                raise TypeError("Context requires ordinary JSON values at " + (path or "/"))
            if isinstance(current, float) and not math.isfinite(current):
                raise ValueError("Context requires finite JSON numbers at " + (path or "/"))
            return _Node("scalar", value=current, characters=len(_dumps(current)))
        if id(current) in active:
            raise ValueError("Cyclic source is not a JSON tree at " + (path or "/"))
        if isinstance(current, dict) and any(type(key) is not str for key in current):
            raise TypeError("JSON object keys must be strings at " + (path or "/"))
        active.add(id(current))
        # Validate descendants before json.dumps, so unsupported values and
        # cycles have one explicit behavior instead of implicit coercion.
        node = _Node("object" if isinstance(current, dict) else "array")
        if isinstance(current, dict):
            node.children = [(key, visit(child, _pointer(path, key))) for key, child in current.items()]
            node.characters = len('["object",[]]') + sum(
                len(_dumps(key)) + child.characters + 3 for key, child in node.children) + max(0, len(node.children)-1)
        else:
            node.children = [visit(child, _pointer(path, index)) for index, child in enumerate(current)]
            node.characters = len('["array",[]]') + sum(child.characters for child in node.children) + max(0, len(node.children)-1)
        active.remove(id(current))
        literal = _dumps(current)
        group = groups_by_literal.get(literal)
        if group is None:
            group = _Group(len(groups), node)
            groups_by_literal[literal] = group
            groups.append(group)
        else:
            # Literal equality includes scalar types and all original order.
            node.children = group.node.children
        node.group = group
        group.positions.append(path)
        return node

    root = visit(value, "")
    # Collection above is bottom-up to validate before serialization. Public
    # IDs instead follow the first position's original preorder traversal.
    positions = {}
    def order(node):
        if node.group is None:
            return
        if node.group.order not in positions:
            positions[node.group.order] = len(positions)
        children = [child for _, child in node.children] if node.kind == "object" else node.children
        for child in children:
            order(child)
    order(root)
    groups.sort(key=lambda group: positions[group.order])
    return root, groups


def _render(root, groups, selected):
    ids = {group.order: "r" + str(index+1) for index, group in enumerate(
        group for group in groups if group.order in selected)}
    uses = {order: 0 for order in ids}

    def node_value(node, inline=None):
        if node.group and node.group.order in ids and node.group.order != inline:
            uses[node.group.order] += 1
            return ["ref", ids[node.group.order]]
        if node.kind == "scalar":
            return node.value
        if node.kind == "object":
            return ["object", [[key, node_value(child)] for key, child in node.children]]
        return ["array", [node_value(child) for child in node.children]]

    document = {"format": FORMAT, "encoding": "interned", "instructions": INSTRUCTIONS,
                "root": node_value(root), "references": []}
    entries = {}
    for group in groups:
        if group.order in ids:
            entry = {"id": ids[group.order], "value": node_value(group.node, inline=group.order),
                     "positions": list(group.positions)}
            entries[group.order] = entry
            document["references"].append(entry)
    # Exact marginal size: inline every emitted use and remove this one table
    # entry. Nested references retain their definitions and source positions.
    benefits = {}
    for order, entry in entries.items():
        size = len(_dumps(entry["value"]))
        reference = len(_dumps(["ref", entry["id"]]))
        metadata = len(_dumps(entry)) - size
        comma = 1 if len(entries) > 1 else 0
        benefits[order] = (uses[order]-1)*size - uses[order]*reference - metadata - comma
    return document, uses, benefits


def intern_json_with_stats(value):
    """Return a complete reversible document and actual serialized-size stats.

    Only repeated objects/lists with positive marginal serialized savings are
    referenced. A plain envelope is used when tagging/reference overhead would
    make the whole document larger. No source value or source position is lost.
    """
    root, groups = _source_tree(value)
    selected = set()
    # Reject obvious losses before building the reference graph. IDs here use
    # an upper bound on their eventual width; final IDs follow selected preorder.
    id_bound = "r" + str(len(groups))
    for group in groups:
        count = len(group.positions)
        if count > 1:
            size = group.node.characters
            reference = len(_dumps(["ref", id_bound]))
            metadata = len(_dumps({"id": id_bound, "value": 0, "positions": group.positions}))-1
            if (count-1)*size - count*reference - metadata - 1 > 0:
                selected.add(group.order)
    while True:
        document, uses, benefits = _render(root, groups, selected)
        losses = {order for order, benefit in benefits.items() if benefit <= 0}
        if not losses:
            break
        # Pruning descendants first preserves useful outer shared structures.
        selected.remove(next(group.order for group in reversed(groups) if group.order in losses))
    plain = {"format": FORMAT, "encoding": "plain", "root": _json_copy(value), "references": []}
    if not selected or len(_dumps(document)) >= len(_dumps(plain)):
        document, uses, benefits = plain, {}, {}
    original_characters = len(_dumps(value))
    output_characters = len(_dumps(document))
    marginal = {entry["id"]: benefits[group.order] for group, entry in zip(
        (group for group in groups if group.order in benefits), document["references"])}
    stats = {"input_characters": original_characters, "output_characters": output_characters,
             "saved_characters": original_characters-output_characters,
             "output_to_input_ratio": output_characters/original_characters,
             "encoding": document["encoding"], "reference_count": len(document["references"]),
             "original_reference_positions": sum(len(entry["positions"]) for entry in document["references"]),
             "encoded_reference_uses": sum(uses.values()),
             "marginal_reference_savings": marginal}
    return document, stats


def intern_json(value):
    """Return the self-contained lossless representation (no size quota)."""
    return intern_json_with_stats(value)[0]


def _decoder(document):
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValueError("Unknown lossless JSON context format")
    if document.get("encoding") not in ("plain", "interned") or "root" not in document:
        raise ValueError("Lossless context requires its encoding and complete root")
    references = document.get("references")
    if not isinstance(references, list):
        raise ValueError("Lossless context requires a self-contained reference table")
    table = {}
    for entry in references:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"] or "value" not in entry:
            raise ValueError("Malformed context reference")
        if entry["id"] in table:
            raise ValueError("Duplicate context reference ID")
        if not isinstance(entry.get("positions"), list) or any(not isinstance(path, str) for path in entry["positions"]):
            raise ValueError("Reference positions must be original JSON Pointers")
        table[entry["id"]] = entry
    visiting, cache = set(), {}

    def reference(identifier):
        if not isinstance(identifier, str) or identifier not in table:
            raise ValueError("Unknown context reference")
        if identifier in visiting:
            raise ValueError("Cyclic context reference graph")
        if identifier not in cache:
            visiting.add(identifier)
            cache[identifier] = decode(table[identifier]["value"])
            visiting.remove(identifier)
        # Independent JSON source positions must remain independently editable.
        return deepcopy(cache[identifier])

    def decode(node):
        if not isinstance(node, list):
            if type(node) not in (str, int, float, bool, type(None)) or isinstance(node, float) and not math.isfinite(node):
                raise ValueError("Malformed literal context scalar")
            return node
        if len(node) != 2 or node[0] not in ("object", "array", "ref"):
            raise ValueError("Every context container requires an explicit type tag")
        tag, children = node
        if tag == "ref":
            return reference(children)
        if not isinstance(children, list):
            raise ValueError("Malformed tagged context container")
        if tag == "array":
            return [decode(child) for child in children]
        result = {}
        for pair in children:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str) or pair[0] in result:
                raise ValueError("Malformed or duplicate context object key")
            result[pair[0]] = decode(pair[1])
        return result
    if document["encoding"] == "plain":
        if references:
            raise ValueError("Plain JSON context cannot contain references")
        _source_tree(document["root"])
        return lambda: _json_copy(document["root"]), reference, table
    # Validate even unreachable definitions, preventing dangling/cyclic table
    # entries from being presented as independently readable source segments.
    for identifier in table:
        reference(identifier)
    return lambda: decode(document["root"]), reference, table


def restore_json(document):
    """Expand a document into the exact original ordered JSON tree."""
    restore, _, _ = _decoder(document)
    return restore()


def reference_segment(document, reference_id):
    """Read one complete original subtree, with its explicit source positions."""
    _, reference, table = _decoder(document)
    value = reference(reference_id)
    return {"reference_id": reference_id, "positions": list(table[reference_id]["positions"]), "value": value}


def segment_diagnostics(value, *, depth=1):
    """Report exact compact JSON character counts at original source paths."""
    if type(depth) is not int or depth < 0:
        raise ValueError("Diagnostic depth must be a nonnegative integer")
    _source_tree(value)
    rows = []
    def visit(current, path, level):
        rows.append({"path": path, "characters": len(_dumps(current)),
                     "kind": "object" if isinstance(current, dict) else "array" if isinstance(current, list) else "scalar"})
        if level == depth:
            return
        if isinstance(current, dict):
            for key, child in current.items():
                visit(child, _pointer(path, key), level+1)
        elif isinstance(current, list):
            for index, child in enumerate(current):
                visit(child, _pointer(path, index), level+1)
    visit(value, "", 0)
    return rows
