import { useAuiState } from '@assistant-ui/react'
import { useLayoutEffect, useRef, type RefObject } from 'react'
import type { StickToBottomState } from 'use-stick-to-bottom'
import { contentHasVisibleText } from '@/components/assistant-ui/thread/content'

interface Props {
  running: boolean
  viewportRef: RefObject<HTMLElement | null>
  follow: StickToBottomState
  parkAnswer: (viewport: HTMLElement, top: number) => void
  hasSelection: (viewport: Element) => boolean
}

/** Mounted only for the visible web transcript, keyed by session. */
export function CompletedAnswerScroll({ running, viewportRef, follow, parkAnswer, hasSelection }: Props) {
  const turn = useAuiState(s => s.thread.messages.findLast(message => message.role === 'user')?.id)
  const answer = useAuiState(s => {
    const tail = s.thread.messages.at(-1)
    if (tail?.role !== 'assistant' || tail.status.type !== 'complete' ||
        tail.metadata.custom?.interim || tail.metadata.custom?.interrupted) return null
    return contentHasVisibleText(tail.content) ? tail.id : null
  })
  const liveTurn = useRef<string | undefined>(undefined)

  useLayoutEffect(() => {
    if (running) {
      liveTurn.current = turn
      return
    }
    const viewport = viewportRef.current
    if (!viewport || !turn || liveTurn.current !== turn || !answer) return
    liveTurn.current = undefined
    if (!follow.isAtBottom || hasSelection(viewport)) return

    const root = viewport.querySelector<HTMLElement>(`[data-answer-id="${CSS.escape(answer)}"]`)
    const body = root?.querySelector<HTMLElement>('[data-slot="aui_assistant-message-content"]')
    if (!root || !body) return
    const prompt = root.closest('[data-slot="aui_turn-pair"]')?.querySelector<HTMLElement>('[data-slot="aui_user-message-root"]')
    const observer = new ResizeObserver(land)
    function land() {
      if (!viewport || !body || !follow.isAtBottom || hasSelection(viewport)) {
        observer.disconnect()
        return
      }
      const viewportTop = viewport.getBoundingClientRect().top
      const promptStyle = prompt && getComputedStyle(prompt)
      const stickyHeight = prompt && promptStyle?.position === 'sticky'
        ? prompt.getBoundingClientRect().height + (parseFloat(promptStyle.top) || 0) : 0
      const target = Math.max(0, viewport.scrollTop + body.getBoundingClientRect().top - viewportTop - stickyHeight - 16)
      // Keep following a short answer. An embedded report may still acquire its height.
      if (target >= viewport.scrollHeight - viewport.clientHeight - 1) return
      observer.disconnect()
      parkAnswer(viewport, target)
    }
    observer.observe(body)
    const frame = requestAnimationFrame(land)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
    }
  }, [running, turn, answer, viewportRef, follow, parkAnswer, hasSelection])
  return null
}
