from __future__ import annotations

from dataclasses import dataclass

import oracledb

from common import is_transient_error


@dataclass(frozen=True)
class FailureDecision:
    retry: bool
    category: str
    message: str = "job execution failed"


def _oracle_error(exc: BaseException):
    if not isinstance(exc, oracledb.DatabaseError) or not exc.args:
        return None
    error = exc.args[0]
    if not isinstance(getattr(error, "code", None), int):
        return None
    return error


def classify_failure(exc: Exception) -> FailureDecision:
    """Classify by exception type and driver metadata, never localized text."""
    oracle_error = _oracle_error(exc)
    if oracle_error is not None:
        return FailureDecision(
            retry=bool(getattr(oracle_error, "isrecoverable", False)),
            category="oracle",
        )
    if is_transient_error(exc):
        return FailureDecision(retry=True, category="infrastructure")
    return FailureDecision(retry=False, category="worker")
