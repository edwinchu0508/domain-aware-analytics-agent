# Reproducibility and release checks

This source snapshot is derived from the final evaluated A–D checkpoint and E control. `SOURCE_PROVENANCE.json` lists source SHA-256 values and portability transformations. `RELEASE_MANIFEST.json` lists the files of this public release. The original development tree and sealed experimental files are not rewritten.

Use Python 3.11 (recorded patch version 3.11.17) and the pinned direct dependencies in `requirements.txt`; pytest is in `requirements-dev.txt`. The SDK, Pydantic and SQLGlot versions are intentionally pinned because their schema/AST behavior matters. A full transitive OS/Conda environment is not bundled. No dependency installation or fresh live model/database test was performed during publication; test success in the existing environment is not a clean-room restoration claim.

From the repository root, run `python -B -m pytest tests -q`. Tests cover actual SDK Runner/tool dispatch with scripted model outputs, SQLite fixture execution, dictionary retrieval and schema matching, SQL safety positives/negatives, NULL and conditional aggregates, disagreement-driven repair, stale evidence rejection, output/diagnostic separation, exceptions, missing usage and the two-request E path. HTTP and PostgreSQL are forbidden in the tests. Scripted reviewers test coordination, not the quality of a real LLM's judgments.

On restricted Windows sandboxes, asyncio's local event-loop socket or temporary-directory creation may be blocked. Use an ordinary permitted local shell for offline tests; do not change the agent's execution behavior or enable real network/database access to bypass a test failure.

The CLI and `examples/offline_demo.py` can run from a source checkout. An editable package install also exposes `analytics-agent`. The release check verifies the manifest and AST compilation without connecting anywhere. The staged-file scanner flags known secret/private-path/artifact patterns and large files; it is a defense-in-depth check, not a guarantee that all sensitive data can be detected automatically.

Historical result seals and aggregate numbers are included as hashes/measurements, not private payloads. Reproduction of the original 34-question answers and scoring requires private benchmark materials and the original data context, which are intentionally absent. External grading must occur after predictions are frozen; the public CLI has no Gold-loading or scoring path.
