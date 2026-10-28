"""Read-only domain retrieval, independent unfamiliar schemas, no LLM/DB/Gold."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from domain_aware_analytics.domain_knowledge_v1 import DomainKnowledge

ROOT = Path(__file__).resolve().parents[1]
OLIST = ROOT / "src/domain_aware_analytics/domain_knowledge/olist_v1.json"
SCHEMA = {
    "orders": {"order_id": "text", "order_purchase_timestamp": "timestamp without time zone", "order_delivered_customer_date": "timestamp without time zone", "customer_id": "text", "order_status": "text"},
    "customers": {"customer_id": "text", "customer_unique_id": "text"},
    "order_items": {"order_id": "text", "order_item_id": "integer", "price": "numeric", "freight_value": "numeric"},
    "payments": {"order_id": "text"}, "reviews": {"order_id": "text"},
}


def test_revenue_retrieval_is_reviewed_definition_not_sql_recipe():
    store = DomainKnowledge.load(OLIST)
    r = store.retrieve("Merchandise revenue excluding freight", SCHEMA)
    assert r["entries"][0]["id"] == "merchandise_revenue"
    assert all(e["review_status"] in {"source_curated", "reviewed"} for e in r["entries"])
    assert "not filter" in r["entries"][0]["content"]
    assert r["entries"][0]["provenance"]["source_file"] == "data_dictionary.md"
    assert "definitions_not_question_semantic_proof" == r["trust"]
    assert not any("sql" in e or "answer" in e or "question_id" in e for e in r["entries"])


def test_retrieval_is_relevant_schema_checked_deterministic_and_readonly():
    before = OLIST.read_bytes()
    store = DomainKnowledge.load(OLIST)
    a = store.retrieve("AOV for purchase quarter", SCHEMA, max_entries=2)
    assert len(a["entries"]) == 2
    assert {e["id"] for e in a["entries"]} == {"order_aov", "purchase_time"}
    assert a == store.retrieve("AOV for purchase quarter", SCHEMA, max_entries=2)
    assert store.retrieve("Magnetometer oscillation", SCHEMA)["status"] == "no_match"
    b = store.retrieve("merchandise revenue", {"telemetry": {"reading": "numeric"}})
    assert b["status"] == "unavailable" and not b["entries"]
    assert b["unavailable_entries"][0]["reason"] == "physical_references_unavailable"
    assert OLIST.read_bytes() == before
    assert not hasattr(store, "writeback")


def test_full_compact_json_respects_budget_and_no_unconditional_dump():
    store = DomainKnowledge.load(OLIST)
    for budget in (512, *range(1000, 2600, 17)):
        r = store.retrieve("orders customers revenue retention freight AOV purchase status", SCHEMA, max_entries=6, max_chars=budget)
        assert len(json.dumps(r, ensure_ascii=False, separators=(",", ":"))) <= budget
    assert store.retrieve("revenue", SCHEMA, max_entries=0)["status"] == "budget_exhausted"
    assert not store.retrieve("revenue", SCHEMA, max_entries=0)["entries"]
    with pytest.raises(ValueError):store.retrieve("revenue", SCHEMA, max_entries=7)


def test_different_domain_reviewed_entries_and_pending_do_not_get_guessed(tmp_path):
    template = json.loads(OLIST.read_text(encoding="utf-8"))
    entry = template["entries"][0]
    entry.update(id="sensor_observation", title="Observation identity", keywords=["observation", "samples"], references=["samples.sample_key"],
                 content="An observation is one samples.sample_key; a station is a separate entity.", review_status="reviewed",
                 provenance={"source_file": "sensor_catalog.md", "source_section": "Observation", "basis": "reviewed_business_definition", "note": "Synthetic independent metadata review fixture."})
    pending = {**entry, "id": "station_pending", "review_status": "pending"}
    template.update(domain="Sensor laboratory", entries=[entry, pending])
    path = tmp_path / "knowledge.json";path.write_text(json.dumps(template), encoding="utf-8")
    store = DomainKnowledge.load(path)
    r = store.retrieve("Count observations from samples", {"samples": {"sample_key": "bigint"}})
    # The explicitly declared samples keyword selects the definition; arbitrary
    # schema overlap alone does not inject unrelated knowledge.
    assert len(r["entries"]) == 1 and r["entries"][0]["id"] == "sensor_observation"
    assert r["unavailable_entries"] == [{"id": "station_pending", "reason": "review_pending"}]
    r["entries"][0]["content"] = "caller mutation"
    assert store.retrieve("samples", {"samples": {"sample_key": "bigint"}})["entries"][0]["content"] != "caller mutation"


def test_invalid_provenance_duplicate_ids_and_hidden_answer_fields_rejected(tmp_path):
    original = json.loads(OLIST.read_text(encoding="utf-8"))
    for mutate in (
        lambda d: d["entries"][0].update(answer=123),
        lambda d: d["entries"][0].update(review_status="reviewed"),
        lambda d: d["entries"].append(d["entries"][0]),
        lambda d: d["entries"][0].update(references=["imagined_table"]),
    ):
        data = json.loads(json.dumps(original));mutate(data)
        path = tmp_path / "bad.json";path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValidationError):DomainKnowledge.load(path)
