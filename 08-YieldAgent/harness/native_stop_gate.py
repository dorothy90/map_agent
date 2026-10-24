"""Use the bundled stop gate to review evidence without starting a human turn."""
import json


class NativeVerificationGate:
    def __init__(self, review, notify=None):
        self.review = review
        self.notify = notify or (lambda: None)
        self.reports = []
        self.reviewed_answer = None

    def evaluate(self, answer):
        self.reviewed_answer = answer
        report = self.review({'completed': True, 'final_response': answer})
        self.reports.append(report)
        if report['verdict'] == 'fail' and len(self.reports) == 1:
            self.notify()
            return ('내부 자동 검증 결과입니다. 새 사용자 요청이나 사용자 정정이 아닙니다. '
                '원래 사용자 요청과 근거를 대조해 답변을 한 번 보완하세요. '
                '최종 답변은 원래 요청에 대한 완결된 답변으로 작성하고 내부 검증자에게 답하지 마세요.\n'
                + json.dumps(report, ensure_ascii=False))
        return None

    def install(self, root_agent):
        # This interpreter owns one run. Keep the vendored source byte-identical;
        # extend its text-stop boundary only for this root, never review children.
        from agent import turn_final_response
        from agent.message_metadata import append_message
        from agent.turn_stop_gates import StopGateVerdict
        original = turn_final_response.apply_stop_gates

        def apply(agent, final_msg, **state):
            verdict = original(agent, final_msg, **state)
            if agent is not root_agent or verdict.continue_turn:
                return verdict
            nudge = self.evaluate(state['final_response'])
            if not nudge:
                return verdict
            messages = state['messages']
            append_message(messages, {**final_msg, '_pre_verify_synthetic': True})
            # Tail anchoring and auto-focus also inspect display_kind; the
            # synthetic flag alone is only recognized by the stronger intent predicate.
            append_message(messages, {'role': 'user', 'content': nudge,
                '_pre_verify_synthetic': True, 'display_kind': 'hidden'})
            agent._session_messages = messages
            return StopGateVerdict(continue_turn=True, final_response=None,
                pending_verification_response=state['final_response'],
                pending_verification_response_previewed=False)

        turn_final_response.apply_stop_gates = apply
        return lambda: setattr(turn_final_response, 'apply_stop_gates', original)

    def finish(self, result):
        complete = result.get('completed') and not any(result.get(k) for k in ('failed', 'partial', 'interrupted', 'error'))
        if complete and not self.reports:
            # An upstream early return may bypass text-stop gates. Review once,
            # but never restart the human turn to repair such a return.
            self.evaluate(result.get('final_response', ''))
        if (complete and self.reports and self.reports[-1]['verdict'] == 'pass'
                and self.reviewed_answer == result.get('final_response', '')):
            return {**result, 'verification': self.reports}
        report = (self.reports[-1] if complete and self.reports
            and self.reviewed_answer == result.get('final_response', '') else
            {'verdict': 'unknown', 'issues': ['실행이 완료되지 않았거나 최종 답변이 검증한 답변과 다릅니다.'], 'result_ids': []})
        reports = self.reports if self.reports and report is self.reports[-1] else [*self.reports, report]
        reason = '\n'.join('- ' + issue for issue in report['issues'])
        return {**result, 'completed': False, 'partial': True,
            'final_response': '최종 검증을 통과하지 못해 답변을 확정하지 못했습니다. 확인된 조회·계산 자료는 아래에 제공됩니다.\n\n' + reason,
            'turn_exit_reason': 'verification_' + report['verdict'], 'verification': reports}
