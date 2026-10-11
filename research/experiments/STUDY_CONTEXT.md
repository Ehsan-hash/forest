# Scientific study context

Study requests retain their original messages, complete public evidence, action
bindings and response contract in the existing episode ledger. The working
request uses the same representation for every treatment arm.

## Shared evidence

`study_context.intern_json_with_stats` compares complete ordered JSON subtrees.
Identical objects and arrays are represented once with references at their
original semantic positions. The self-contained reference document retains
every value and ordered array element; `restore_json` reconstructs the source
exactly. Reference IDs follow source traversal order. Each published document
includes its reference definitions and original JSON Pointer positions.

`artifacts` and `observations` remain separate positions. Dataset manifests,
training configurations and execution receipts can share identical payloads.
Different revisions, seeds, scientific conditions and evidence identities keep
their own original values. Nonidentical evidence is never merged.

## Persistent memory and reads

`StudyMemory` stores complete versioned public JSON through the existing
`ContextStore`. Its catalog and JSON Pointer pages are scoped to one episode.
The caller registers the treatment's public view before storage: graph types
and edges remain subject to the original arm visibility rules.

`read_context_segment` selects a stored segment version and optionally a JSON
Pointer subtree. Its `content` is an exact character slice of the selected
value's compact JSON serialization. `offset`, `next_offset` and
`total_characters` refer to that serialization. Array element order and all
scientific values remain intact; pages do not have to be independently valid
JSON. Concatenate consecutive pages before parsing them.

A pointer to `/messages/0/content` selects a JSON string value. Its pages
therefore include the string's JSON quotation and escaping. Parsing the
complete concatenated pages reconstructs the exact original message string;
it does not automatically parse scientific JSON contained inside that string.
`complete` means the cursor reached the end of the selected value. It does not
mean that the scientific task or an evidence requirement is complete.

Reading is observational and cannot supply a fit, measured metric, scientific
repair or evidence of task completion. The current requested page body enters
the working history. Earlier reads appear as exact segment-version, pointer
and offset references. The complete segments and original page receipts stay
durable and independently retrievable, preventing recursive copies of prior
returned history. This applies equally to the public information available to
each treatment arm.

Native AIDE's review carries the complete current page in its review task
context, so upstream's terminal-output presentation cannot silently remove
the middle of a retrieved page. Older page bodies are replaced only in a
disposable prompt Journal. The durable native Journal retains the original
execution output, tree identities and ordinary recovery state.

### Provider interface capacity

`StudyMemory.read_json` accepts optional `response_capacity` and
`measure_response(page)` arguments. The runtime supplies a declared provider
input capability or a capacity observed in that provider's actual transport
rejection receipt. The memory API assumes no universal CLI capacity. For
example, an observed `max_chars` of 1,048,576 describes one serialized CLI
input; it is neither a token allowance nor a cumulative episode quota.

The measurement must include the complete prospective request: controls,
strict response schema, the selected working history, all results in the
current action batch, native AIDE review wrappers when applicable, and every
JSON escaping layer. Measuring only `content` or the page JSON cannot establish
that the physical request fits. The runtime must refresh previously unknown
capabilities when a matching transport receipt becomes available.

When capacity requires a shorter page, `requested_limit` retains the model's
request, `returned_characters` records the exact slice length, and
`capacity_limited` identifies the adjustment. Follow `next_offset` to obtain
the rest, with no cumulative read limit. All original characters and segment
versions remain stored. If even one source character cannot fit in the
selected request, `ContextPageCapacityError` reports the interface capacity
and minimum measured request size. The caller can select a smaller working
request and retrieve the same durable source.

The page search measures logarithmically many candidate prefix lengths. A
callback should reuse prepared invariant controls and source representations
where possible while measuring each final candidate exactly. It must not
perform inference, advance a native query identity, mutate durable Journal or
RNG state, or create scientific effects.

## Transport provenance

Each study Codex request records its effective schema, messages and request-owned
trace location before inference. The local CLI trace retains actual stdin,
inference instructions, arguments, schema, exit status, stdout, stderr and
elapsed time. Authentication environment values are excluded. Missing usage
stays unknown; subscription cost stays unpriced.

Only a verified completed CLI turn supplies a completed model response. Partial
messages, failed turns and malformed streams remain diagnostic evidence.
Explicit input-capacity rejection is distinct from model output truncation.
The provider's strict schema is a private transport copy; responses are checked
against the original scientific validator after optional null placeholders are
restored. A later executor error preserves the completed model response and
its physical inference timing.

A `context_page_read` ledger event records the exact locally constructed page
and its source cursor before the parent episode checkpoint. It does not prove
physical model delivery. The request record and actual CLI trace identify the
page or reference representation sent; the verified transport outcome records
whether that request completed. Native AIDE prompt selection must preserve its
ordinary saved Journal, action receipts and RNG state.

## Regression coverage

The suites cover exact reference roundtrips, source identity and ordering,
all registered treatment views and native AIDE, complete paged retrieval,
cross-episode isolation, actual child input capture, strict schema adaptation,
failed CLI receipts, retained usage and multi-round history growth. Real study
connectivity receipts and scientific results remain in the campaign directory.
The memory fixtures also cover an oversized requested page, exact nested
`ModelClient.build_request` character measurements, continuation until every
original character is reconstructed, and preservation of sources across
restart. These fixtures perform no model inference or GPU training.
