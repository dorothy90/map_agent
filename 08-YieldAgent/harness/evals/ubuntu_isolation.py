"""Run the employee isolation acceptance test with the Ubuntu service configuration."""
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

from harness.config import LocalIsolationSettings


def main():
    if sys.platform != "linux":
        raise SystemExit("Run this check on the Ubuntu deployment host.")
    if os.geteuid() == 0:
        raise SystemExit("Run this check as the non-root harness service account.")
    load_dotenv()
    if os.getenv("HARNESS_ISOLATION_BACKEND", "local") != "local":
        raise SystemExit("The Ubuntu service must use the local isolation backend for this check.")
    config = LocalIsolationSettings.from_env()
    os.environ["YIELD_TEST_ANALYSIS_VENV"] = str(config.analysis_venv)
    path = Path(__file__).resolve().parents[1] / "tests" / "test_linux_local_transport.py"
    outcomes = []

    class Result:
        def pytest_runtest_logreport(self, report):
            outcomes.append((report.when, report.outcome))

    status = pytest.main(["-q", "-rs", f"{path}::test_actual_local_analysis_scope"], plugins=[Result()])
    if status:
        raise SystemExit(status)
    if outcomes != [("setup", "passed"), ("call", "passed"), ("teardown", "passed")]:
        raise SystemExit("Employee isolation was not verified. One passing test is required.")


if __name__ == "__main__":
    main()
