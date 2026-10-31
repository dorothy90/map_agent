import { cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { ContextUsagePanel } from '../hermes/app/shell/context-usage-panel'
import { useContextBreakdown } from '../hermes/app/shell/hooks/use-context-breakdown'
import { ClarifyToolPending } from '../hermes/components/assistant-ui/clarify/pending'
import { HermesGateway } from '../hermes/hermes'
import { I18nProvider, useI18n } from '../hermes/i18n'
import { installWebApiBridge } from '../hermes/platform-bridge'
import { $gateway } from '../hermes/store/gateway'

let releaseHost: (() => void) | undefined

afterEach(() => {
  cleanup()
  $gateway.set(null)
  releaseHost?.()
  releaseHost = undefined
  vi.restoreAllMocks()
})

it('mounts the original locale provider without automatic config REST', () => {
  const api = vi.fn(async (): Promise<never> => { throw new Error('Unexpected REST') })
  releaseHost = installWebApiBridge({ api })
  function Locale() { return <span>{useI18n().locale}</span> }
  render(<I18nProvider initialLocale="ja"><Locale /></I18nProvider>)
  expect(screen.getByText('ja')).toBeTruthy()
  expect(api).not.toHaveBeenCalled()
})

it('does not poll an unimplemented context breakdown on mount or after a turn', () => {
  const requestGateway = vi.fn(async (): Promise<never> => { throw new Error('Unexpected RPC') })
  const { result, rerender } = renderHook(({ busy }) => useContextBreakdown({
    busy, enabled: true, requestGateway, sessionId: 'session-a'
  }), { initialProps: { busy: true } })
  rerender({ busy: false })
  expect(requestGateway).not.toHaveBeenCalled()
  expect(result.current.loading).toBe(false)
})

it('renders received context usage without requesting an estimate', () => {
  const { container } = render(<ContextUsagePanel breakdown={null} loading={false} usage={{
    calls: 1, input: 1200, output: 200, total: 1400,
    context_max: 10000, context_used: 1200, context_percent: 12
  }} />)
  expect(container.textContent).toContain('1.2k')
  expect(container.textContent).toContain('10k')
  expect(container.textContent).toContain('12%')
})

it('submits a choice through the original clarify card only after user confirmation', async () => {
  const gateway = new HermesGateway()
  const request = vi.spyOn(gateway, 'request').mockResolvedValue({ status: 'ok' })
  $gateway.set(gateway)
  const answered = vi.fn()
  const { container } = render(<ClarifyToolPending onAnswered={answered} undelivered={false} request={{
    requestId: 'clarify-a', sessionId: null,
    questions: [{ qid: 'question-a', question: 'Choose a period', choices: ['Weekly', 'Monthly'], multiSelect: false }]
  }} />)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Weekly/ }))
  expect(request).not.toHaveBeenCalled()
  fireEvent.submit(container.querySelector('form')!)
  await waitFor(() => expect(answered).toHaveBeenCalledOnce())
  expect(request).toHaveBeenCalledWith('clarify.lock', {
    answer: 'Weekly', question_id: 'question-a', request_id: 'clarify-a'
  })
})
