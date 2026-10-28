# Architecture and trust boundaries

The canonical entry point is `aask_analytics` in `analytics_agent_v1.py`. A single solver receives the question, physical schema, and (only for B/D) retrieved definitions. The two exposed tools are `analytics_get_schema` and `analytics_run_sql`. A tool invocation includes a typed `AnalysisIntent` before execution. The final solver output is a typed answer with claims referencing result cells.

`AnalysisIntent` records objective, entity grain, population, numerator, denominator, time scope, required filters, grouping, ordering, NULL/empty-set policy, output columns and unresolved ambiguities. It is a model interpretation, not ground truth. Repair must preserve its hash and meaning. Agreement between SQL and intent alone does not prove agreement with the original user request.

## Execution, observation and review

Execution safety is a distinct gate: SQLGlot parses and resolves physical references, rejects mutation/locking and unapproved functions/types, and checks bounded output clauses. Passing this gate only grants execution permission. Safety does not establish population, cardinality or task correctness.

The executor pins a read-only repeatable-read transaction and five-second statement timeout. Savepoints let ordinary query errors be handled without silently opening a different evidence snapshot. Complete cursor results are bounded by rows, bytes and read duration. Oversized results are not truncated into an apparently complete answer.

Observation separates primary output columns from diagnostic columns. C/D invoke an independent reviewer after execution and after answer generation. The reviewer sees the original request, fixed intent, actual SQL and result, PostgreSQL AST facts, prior checks and concerns. It may propose up to two independent checks through the same safety/execution boundary. This is post-execution verification, not the old pre-execution SQL reviewer pipeline.

## Evidence lifecycle

Check records retain source SQL/result IDs, snapshot binding, check purpose and coverage, actual diagnostic rows, comparison status, and validity. A repair creates a new primary attempt without erasing the earlier contradiction. Reuse requires compatible intent, an unchanged evidence snapshot and matching query/result provenance. Checks affected by changes become stale; a textually similar SQL statement is insufficient evidence of validity.

PROVEN, CONTRADICTED and UNKNOWN are scoped statuses. Deterministic disagreement cannot be overridden by a generic later model approval. Material unresolved concerns remain attached to the answer. Diagnostic cells cannot masquerade as user-requested final output. At most two repairs are available, and repeat SQL detection prevents buying repeated approval with the same candidate.

## Transport and persistence

All live generator and reviewer requests pass through a metered provider. SDK/model retries are disabled. Missing usage remains unknown and stops further requests; it is never treated as zero cost. Per-request/task deadlines and observed token limits are retained from the evaluated version. Exceptions preserve their semantics, while the agent's `finally` path saves attempted SQL, observations, reviews, concerns, workflow events and terminal state to an exclusively reserved trace file.

The model receives no arbitrary file-read, shell or Python execution tool. The Python process itself is not OS-confined. Local logs contain questions, SQL and results and are private by default.

## Publication portability

Nine core modules retain their evaluated implementation with package-relative import changes. Active transport/observer helpers were extracted from historical evaluation infrastructure; unused legacy adapters were omitted. The PostgreSQL adapter exposes the same schema/constraint discovery and read-only execution interface without importing the old backend or auto-loading its environment file. The E control's actual `run_one` implementation and prompts were extracted without private question loaders.

The public CLI is a single-session convenience entry point, not a replacement for the historical private benchmark launcher's custody and database-consistency attestations. Live operation of this exact packaging has not been re-benchmarked.
