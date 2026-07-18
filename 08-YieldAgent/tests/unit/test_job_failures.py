import importlib

import httpx
import oracledb
import pytest

from job_failures import classify_failure


@pytest.mark.parametrize(
    "exc,retry",
    [
        (TimeoutError("upstream"), True),
        (ConnectionError("reset"), True),
        (httpx.ConnectError("upstream unavailable"), True),
        (FileNotFoundError("missing config"), False),
        (ValueError("bad planner output"), False),
        (TypeError("bug"), False),
    ],
)
def test_retry_classification(exc, retry):
    assert classify_failure(exc).retry is retry


def _oracle_error(code: int) -> oracledb.DatabaseError:
    error_type = importlib.import_module("oracledb.errors")._Error
    error = error_type(f"ORA-{code:05}: fixture", code=code)
    return error.exc_type(error)


def test_recoverable_oracle_error_retries():
    decision = classify_failure(_oracle_error(3113))
    assert decision.retry is True
    assert decision.category == "oracle"


def test_deterministic_oracle_error_does_not_retry():
    decision = classify_failure(_oracle_error(942))
    assert decision.retry is False
    assert decision.category == "oracle"


def test_failure_message_is_fixed_and_does_not_leak_exception_text():
    decision = classify_failure(RuntimeError("password=secret"))
    assert decision.message == "job execution failed"
    assert "secret" not in decision.message
