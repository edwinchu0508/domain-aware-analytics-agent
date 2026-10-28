# Evaluation design

Research question: does a domain-specialized, self-verifying analytics agent improve final task accuracy over a general SQL-capable baseline under matched model and database conditions?

A/B/C/D form dictionary and verification ablations. The same normal solver, schema visibility, SQL safety and database privileges are used. A is not artificially restricted to a weaker model. D−A is the primary contrast; B−A and C−A examine individual contributions. E is a later, simpler control, with one SQL generation and one result-answer request at most.

## Fixed operating conditions

Model: `gpt-6.1-sol`, medium reasoning, Responses API, zero SDK retries. A–D: at most 12 requests, two repairs and two diagnostic queries per session. E: at most two requests, one SQL attempt, no repair/review. Shared limits: 30 seconds/request, 8,192 output tokens, 50,000 observed task tokens, 120 seconds/task; 50 rows and 200,000 bytes of complete results. PostgreSQL uses a SELECT-only role, repeatable-read read-only transactions and five-second SQL timeout.

The original 40-question, four-arm experiment ended PARTIAL on a model request with unknown usage. It launched 138 of 160 assigned sessions. Four-arm-complete questions 1–34 were declared eligible after execution and before Gold retrieval; the remaining questions were not included. This is not a completed 40-question benchmark. E ran later on the same prefix; all 170 included terminal records remain in the five-arm comparison.

## Grading

Correct=1 only when the delivered final answer satisfies the requested business semantics and reference evidence. Incorrect, Unsupported, native Unverified, Failed and Not Started score zero. SQL execution success and reviewer approval are not correctness labels. A Completed answer can include internal UNKNOWN; that alone is not automatically an incorrect final answer.

Integers require equality. Displayed rounded values are compared against the reference rounded to the same precision. Main minimum display precision: currency two decimals; proportions four decimals or percentages two decimals; other continuous quantities four significant figures. Full precision only in an internal SQL result cannot repair an under-precise final answer. NULL and zero remain distinct; requested ordering, Top-K, ties, population, grain and date scope are checked semantically rather than by column-name equality.

The exploratory sensitivity analysis accepts correctly rounded continuous means at two decimals; all other semantic/completion requirements remain. It was added during masked review, before group reveal. It is not a replacement primary endpoint.

Three adjudications were retained: T012 accepts a defensible alternative counting unit only for the requested categories, without asserting universal equivalence; T022 applies precision rules to requested indicators, not clearly labeled ancillary approximations; E/T009 accepts this dataset's result without treating NULL-to-zero rewrites as generally equivalent.

A–D records were shuffled and masked; main and sensitivity judgments were sealed before label reveal. This is operational masking, not strict double-blind evaluation: the same development/AI-assisted environment had prior context. E was run/scored after reveal and was not blinded.

## Statistical reporting

Accuracy uses all 34 assigned tasks per arm. Completion and precision use distinct denominators; failures are not discarded from accuracy or mean cost. Exact two-sided McNemar tests use discordant paired outcomes. All ten pairwise main comparisons were non-significant without multiple-comparison adjustment; no positive significance or equivalence claim follows. Wilson score intervals quantify binomial uncertainty but do not capture prompt/model stochasticity, grading uncertainty or dataset selection.

## Mechanism attribution

Dictionary retrieval, relevance and use in planning are different from demonstrated accuracy improvement. Similarly, additional verification SQL, repair attempts, resolved contradictions and false alarms must be separated. Development fault injection demonstrated an evidence-preserving correction cycle; its success is not an independent task-accuracy measurement. Aggregate evaluation did not support the hypothesis of a statistically significant accuracy gain from specialization and verification.

The questions, Gold and raw traces are private. Public aggregate data and hashes support provenance, not independent reproduction of the original grading. A new independent evaluation requires separately authored questions, an executed/semantically reviewed reference, fixed data, prediction sealing before external grading, and retention of every assigned failure.
