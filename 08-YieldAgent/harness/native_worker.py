"""JSON-lines transport only. Reasoning and tool orchestration are upstream Hermes."""
import json
import sys
import threading
import uuid
from queue import Queue
from pathlib import Path

# Both projects contain a top-level tools package. Resolve the pinned engine's
# packages before this adapter's directory in the separate interpreter.
sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))


def main():
    startup = json.loads(sys.stdin.readline())
    output = sys.stdout
    sys.stdout = sys.stderr  # Upstream diagnostic output must not corrupt RPC.
    lock = threading.Lock()
    pending = {}
    def emit(message):
        with lock:
            output.write(json.dumps(message, ensure_ascii=False, default=str) + '\n')
            output.flush()
    def receive():
        for line in sys.stdin:
            item = json.loads(line)
            with lock:
                queue = pending.get(item['id'])
            if queue is not None:
                queue.put(item['result'])
        with lock:
            for queue in pending.values():
                queue.put({'error': 'Backend connection closed'})
    threading.Thread(target=receive, daemon=True).start()
    def handler(name):
        def call(args, **kwargs):
            identity, queue = str(uuid.uuid4()), Queue()
            with lock:
                pending[identity] = queue
            try:
                emit({'type': 'tool_call', 'id': identity, 'name': name, 'args': args})
                return json.dumps(queue.get(timeout=startup['run_budget_seconds']), ensure_ascii=False)
            finally:
                with lock:
                    pending.pop(identity, None)
        return call
    try:
        from tools.registry import registry
        for tool in startup['tools']:
            schema = tool['function']
            registry.register(name=schema['name'], toolset='yield_backend', schema=schema,
                handler=handler(schema['name']), description=schema['description'])
        from run_agent import AIAgent
        agent = None
        def step(count, previous):
            emit({'type': 'step', 'count': count,
                'tokens': getattr(agent, 'session_total_tokens', 0),
                'models': getattr(agent, 'session_api_calls', 0)})
            if agent is not None and getattr(agent, 'session_total_tokens', 0) >= startup['token_limit']:
                agent.interrupt()
        agent = AIAgent(model=startup['model'], base_url=startup['base_url'], api_key=startup['api_key'],
            enabled_toolsets=['yield_backend', 'skills', 'memory'],
            max_iterations=startup['max_iterations'], max_tokens=startup['max_tokens'],
            run_budget_seconds=startup['run_budget_seconds'], session_id=startup['session_id'],
            quiet_mode=True, skip_context_files=False, load_soul_identity=False,
            step_callback=step, interim_assistant_callback=lambda content: emit({'type': 'commentary', 'content': content}))
        result = agent.run_conversation(startup['query'], system_message=startup['instructions'],
            conversation_history=startup['history'])
        result.update(input_tokens=agent.session_input_tokens, output_tokens=agent.session_output_tokens,
            total_tokens=agent.session_total_tokens)
        emit({'type': 'result', 'result': result})
        agent.close()
    except Exception as exc:
        message = str(exc).replace(startup.get('api_key') or 'UNUSED_SECRET', '[redacted]')
        emit({'type': 'error', 'message': type(exc).__name__ + ': ' + message[:1000]})


if __name__ == '__main__':
    main()
