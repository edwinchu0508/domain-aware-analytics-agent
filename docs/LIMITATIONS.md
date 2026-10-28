# Limitations

- The analyzed sample contains 34 paired questions from one Olist database and one run per configuration. Statistical power is low and stochastic variation was not estimated. Non-significance is not equivalence.
- The complete prefix was selected after the original 40-question run stopped and before Gold retrieval, rather than being the original preregistered 34-question scope. A claim of full 40-task accuracy would be incorrect.
- E was introduced after A–D results were revealed. It was not blinded; protocol and budgets also differ by design (up to two calls versus a bounded agent).
- Gold was reconstructed from surviving materials, not restored byte-for-byte. One SQL expression was completed from documented intent; six questions lack exact surviving original reference values. Current reconstructed references were executed and reviewed, but this does not recover the original artifacts.
- AI-assisted scoring and development involved the same team/environment. Group-label masking reduced one bias source; it did not provide an external independent evaluator.
- Model capability may have produced a ceiling effect on these tasks, but difficulty was not independently calibrated. No general claim about all analytics workloads or other datasets is justified.
- Domain definitions encode reviewed project conventions. They cannot decide an underspecified user's denominator or silently override explicit intent. Lexical retrieval has limited paraphrase coverage.
- A typed intent and its hash do not authenticate business meaning. Partial deterministic evidence and independent model review can miss a shared population error.
- Extra checking may increase latency/cost, trigger timeouts, or unnecessarily reject correct SQL. Reviewer PROVEN is scoped evidence, not external task correctness.
- Complex SQL may pass execution safety while semantic proof remains UNKNOWN. Unsupported or unverified outputs remain coverage failures; no blanket correctness guarantee is offered for joins, multi-branch CTEs or windows.
- Task-local repeatable-read snapshots do not stop other writers across sessions. Comparable evaluation requires an external fixed snapshot or credible no-write window. Before/after counts alone do not prove unchanged content.
- Tool-level model isolation is not OS-level Python isolation. SQL safety relies on a minimal SELECT-only role and trusted server/schema configuration; it is not a substitute for database administration.
- Cost figures are usage-based estimates under recorded rates, not invoices. Missing usage must remain unknown. Live model availability, package availability and API behavior can change.
- Public source supports offline tests and configured live sessions. Private benchmark questions, reference SQL/answers, database snapshot and raw traces are withheld, so the original study cannot be exactly reproduced from this repository alone.
