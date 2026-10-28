"""Request-local convergence guard; no SQL execution or semantic rewriting.

The AST key is deliberately conservative: SQLGlot only reserializes the
PostgreSQL AST without comments.  Predicates, literals, qualified names and
quoted identifiers are not reordered, removed or interpreted as equivalent.
Identical rejection *categories* are a separate bounded-progress signal, not
proof that different SQL statements mean the same thing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import sqlglot


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ast_fingerprint(query: str) -> str | None:
    """Leave parse errors to the real Validator; still detect exact repeats."""
    try:
        statements = sqlglot.parse(query, read="postgres")
        if len(statements) != 1 or statements[0] is None:
            return None
        serialized = statements[0].sql(dialect="postgres", pretty=False, comments=False)
        return _sha256(serialized)
    except Exception:
        return None


@dataclass
class SQLAttemptControl:
    """Bounded progress state owned by one request, never shared across runs.

    ``start`` reserves a statement synchronously, before a caller can await a
    Reviewer. Thus sibling async tool calls cannot both execute identical SQL.
    A caller must still check ``stop_reason`` after each await and before DB
    execution: an already-running sibling cannot be cancelled by this object.
    """

    rejection_limit: int = 3
    max_attempts: int | None = None  # opt-in: initial SQL plus bounded repairs
    stop_reason: dict | None = field(default=None, init=False)
    _attempts: list[dict] = field(default_factory=list, init=False, repr=False)
    _raw_seen: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _ast_seen: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _last_rejection: tuple[str, str] | None = field(default=None, init=False, repr=False)
    _rejection_streak: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.rejection_limit) is not int or self.rejection_limit < 1:
            raise ValueError("rejection_limit must be a positive integer")
        if self.max_attempts is not None and (type(self.max_attempts) is not int or self.max_attempts < 1):
            raise ValueError("max_attempts must be a positive integer or None")

    def _existing(self, query: str) -> tuple[int | None, str, str | None]:
        raw_sha256 = _sha256(query)
        ast_sha256 = _ast_fingerprint(query)
        previous = self._raw_seen.get(raw_sha256)
        if previous is None and ast_sha256 is not None:
            previous = self._ast_seen.get(ast_sha256)
        return previous, raw_sha256, ast_sha256

    def start(self, query: str) -> dict | None:
        """Reserve SQL before any await; return a terminal rejection on repeat."""
        if self.stop_reason is not None:
            return self.stop_reason
        previous, raw_sha256, ast_sha256 = self._existing(query)
        if previous is not None:
            old = self._attempts[previous - 1]
            self.stop_reason = {
                "status": "rejected", "stage": "convergence", "code": "REPEATED_SQL",
                "reason": "The same raw SQL or reserialized AST was already submitted; no new Reviewer or DB work is permitted.",
                "retryable": False, "previous_attempt": previous,
                "previous_status": old["status"], "raw_sha256": raw_sha256,
                "sql_fingerprint": ast_sha256,
            }
            return self.stop_reason
        if self.max_attempts is not None and len(self._attempts) >= self.max_attempts:
            self.stop_reason = {"status": "rejected", "stage": "convergence",
                "code": "REPAIR_BUDGET_EXCEEDED", "category": "unverified",
                "reason": "Initial SQL and all allowed repairs were consumed; no new SQL/Reviewer/DB work is permitted.",
                "retryable": False, "attempts": len(self._attempts)}
            return self.stop_reason
        attempt = len(self._attempts) + 1
        self._attempts.append({"attempt": attempt, "status": "inflight",
                               "raw_sha256": raw_sha256, "sql_fingerprint": ast_sha256})
        self._raw_seen[raw_sha256] = attempt
        if ast_sha256 is not None:
            self._ast_seen[ast_sha256] = attempt
        return None

    def reject(self, query: str, stage: str, code: str, message: str,
               terminal: bool = False) -> dict:
        """Remember one real rejection; unsupported/unsafe callers set terminal."""
        if self.stop_reason is not None:
            return self.stop_reason
        previous, raw_sha256, ast_sha256 = self._existing(query)
        if previous is None:
            self.start(query)
            previous = len(self._attempts)
        attempt = self._attempts[previous - 1]
        attempt.update(status="rejected", rejection_stage=stage, rejection_code=code)
        category = (stage, code)
        self._rejection_streak = self._rejection_streak + 1 if category == self._last_rejection else 1
        self._last_rejection = category
        rejection = {
            "status": "rejected", "stage": stage, "code": code, "reason": message,
            "retryable": not terminal, "previous_attempt": None, "attempt": previous,
            "raw_sha256": raw_sha256, "sql_fingerprint": ast_sha256,
            "consecutive_rejections": self._rejection_streak,
        }
        if terminal:
            self.stop_reason = rejection
        elif self.max_attempts is not None and len(self._attempts) >= self.max_attempts:
            rejection.update(code="REPAIR_BUDGET_EXCEEDED", cause_code=code, category="unverified",
                             retryable=False, reason="At most two SQL repairs are allowed. Last rejection: " + message)
            self.stop_reason = rejection
        elif self._rejection_streak >= self.rejection_limit:
            rejection.update(
                code="REPEATED_REJECTION_CATEGORY", retryable=False,
                cause_code=code, cause_stage=stage,
                reason="Repeated rejection category without progress; different SQL is not asserted to be semantically equivalent. Last rejection: " + message,
            )
            self.stop_reason = rejection
        return rejection

    def success(self, query: str, rows=None) -> None:
        """Remember completion without retaining business data; reset failures."""
        if self.stop_reason is not None:
            return
        previous, _, _ = self._existing(query)
        if previous is None:
            self.start(query)
            previous = len(self._attempts)
        self._attempts[previous - 1]["status"] = "executed"
        self._last_rejection = None
        self._rejection_streak = 0
