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

`read_context_segment` returns exact original text with offsets, total length
and continuation position. Reading is observational and cannot supply a fit,
measured metric, scientific repair or evidence of task completion. Prior reads
are referenced in subsequent history, preserving their complete original
receipts without recursively copying their returned history into itself.

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

## Regression coverage

The suites cover exact reference roundtrips, source identity and ordering,
all registered treatment views and native AIDE, complete paged retrieval,
cross-episode isolation, actual child input capture, strict schema adaptation,
failed CLI receipts, retained usage and multi-round history growth. Real study
connectivity receipts and scientific results remain in the campaign directory.
