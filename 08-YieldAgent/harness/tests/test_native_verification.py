import json
from harness.native_verification import checked_report, verify_and_repair


def entry(verdict='pass', issues=None, ids=None):
    return {'status': 'completed', 'exit_reason': 'completed', 'summary': json.dumps({'verdict': verdict, 'issues': issues or [], 'result_ids': ids or []})}


def test_invalid_or_unfinished_verifier_never_passes():
    for item in [{**entry(), 'truncated': True}, {**entry(), 'status': 'failed'}, {'summary':'looks good'}, entry(ids=['foreign']), entry('pass',['wrong date'])]:
        assert checked_report(item, {'owned'})['verdict'] == 'unknown'
    assert checked_report(entry(ids=['owned']), {'owned'})['verdict'] == 'pass'


def test_one_repair_then_recheck_and_no_unverified_success():
    reviews, repairs = [], []
    def review(candidate):
        reviews.append(candidate['final_response'])
        return {'verdict':'fail','issues':['범위 불일치'],'result_ids':['r']}
    def repair(candidate, report):
        repairs.append(report)
        return {'completed':True,'final_response':'수정한 답변'}
    out = verify_and_repair({'completed':True,'final_response':'초안'}, review, repair)
    assert len(reviews) == 2 and len(repairs) == 1
    assert out['partial'] and not out['completed']
    assert '범위 불일치' in out['final_response']
    assert '수정한 답변' not in out['final_response']


def test_pass_never_repairs_and_unknown_never_passes():
    def forbidden(*a): raise AssertionError('unexpected repair')
    good = verify_and_repair({'completed':True,'final_response':'근거 있는 답변'}, lambda c: {'verdict':'pass','issues':[],'result_ids':[]}, forbidden)
    assert good['completed'] and good['final_response'] == '근거 있는 답변'
    unknown = verify_and_repair({'completed':True,'final_response':'초안'}, lambda c: {'verdict':'unknown','issues':['자료 부족'],'result_ids':[]}, forbidden)
    assert unknown['partial'] and not unknown['completed']


def test_internal_repair_and_rejected_draft_do_not_become_user_history():
    from harness.native_verification import final_messages
    messages = [{'role':'user','content':'question'}, {'role':'assistant','content':'wrong draft'},
        {'role':'user','content':'internal review'}, {'role':'assistant','content':'corrected'}]
    assert final_messages(messages, ['internal review'], 'verified') == [
        {'role':'user','content':'question'}, {'role':'assistant','content':'verified'}]


def test_verifier_backend_capability_is_read_result_only(monkeypatch):
    import asyncio
    import pytest
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.executor import ExecutionContext
    from harness.tools.registry import ToolRegistry, ToolSpec
    from harness.types import Contract, ToolObservation
    registry = ToolRegistry()
    for name, effect in [('read_result','read'), ('query_yield','read'), ('run_python','artifact')]:
        registry.add(ToolSpec(name, name, Contract, lambda *a: None, effect=effect))
    contexts = []
    class Executor:
        def __init__(self, registry, context):
            contexts.append(context)
        async def execute(self, call):
            return ToolObservation(principal_id='p',session_id='s',run_id='r',invocation_id=call['id'],
                result_id='review-read',tool_name=call['name'],status='success')
    monkeypatch.setattr(native, 'ToolExecutor', Executor)
    context = ExecutionContext(None, Settings(), {'principal_id':'p','session_id':'s','run_id':'r'})
    bridge = native.NativeBridge(registry, context)
    for name in ('query_yield', 'run_python', 'memory', 'delegate_task'):
        with pytest.raises(PermissionError):
            asyncio.run(bridge.invoke({'id':'i','name':name,'args':{}}, review_id='review'))
    asyncio.run(bridge.invoke({'id':'i','name':'read_result','args':{}}, review_id='review'))
    assert contexts[-1].run is context.run
    assert contexts[-1].parent_invocation_id == 'review'
    assert not bridge.observations
    assert bridge.review_observations[0]['result_id'] == 'review-read'
