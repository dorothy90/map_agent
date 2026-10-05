import asyncio


def test_python_computes_from_all_rows():
    from harness.runtime.container import ContainerRuntime
    runtime = ContainerRuntime()
    result = asyncio.run(runtime.execute("print(len(df)); emit_table(df.agg({'n': 'sum'}).to_frame().T)", {"r1": [{"n": i} for i in range(120)]}))
    assert result["status"] == "success"
    assert result["tables"][0]["rows"][0]["n"] == 7140
    assert "120" in result["stdout"]


def test_python_timeout_is_reported():
    from harness.runtime.container import ContainerRuntime
    runtime = ContainerRuntime()
    result = asyncio.run(runtime.execute("while True: pass", {}, timeout=1))
    assert result["status"] == "timeout"


def test_container_cannot_read_host_secrets_or_connect_to_network():
    from harness.runtime.container import ContainerRuntime
    code = '''
import os, socket
assert not os.path.exists('/Users/daehwankim/yield-agent/.env')
assert 'OPENROUTER_API_KEY' not in os.environ
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    print('network denied')
else:
    raise AssertionError('network was accessible')
try:
    open('/input/code.py', 'w')
except OSError:
    print('input readonly')
else:
    raise AssertionError('input was writable')
'''
    result = asyncio.run(ContainerRuntime().execute(code, {}))
    assert result["status"] == "success"
    assert "network denied" in result["stdout"]
    assert "input readonly" in result["stdout"]
