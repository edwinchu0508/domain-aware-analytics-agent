"""Bounded retrieval of curated business definitions, never answer/SQL recipes.

No model, database, environment configuration or automatic writeback is used.
Schema availability is checked independently of lexical relevance. Source
curation is provenance, NOT proof that a definition answers the current question.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class KnowledgeProvenance(_Strict):
    source_file: str = Field(min_length=1, max_length=160)
    source_section: str = Field(min_length=1, max_length=200)
    basis: Literal["existing_project_definition", "reviewed_business_definition"]
    note: str = Field(min_length=1, max_length=400)


class KnowledgeEntry(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["entity", "relationship", "metric", "time", "assumption"]
    title: str = Field(min_length=1, max_length=160)
    keywords: tuple[str, ...] = Field(min_length=1, max_length=25)
    references: tuple[str, ...] = Field(max_length=20)
    content: str = Field(min_length=1, max_length=3000)
    review_status: Literal["source_curated", "reviewed", "pending"]
    provenance: KnowledgeProvenance

    @model_validator(mode="after")
    def canonical_metadata(self):
        if any(not re.fullmatch(r"[A-Za-z_][\w]*\.[A-Za-z_][\w]*", r) for r in self.references):
            raise ValueError("Knowledge references must be exact physical table.column names")
        if any(not k.strip() or len(k) > 100 for k in self.keywords):
            raise ValueError("Knowledge keywords must be bounded nonempty strings")
        if len(set(self.references)) != len(self.references):
            raise ValueError("Knowledge references must be unique")
        if self.review_status == "reviewed" and self.provenance.basis != "reviewed_business_definition":
            raise ValueError("Reviewed knowledge needs explicit business-review provenance")
        return self


class _KnowledgeDocument(_Strict):
    version: str = Field(min_length=1, max_length=40)
    domain: str = Field(min_length=1, max_length=100)
    curation_note: str = Field(min_length=1, max_length=500)
    entries: tuple[KnowledgeEntry, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({e.id for e in self.entries}) != len(self.entries):
            raise ValueError("Knowledge IDs must be unique")
        return self


def _json_size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _mentioned(term: str, text: str) -> bool:
    term = " ".join(term.casefold().split())
    # English identifiers have word boundaries; Chinese phrases have no such
    # whitespace convention. These are declared domain terms, not task patterns.
    if re.fullmatch(r"[a-z0-9_ .-]+", term):
        return bool(re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text))
    return term in text


class DomainKnowledge:
    """Read-only snapshot; a changed knowledge file requires an explicit reload."""

    def __init__(self, document: _KnowledgeDocument, source_sha256: str):
        self._document = document
        self.sha256 = source_sha256

    @classmethod
    def load(cls, path: str | Path) -> "DomainKnowledge":
        raw = Path(path).read_bytes()
        # Strict JSON/Pydantic validation forbids hidden question_id/answer/sql
        # fields. Content itself still requires human domain review.
        text = raw.decode("utf-8-sig", errors="strict")
        document = _KnowledgeDocument.model_validate_json(text, strict=True)
        return cls(document, hashlib.sha256(raw).hexdigest())

    def retrieve(self, question: str, schema: dict, *, max_entries: int = 4,
                 max_chars: int = 5000) -> dict:
        """Return relevant definitions with provenance under a total JSON budget.

        Retrieval does not select a query, infer JOINs, modify intent or certify
        semantics. No match/invalid schema references are explicit outcomes.
        Serialize compactly to preserve the documented max_chars bound.
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Knowledge retrieval requires a nonempty question")
        if type(max_entries) is not int or not 0 <= max_entries <= 6:
            raise ValueError("max_entries must be between zero and six")
        if type(max_chars) is not int or not 512 <= max_chars <= 20000:
            raise ValueError("max_chars must be between 512 and 20000")
        if not isinstance(schema, dict) or any(not isinstance(columns, dict) for columns in schema.values()):
            raise ValueError("Schema must map physical table names to column dictionaries")
        text = " ".join(question.casefold().split())
        available, unavailable = [], []
        for entry in self._document.entries:
            keyword_hits = sum(_mentioned(k, text) for k in entry.keywords)
            explicit_refs = sum(_mentioned(r, text) for r in entry.references)
            if keyword_hits == 0 and explicit_refs == 0:
                continue
            missing = [r for r in entry.references if r.split(".")[1] not in schema.get(r.split(".")[0], {})]
            if entry.review_status == "pending":
                unavailable.append({"id": entry.id, "reason": "review_pending"})
            elif missing:
                unavailable.append({"id": entry.id, "reason": "physical_references_unavailable", "missing": missing})
            else:
                tables = {r.split(".")[0] for r in entry.references}
                score = keyword_hits * 2 + explicit_refs * 4 + sum(_mentioned(t, text) for t in tables)
                available.append((score, entry.id, entry))
        available.sort(key=lambda item: (-item[0], item[1]))
        result = {"version": self._document.version, "domain": self._document.domain,
                  # Reserve the longest status spelling while packing entries;
                  # assigning the final status cannot grow past the budget.
                  "status": "budget_exhausted", "knowledge_sha256": self.sha256,
                  "entries": [], "unavailable_entries": unavailable[:max_entries],
                  "unavailable_count": len(unavailable), "omitted_relevant_entries": len(available),
                  "trust": "definitions_not_question_semantic_proof"}
        # Missing-reference diagnostics must also respect the total budget.
        while result["unavailable_entries"] and _json_size(result) > max_chars:
            result["unavailable_entries"].pop()
        if _json_size(result) > max_chars:
            raise ValueError("max_chars is smaller than the required provenance metadata")
        for _, _, entry in available:
            if len(result["entries"]) == max_entries:
                break
            candidate = entry.model_dump(mode="json")
            result["entries"].append(candidate)
            result["omitted_relevant_entries"] = len(available) - len(result["entries"])
            if _json_size(result) > max_chars:
                result["entries"].pop()
                result["omitted_relevant_entries"] = len(available) - len(result["entries"])
        result["status"] = ("partial" if unavailable or result["omitted_relevant_entries"] else "retrieved") if result["entries"] else (
            "budget_exhausted" if available else "unavailable" if unavailable else "no_match")
        assert _json_size(result) <= max_chars
        return result
