import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


def model_tool_names():
    from harness.tools.registry import domain_registry

    return [tool["function"]["name"] for tool in domain_registry().model_tools()]


def test_domain_tool_registry_exposes_yield_handlers():
    from harness.tools.registry import domain_registry

    registry = domain_registry()
    assert model_tool_names() == [
        "read_result", "run_python", "query_db", "list_tables", "load_wafer_maps", "render_yield_table",
    ]
    assert registry.tools["read_result"].handler.__module__ == "harness.tools.python_tools"
    assert registry.tools["run_python"].handler.__module__ == "harness.tools.python_tools"
    assert registry.tools["query_db"].handler.__module__ == "harness.tools.query_db"
    assert registry.tools["query_db"].read_only is True
    assert registry.tools["query_db"].effect == "artifact"
    assert registry.tools["query_db"].eager is True
    assert registry.tools["list_tables"].handler.__module__ == "harness.tools.list_tables"
    assert registry.tools["list_tables"].read_only is True
    assert registry.tools["list_tables"].effect == "read"
    assert registry.tools["list_tables"].eager is True


def test_lot_history_is_a_read_only_native_domain_tool():
    from harness.tools.registry import domain_registry, native_domain_registry

    native = native_domain_registry()
    assert set(native.tools) - set(domain_registry().tools) == {"query_lot_history"}
    assert native.tools["query_lot_history"].read_only
    assert any(tool["function"]["name"] == "query_lot_history" for tool in native.model_tools())


def test_engine_session_search_reads_profile_state_db(tmp_path):
    engine = Path(__file__).parents[1] / "engine"
    profile = tmp_path / "profile"
    script = f"""
import json
import sys
from pathlib import Path
sys.path.insert(0, {str(engine)!r})
from hermes_state import SessionDB
from tools.session_search_tool import session_search
home = Path({str(profile)!r})
home.mkdir(parents=True, exist_ok=True)
db = SessionDB(db_path=home / 'state.db')
db.create_session('old-session', 'cli', model='test')
db.append_message('old-session', 'user', 'needle-pr65 historical question')
db.append_message('old-session', 'assistant', 'historical answer')
db.close()
print(session_search(query='needle-pr65'))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(profile)},
        text=True, capture_output=True, check=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert payload["results"][0]["session_id"] == "old-session"
    assert payload["results"][0]["messages"][0]["content"] == "needle-pr65 historical question"
    assert (profile / "state.db").is_file()


def test_host_model_tools_payload_log_records_bound_names(tmp_path):
    from harness.executor import log_model_tools_payload

    output = tmp_path / "host-tools.jsonl"
    model = SimpleNamespace(kwargs={"tools": [
        {"type": "function", "function": {"name": "finish"}},
        {"type": "function", "name": "web_search"},
    ]})
    log_model_tools_payload(output, model, request_id="run:reasoning:1")

    assert json.loads(output.read_text()) == {
        "api_request_id": "run:reasoning:1", "tools": ["finish", "web_search"]
    }
