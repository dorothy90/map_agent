"""JSON-lines transport only. Reasoning and tool orchestration are upstream Hermes."""
import json
import os
import sys
import threading
import uuid
import time
from queue import Queue
from pathlib import Path

# Both projects contain a top-level tools package. Resolve the pinned engine's
# packages before this adapter's directory in the separate interpreter.
sys.path.insert(0, str(Path(__file__).resolve().with_name('engine')))
sys.path.append(str(Path(__file__).resolve().parent))


def main():
    startup = json.loads(sys.stdin.readline())
    output = sys.stdout
    sys.stdout = sys.stderr  # Upstream diagnostic output must not corrupt RPC.
    lock = threading.Lock()
    pending = {}
    deadline = time.monotonic() + startup['run_budget_seconds']
    review_id = None
    review_known_ids = set()
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
    def rpc(kind, **payload):
        identity, queue = str(uuid.uuid4()), Queue()
        with lock:
            pending[identity] = queue
        try:
            emit({'type': kind, 'id': identity, **payload})
            return queue.get(timeout=max(.1, deadline - time.monotonic()))
        finally:
            with lock:
                pending.pop(identity, None)
    def handler(name):
        def call(args, **kwargs):
            if review_id and name != 'read_result':
                return json.dumps({'error': 'Verifier can only read stored results'})
            result = rpc('tool_call', name=name, args=args, review_id=review_id)
            if review_id and result.get('status') in ('success', 'partial', 'empty'):
                review_known_ids.add(result['result_id'])
                review_known_ids.update(result.get('source_result_ids', []))
            return json.dumps(result, ensure_ascii=False)
        return call
    budget = None
    restore_gate = None
    agents = []
    try:
        from native_verification import REVIEW_SCHEMA, checked_report, unknown, final_messages
        from native_budget import install_http_budget
        from native_stop_gate import NativeVerificationGate
        def budget_update(snapshot):
            emit({'type': 'step', 'count': 0, **snapshot})
            if budget is not None and budget.exhausted_reason:
                for active in agents:
                    active.interrupt()
        budget = install_http_budget(model_limit=startup['max_iterations'],
            token_limit=startup['token_limit'], max_output_tokens=startup['max_tokens'],
            base_url=startup['base_url'], on_usage=budget_update, call_timeout=startup['call_timeout'])
        os.environ['HERMES_API_TIMEOUT'] = str(startup['call_timeout'])
        import hermes_constants
        hermes_constants.get_skills_dir = lambda: Path(startup['skills_path'])
        from tools import skills_tool
        skills_tool.SKILLS_DIR = Path(startup['skills_path'])
        from tools.registry import registry
        for tool in startup['tools']:
            schema = tool['function']
            registry.register(name=schema['name'], toolset='yield_evidence' if schema['name'] == 'read_result' else 'yield_backend', schema=schema,
                handler=handler(schema['name']), description=schema['description'])
        from run_agent import AIAgent
        from gateway.session_context import declare_stateless_channel
        # This worker exits after its response; delegated results must arrive before then.
        declare_stateless_channel()
        def usage():
            return budget.snapshot()
        def step(count, previous):
            with budget.lock:
                used = usage()
                emit({'type': 'step', 'count': count, **used})
            if budget.exhausted_reason or time.monotonic() >= deadline:
                for active in agents:
                    active.interrupt()
        agent = AIAgent(model=startup['model'], base_url=startup['base_url'], api_key=startup['api_key'],
            enabled_toolsets=['yield_backend', 'yield_evidence', 'skills', 'memory', 'delegation'],
            max_iterations=startup['max_iterations'], max_tokens=startup['max_tokens'],
            run_budget_seconds=startup['run_budget_seconds'], session_id=startup['session_id'],
            reasoning_config={'enabled': True, 'effort': startup['reasoning_effort']} if startup.get('reasoning_effort') else None,
            request_overrides={'temperature': startup['temperature']},
            quiet_mode=True, skip_context_files=False, load_soul_identity=False,
            step_callback=step, interim_assistant_callback=lambda content: emit({'type': 'commentary', 'content': content}))
        agents.append(agent)
        from tools.delegate_tool import _build_child_preserving_parent_tools, _run_single_child
        from tools.delegation_output_schema import extract_json_candidate
        review_attempt = 0
        def review(candidate):
            nonlocal review_id, review_attempt
            remaining = startup['max_iterations'] - usage()['models']
            if remaining < 1 or time.monotonic() >= deadline or usage()['tokens'] >= startup['token_limit']:
                return unknown('검증에 필요한 실행 예산이 남아 있지 않습니다.')
            review_attempt += 1
            review_id = 'verify-' + uuid.uuid4().hex
            review_known_ids.clear()
            started = time.monotonic()
            try:
                bundle = rpc('verification_start', review_id=review_id, candidate=candidate.get('final_response', ''), attempt=review_attempt)
                if bundle.get('error'):
                    report = unknown('저장된 검증 근거를 불러오지 못했습니다.')
                else:
                    goal = startup['verification_instructions'] + '\n\n출력 JSON schema:\n' + json.dumps(REVIEW_SCHEMA) + '\n\n검토 자료(JSON):\n' + json.dumps(bundle, ensure_ascii=False)
                    child = _build_child_preserving_parent_tools(task_index=review_attempt, goal=goal,
                        context=None, toolsets=['yield_evidence'], model=None,
                        max_iterations=min(6, remaining), task_count=1, parent_agent=agent)
                    child.run_budget_seconds = max(.1, deadline - time.monotonic())
                    child.max_tokens = startup['max_tokens']
                    child.step_callback = step
                    child._delegate_output_schema = REVIEW_SCHEMA
                    agents.append(child)
                    entry = _run_single_child(review_attempt, goal, child=child, parent_agent=agent)
                    # Use the same JSON envelope parsing as Hermes' schema check.
                    entry['summary'] = extract_json_candidate(entry.get('summary') or '')
                    report = checked_report(entry, set(bundle['known_result_ids']) | review_known_ids)
            except Exception:
                report = unknown('검증 에이전트 실행 중 오류가 발생했습니다.')
            finally:
                current_id, review_id = review_id, None
            if budget.exhausted_reason:
                report = unknown('공유 실행 예산에서 추가 모델 호출이 차단되었습니다: ' + budget.exhausted_reason)
            rpc('verification_finished', review_id=current_id, report=report, elapsed=time.monotonic() - started)
            step(0, None)
            return report
        gate = NativeVerificationGate(review, lambda: emit({'type': 'commentary',
            'content': '검증에서 확인한 문제를 보완하고 결과를 다시 확인하겠습니다.'}))
        restore_gate = gate.install(agent)
        result = agent.run_conversation(startup['query'], system_message=startup['instructions'],
            conversation_history=startup['history'])
        result = gate.finish(result)
        if budget.exhausted_reason:
            result.update(completed=False, partial=True, turn_exit_reason=budget.exhausted_reason)
        messages = final_messages(result.get('messages', []), [], result['final_response'],
            turn_id=result.get('turn_id'), current_turn_user_idx=result.get('current_turn_user_idx'),
            conversation_history=startup['history'], user_message=startup['query'])
        totals = usage()
        result.update(messages=messages, input_tokens=totals['input_tokens'], output_tokens=totals['output_tokens'],
            total_tokens=totals['tokens'], api_calls=totals['models'])
        emit({'type': 'result', 'result': result})
    except Exception as exc:
        message = str(exc).replace(startup.get('api_key') or 'UNUSED_SECRET', '[redacted]')
        emit({'type': 'error', 'message': type(exc).__name__ + ': ' + message[:1000]})
    finally:
        if restore_gate:
            restore_gate()
        for active in agents:
            active.close()
        # Keep HTTP admission active until this dedicated interpreter exits.


if __name__ == '__main__':
    main()
