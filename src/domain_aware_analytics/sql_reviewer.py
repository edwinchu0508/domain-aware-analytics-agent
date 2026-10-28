
from pydantic import BaseModel, Field
from agents import Agent, Runner, ModelSettings
from dataclasses import replace
import os
import json
import sqlglot
from openai.types.shared import Reasoning


def development_reviewer_config():
    """Explicit V3 process opt-in; historical/default reviewer stays unchanged."""
    model = os.getenv("V3_REVIEWER_MODEL", "gpt-4.1-mini")
    if model not in {"gpt-6.1-sol", "gpt-4.1-mini"}:
        raise ValueError("Unsupported V3 Reviewer model")
    effort = os.getenv("V3_REVIEWER_REASONING", "medium" if model == "gpt-6.1-sol" else "")
    if (model == "gpt-6.1-sol" and effort not in {"low", "medium", "high", "xhigh", "max"}
            or model == "gpt-4.1-mini" and effort):
        raise ValueError("Reasoning setting incompatible with V3 Reviewer model")
    cap = int(os.getenv("V3_REVIEWER_MAX_OUTPUT_TOKENS", "8192" if model == "gpt-6.1-sol" else "2048"))
    if not 1 <= cap <= 128000:
        raise ValueError("Invalid V3 Reviewer output budget")
    return model, ModelSettings(max_tokens=cap, reasoning=Reasoning(effort=effort) if effort else None)


def postgres_review_facts(sql_query: str) -> dict:
    """Dialect/AST facts only: never an approval, safety or business proof."""
    facts = {"execution_dialect": "PostgreSQL", "dialect_source": "V3 PostgreSQL execution configuration",
             "proof_scope": "ordering_direction_and_effective_NULL_placement_only",
             "task_correctness": "not_proven", "independent_business_review_required": True}
    try:
        trees = sqlglot.parse(sql_query, read="postgres")
        if len(trees) != 1 or trees[0] is None:
            return {**facts, "status": "unavailable", "reason": "not_single_statement"}
        order = trees[0].args.get("order")
        effective = []
        for item in order.expressions if order else []:
            nulls_first = item.args.get("nulls_first")
            if type(nulls_first) is not bool:
                return {**facts, "status": "unavailable", "reason": "NULL_order_not_resolved"}
            effective.append({"expression": item.this.sql(dialect="postgres"),
                              "direction": "desc" if item.args.get("desc") else "asc",
                              "effective_nulls": "first" if nulls_first else "last"})
        return {**facts, "status": "available", "effective_ordering": effective}
    except (sqlglot.errors.ParseError, ValueError, TypeError):
        return {**facts, "status": "unavailable", "reason": "AST_not_parsed"}


class SQLReview(BaseModel):
    approved: bool = Field(
        description="Whether SQL correctly answers the question"
    )
    feedback: str = Field(
        description="Reason for approval or rejection"
    )


reviewer_agent = Agent(
    name="SQL Semantic Reviewer",
    model="gpt-4.1-mini",
    output_type=SQLReview,

    instructions="""
    You are an independent SQL reviewer.

    Review the candidate SQL against:
    1. The ORIGINAL user question.
    2. The database schema and data dictionary.

    Check:
    - Correct join keys and table relationships
    - Correct aggregation granularity
    - Customer identity and distinct counts
    - Date fields and time windows
    - Numerator and denominator definitions
    - Business metric definitions
    - Duplicate rows from joins
    - Missing or incorrect filters

    Reject SQL if it has a meaningful semantic error.

    Do not execute SQL.
    Do not assume a query is correct because it is valid SQL.
    If uncertain about correctness, reject and explain why.

    Return a clear approval decision and feedback.
    """
)


async def review_sql(
    user_question: str,
    sql_query: str,
    dictionary: str
) -> SQLReview:

    review_input = f"""
ORIGINAL USER QUESTION:
{user_question}

DATABASE DICTIONARY:
{dictionary}

CANDIDATE SQL:
{sql_query}

Review whether this SQL answers the question correctly.
"""

    selected_reviewer = reviewer_agent
    if "V3_REVIEWER_MODEL" in os.environ:
        model, settings = development_reviewer_config()
        review_input += ("\n\nPOSTGRESQL EXECUTION FACTS (deterministic dialect/AST evidence):\n" +
                         json.dumps(postgres_review_facts(sql_query), ensure_ascii=False, indent=2) +
                         "\nThese facts do not prove SQL safety, Contract-to-question meaning, or final-answer correctness. "
                         "Review the original question and all supplied Contract/validation/result evidence independently.")
        selected_reviewer = reviewer_agent.clone(
            model=model if isinstance(reviewer_agent.model, str) else reviewer_agent.model,
            model_settings=replace(reviewer_agent.model_settings, max_tokens=settings.max_tokens,
                                   reasoning=settings.reasoning))
    result = await Runner.run(
        selected_reviewer,
        review_input
    )

    return result.final_output
