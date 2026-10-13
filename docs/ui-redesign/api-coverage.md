# API coverage

The complete action, request/response, identity, revision, persistence, error and verification matrix is maintained in [UI-to-API coverage](ui-api-coverage.md). It includes all A01–A57 baseline UI action groups, the added A58 workspace analysis integration, source-confirmed API-only gaps and the implementation/validation update.

Read the [feature inventory](feature-inventory.md) for the F01–F102 baseline join and added F103 analysis scope, the [implementation plan](implementation-plan.md) for phase status, and the [56-row test matrix](test-matrix.md) for exact executed test names, results, logs and real API/worker/browser versus fault/mock/NOT RUN boundaries. The later analysis scope has a separate acceptance extension; it does not alter the original 56-row count.

The generated contract contains 185 HTTP operations across 142 paths. These action groups document reachable frontend capabilities and their source owners; they do not assert all-operation browser coverage. The [frontend API guide](../FRONTEND_API_GUIDE.md#workspace-ai-analysis) documents the four new analysis operations, saved context and explicit freshness/retry recovery.
