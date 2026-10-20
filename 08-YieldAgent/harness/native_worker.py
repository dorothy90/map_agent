"""JSON-lines transport only. Reasoning and tool orchestration are upstream Hermes."""
import json
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
    try:
        from native_verification import REVIEW_SCHEMA, checked_report, unknown, verify_and_repair, final_messages
        from tools.registry import registry
        for tool in startup['tools']:
            schema = tool['function']
            registry.register(name=schema['name'], toolset='yield_evidence' if schema['name'] == 'read_result' else 'yield_backend', schema=schema,
                handler=handler(schema['name']), description=schema['description'])
        from run_agent import AIAgent
        from gateway.session_context import declare_stateless_channel
        # This worker exits after its response; delegated results must arrive before then.
        declare_stateless_channel()
        agent = None
        agents = []
        def usage():
            return {key: sum(getattr(a, attr, 0) or 0 for a in agents) for key, attr in
                [('tokens', 'session_total_tokens'), ('models', 'session_api_calls'),
                 ('input_tokens', 'session_input_tokens'), ('output_tokens', 'session_output_tokens')]}
        def step(count, previous):
            used = usage()
            emit({'type': 'step', 'count': count, **used})
            if used['tokens'] >= startup['token_limit'] or used['models'] >= startup['max_iterations'] or time.monotonic() >= deadline:
                for active in agents:
                    active.interrupt()
        agent = AIAgent(model=startup['model'], base_url=startup['base_url'], api_key=startup['api_key'],
            enabled_toolsets=['yield_backend', 'yield_evidence', 'skills', 'memory', 'delegation'],
            max_iterations=startup['max_iterations'], max_tokens=startup['max_tokens'],
            run_budget_seconds=startup['run_budget_seconds'], session_id=startup['session_id'],
            quiet_mode=True, skip_context_files=False, load_soul_identity=False,
            step_callback=step, interim_assistant_callback=lambda content: emit({'type': 'commentary', 'content': content}))
        agents.append(agent)
        result = agent.run_conversation(startup['query'], system_message=startup['instructions'],
            conversation_history=startup['history'])
        from tools.delegate_tool import _build_child_preserving_parent_tools, _run_single_child
        from tools.delegation_output_schema import extract_json_candidate
        review_attempt = 0
        repair_prompts = []
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
            rpc('verification_finished', review_id=current_id, report=report, elapsed=time.monotonic() - started)
            step(0, None)
            return report
        def repair(candidate, report):
            remaining = startup['max_iterations'] - usage()['models']
            if remaining < 1 or time.monotonic() >= deadline or usage()['tokens'] >= startup['token_limit']:
                return {**candidate, 'completed': False, 'partial': True}
            emit({'type': 'commentary', 'content': '검증에서 확인한 문제를 보완하고 결과를 다시 확인하겠습니다.'})
            prompt = '원래 사용자 요청에 대한 독립 검증 결과입니다. 사용자 조건을 변경하지 말고 지적된 근거를 확인하여 한 번 보완하세요. 해결할 수 없는 항목은 명시하세요.\n' + json.dumps(report, ensure_ascii=False)
            repair_prompts.append(prompt)
            agent.max_iterations = remaining
            agent.run_budget_seconds = max(.1, deadline - time.monotonic())
            return agent.run_conversation(prompt, system_message=startup['instructions'], conversation_history=candidate.get('messages', []))
        result = verify_and_repair(result, review, repair)
        # Internal reviewer feedback is not a user turn in future conversations.
        messages = final_messages(result.get('messages', []), repair_prompts, result['final_response'])
        totals = usage()
        result.update(messages=messages, input_tokens=totals['input_tokens'], output_tokens=totals['output_tokens'],
            total_tokens=totals['tokens'], api_calls=totals['models'])
        emit({'type': 'result', 'result': result})
        agent.close()
    except Exception as exc:
        message = str(exc).replace(startup.get('api_key') or 'UNUSED_SECRET', '[redacted]')
        emit({'type': 'error', 'message': type(exc).__name__ + ': ' + message[:1000]})


if __name__ == '__main__':
    main()
