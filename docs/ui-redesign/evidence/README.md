# Verification evidence

The delivery production assets are `index-BIrSAtO6.js`, `WorkspacePage-BMJxELRb.js` and `index-CVqPoWfS.css`. Tests use the independent API/worker at `127.0.0.1:18340` and `/tmp/forest-redesign-runtime-20261013`; these are test records rather than the user's research data.

The [delivery manifest](run-manifest.json), [source hashes](source-sha256.tsv), [changed-file index](changed-files.tsv) and [served asset readback](served-build-readback.json) identify the tested source and exact delivered assets. The [final report](../final-report.md) joins implementation, validation and remaining acceptance conditions.

| Evidence | Result and method |
| --- | --- |
| [Production build](build-delivery.txt) | TypeScript and Vite production build PASS. The large lazy Monaco chunk warning remains. |
| [Browser cases](browser-results.md), [machine report](e2e-delivery.json), [transcript](e2e-delivery.txt) | 108/108 PASS, 196.30 seconds, no skipped/unexpected/flaky cases. Each case has its method, artifact/log references and any fixture boundary. |
| [Unit cases](unit-results.md), [machine report](unit-delivery.json), [transcript](vitest-delivery.txt) | 102/102 PASS across 20 source files. Helper/component execution supplements the browser/worker checks. |
| [Named analysis/backend cases](../backend-analysis-results.md), [transcript](analysis-integration-tests-final.txt) | 35 PASS, 2 PostgreSQL variants SKIP in 64.33 seconds. All 37 case names, timings and evidence methods are indexed. |
| [Analysis history completeness](analysis-context-history-tests.txt) | 13-case retest PASS, including five saved history models, foreign-project exclusion, tool/effect-only freshness and bounded credential-redacted logs. These are a subset of the named backend run. |
| [Analysis worker receipts](analysis-worker-smoke.json), [transcript](analysis-worker-smoke.txt), [script](../scripts/analysis-smoke.py) | Actual project/node/path jobs completed through the API, scheduler, worker and ModelClient HTTP adapter; actual completed/failed experiments and actual Crossref metadata reached all three saved reports. Model prose is an authored protocol fixture; live AI quality is NOT RUN. |
| [Numerical measurements](actual-numerical-run.json), [paired calculation](actual-paired-inference.json), [source checks](actual-source-bound-check.json) | Actual bounded computations and source-bound receipt collection. These do not establish scientific improvement or publishability. |
| [Measured-source figure](actual-figure-source.json), [SVG](actual-figure.svg), [PDF](actual-figure.pdf) | Actual worker-rendered source bindings and downloaded figure formats. |
| [Binary round trip](actual-binary-roundtrip.json), [repository receipt](actual-repository-manifest.json) | Actual persisted/downloaded bytes and actual repository/commit readback. |
| [Runtime reconnect](../runtime-observations.json) | Actual owner authentication and EventSource close/reconnect with dirty-draft protection. |
| [Before/after journeys](../journey-comparison.md) | Actual browser/API/worker paths and real current manuscript PDF; click counts and main-view switches are distinguished. |
| [Visual review](../visual-comparison.md) | Capture geometry and opened-pixel review have separate scopes and exact build provenance. |
| [Original backend suite](baseline-pytest.txt), [failure classification](../backend-baseline-failures.json) | 1429 PASS, 71 SKIP, 63 FAIL on this macOS host. Linux Bubblewrap prerequisites and the subsequently corrected skill-discovery assertion are explicitly classified; this is not a clean full-backend PASS. |

Earlier browser/build/visual diagnostics remain separately named. Retests are not added to unique scenario counts. The [acceptance matrix](../test-matrix.md) retains PARTIAL, MOCK and NOT RUN conditions, including live model/Agent scientific behavior, PostgreSQL, source entailment and exhaustive native keyboard coverage.
