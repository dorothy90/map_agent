export type VerificationState = 'none' | 'pending' | 'pass' | 'fail' | 'unknown'
const labels = { none: '검증 정보 없음', pending: '검증 중 — 확정 답변 대기', pass: '검증 통과', fail: '검증 실패 — 공개된 답변 없음', unknown: '검증 결과 미확정' }
export function YieldVerify({ state, revision, summary }: { state: VerificationState; revision?: number; summary?: string }) {
  return <section role="status" data-verification={state}><strong>{labels[state]}</strong>{revision !== undefined && <span> · 리비전 {revision}</span>}{summary && <p>{summary}</p>}</section>
}
