"""Use the bundled stop gate to review evidence without starting a human turn."""
import json

try:
    from .native_verification import verification_failure_answer
except ImportError:  # Standalone worker import.
    from native_verification import verification_failure_answer


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
                '근거나 아티팩트의 수정이 필요하면 관련 도구로 근거 표를 다시 생성한 뒤 최종 답변을 제출하세요. '
                '검증 보고서의 result_ids를 read_result로 읽고 issues의 원본·선택·계산 불일치를 수정하세요. '
                '웨이퍼 맵 근거 표 문제라면 실제 원본에서 필수 열을 계산해 '
                "emit_table(manifest_df, name='wafer_map_manifest'), "
                "emit_table(members_df, name='wafer_map_members'), "
                "cummap은 emit_table(cells_df, name='wafer_map_cells')로 다시 저장하세요. "
                '위 DataFrame 변수는 예시이며 직접 생성해야 합니다. 표 이름만 바꾸어 잘못된 값을 숨기지 마세요. '
                '수정한 근거와 맵은 같은 run_python 호출에서 함께 다시 생성하세요. '
                '최종 답변 제출 후 같은 검사를 다시 수행하며 남은 실행 예산을 공유합니다. '
                '보완할 수 없는 사항은 최종 답변에 해당 한계와 확인하지 못한 내용을 명시하세요. '
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
        answer = result.get('final_response')
        if (complete and self.reports and self.reports[-1]['verdict'] in ('fail', 'unknown')
                and self.reviewed_answer == answer
                and isinstance(answer, str) and answer.strip()):
            report = self.reports[-1]
            return {**result, 'completed': False, 'partial': True,
                'final_response': verification_failure_answer(answer, report),
                'verification_candidate': answer,
                'turn_exit_reason': 'verification_' + report['verdict'], 'verification': self.reports}
        report = (self.reports[-1] if complete and self.reports
            and self.reviewed_answer == result.get('final_response', '') else
            {'verdict': 'unknown', 'issues': ['실행이 완료되지 않았거나 최종 답변이 검증한 답변과 다릅니다.'], 'result_ids': []})
        reports = self.reports if self.reports and report is self.reports[-1] else [*self.reports, report]
        return {**result, 'completed': False, 'partial': True,
            'final_response': verification_failure_answer(None, report),
            'turn_exit_reason': 'verification_' + report['verdict'], 'verification': reports}
