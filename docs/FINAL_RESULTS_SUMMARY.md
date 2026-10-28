# Final results: accuracy, reliability and cost

This is the sanitized English publication of the sealed project findings, prepared without new model calls, database queries or changes to judgments. The original internal summary has SHA-256 `cfaadef3ecd446fa0c91b80e9b69ca2ad34572f2d094e7611c9f63dcf7edddb7`.

## Scope and configuration

Olist PostgreSQL; dedicated read-only role; repeatable-read read-only transactions; five-second SQL timeout. All configurations used `gpt-6.1-sol` with medium reasoning and Responses API, with no SDK automatic retries. A–D use a single solver with typed intent; B/D add seven curated definitions, C/D add independent result and answer review. E performs one SQL-generation request and, after successful execution, one answer-generation request.

The original A–D experiment planned 40 questions and 160 sessions. It stopped on a reviewer model timeout with unknown token usage during question 35. It launched 138 sessions; 22 were Not Started. The four-arm-complete prefix, questions 1–34, was declared eligible after execution and before Gold retrieval. All failures within that prefix remain in the denominator. E ran subsequently on the same 34 tasks. The analysis contains 170 terminal-status records, not 170 successful answers or a completed 40-question benchmark.

Gold is a reconstructed, executed and reviewed reference, not the original byte-identical artifact. Its SHA-256 is `dad46c7fa8d02b3c47dcbaf930bf178c3d7d7840f7cf7e30f22885140df7ad0f`. Six tasks lack exact surviving original reference values (T015, T017, T022, T025, T028, T033); T033 includes a reconstructed SQL expression. Neither private SQL nor reference answers are published here.

## Aggregate results

| Arm | Main correct | Sensitivity correct | Completed | Failed | Unsupported | Requests | Tokens | Total estimated USD | Mean latency (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 30/34 | 32/34 | 33 | 1 | 0 | 108 | 202,962 | 0.3046153 | 10.76094 |
| B | 31/34 | 33/34 | 34 | 0 | 0 | 108 | 251,326 | 0.3366014 | 11.08406 |
| C | 28/34 | 30/34 | 31 | 3 | 0 | 174 | 458,863 | 1.1252857 | 31.37588 |
| D | 31/34 | 33/34 | 34 | 0 | 0 | 179 | 539,979 | 1.2048772 | 31.78035 |
| E | 30/34 | 31/34 | 32 | 0 | 2 | 66 | 41,966 | 0.1235080 | 5.32818 |

Every accuracy, cost/task and latency/task uses a denominator of 34, including failures and unsupported sessions. Native Completed, internal verified, and externally Correct are different dimensions. Cost is an estimate under the study's recorded standard rates, not an invoice; average latency excludes batch-start and between-task administration.

Main accuracies are A 88.24%, B 91.18%, C 82.35%, D 91.18%, E 88.24%. Sensitivity accuracies are 94.12%, 97.06%, 88.24%, 97.06%, 91.18%, respectively. Sensitivity accepts correctly rounded continuous means at two decimals and leaves all other requirements unchanged. See EXPERIMENTS.md for precision rules and adjudications.

E's mean estimated cost is 40.55% of A's; its mean latency is 49.51% of A's. Matched reviewer cost increases are C/A 3.69× and D/B 3.58×; latency increases are 2.92× and 2.87×. D/A includes both dictionary and verification and must not be described as a pure reviewer effect.

## Paired uncertainty

Exact two-sided McNemar tests use the counts below. The two discordant counts mean first arm correct/second wrong, then first wrong/second correct.

| Pair | Main discordance | Main p | Sensitivity discordance | Sensitivity p |
|---|---:|---:|---:|---:|
| A/B | 0:1 | 1.00 | 0:1 | 1.00 |
| A/C | 2:0 | 0.50 | 2:0 | 0.50 |
| A/D | 0:1 | 1.00 | 0:1 | 1.00 |
| A/E | 2:2 | 1.00 | 2:1 | 1.00 |
| B/C | 3:0 | 0.25 | 3:0 | 0.25 |
| B/D | 0:0 | 1.00 | 0:0 | 1.00 |
| B/E | 2:1 | 1.00 | 2:0 | 0.50 |
| C/D | 0:3 | 0.25 | 0:3 | 0.25 |
| C/E | 2:4 | 0.6875 | 2:3 | 1.00 |
| D/E | 2:1 | 1.00 | 2:0 | 0.50 |

No comparison is significant. The Wilson 95% interval for 30/34 is 73.38%–95.33%. These tests do not prove systems equivalent, account for all grading uncertainty, or estimate variability across repeated model samples.

## Interpretation and failures

The hypothesis that domain knowledge plus self-verification improves accuracy was not supported on this sample. Dictionary retrieval was genuinely active and development tests demonstrated independent checking and repair, but invocation alone is not evidence of incremental accuracy.

Main completed-answer errors were shared: A–D had the same precision-sensitive mean tasks and a common denominator-definition task; E differed on one precision presentation. Under sensitivity, the shared denominator-definition task remains incorrect across all five. Between-arm score differences also involve execution failures and unsupported outputs, so a simple score ranking should not be interpreted as a general reasoning ranking.

T009 illustrates verification overhead: C's primary result matched the reference, but an additional reviewer-requested duplication check timed out after approximately five seconds and no final answer was delivered. A/B/D were accepted. E was accepted by explicit adjudication limited to this dataset. Publication does not claim that replacing SQL NULL with zero is generally equivalent.

T019 illustrates a shared ambiguity: all five configurations used a plausible population including orders without item rows, whereas the reference used item-bearing orders. B/D retrieved related definitions without resolving that population choice. C/D raw reviews were PROVEN, while final session verification remained UNKNOWN/false; this must not be described as a fully verified-correct answer. No additional checks or repairs resolved the issue. The historical grades remain unchanged.

## Limits of the evidence

Only one dataset, 34 questions and one stochastic run per arm were analyzed. A–D label masking was operational, not external double-blind evaluation; development and AI-assisted scoring shared context. E was added and graded after reveal. Reconstructed Gold and human adjudications introduce additional uncertainty. Task difficulty was not independently calibrated. Earlier miniature-model pipelines used different tasks/Gold/budgets and cannot be directly compared with these accuracy figures.

The before/after schema, table counts and available modification statistics agreed, but those metadata alone cannot prove absolute data immutability. The study relied on the data custodian's no-write window and task-local read-only snapshots. Isolation was tool-level, not OS-level.

## Provenance hashes

The following SHA-256 values identify retained private evidence. Hash publication does not make the payloads public or independently reproducible.

| Artifact | SHA-256 |
|---|---|
| A–D experiment003 result seal | `4e888c456294e4db93b85e229b3374cca67cfa19b43f2fa9d3cedcacce196bf5` |
| A–D final masked judgments seal | `589a7c3d1100ac17789db9819f5820643cba9c1869bad2609e7fe5ea237f55bc` |
| A–D scoring result seal | `71822f75ae4ad966369d4b2e04eae7acc390c751eb619eae22c71d134676f1d2` |
| E result seal | `4cb12a0969428553b740d33de5a88a82f8b1f6f0ccacacee2a76d9264ff41145` |
| E scoring result seal | `02d6f3a62edf7c7c2726ca3cdc98110cf41a44d2a8538c9e5e2af8574b1183b8` |

The publication process rechecked these file hashes without distributing the private artifacts or changing any judgment.
