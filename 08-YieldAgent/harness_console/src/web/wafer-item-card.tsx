import type { ThreadMessage } from '@assistant-ui/react'

type Group = { lot_id: string; wf_ids: number[] }
type Item = { id: string; title: string; wafer_count: number; wafer_groups: Group[]; added_wafer_groups: Group[] }

function groups(value: unknown): value is Group[] {
  return Array.isArray(value) && value.every(group => group && typeof group.lot_id === 'string' &&
    Array.isArray(group.wf_ids) && group.wf_ids.every(Number.isSafeInteger))
}

function savedItem(result: unknown): Item | undefined {
  if (typeof result !== 'object' || !result || !('wafer_item' in result) || !('status' in result) || result.status !== 'success') return
  const item = result.wafer_item as Partial<Item> | null
  if (item && typeof item.id === 'string' && typeof item.title === 'string' &&
      groups(item.wafer_groups) && groups(item.added_wafer_groups) &&
      item.wafer_count === item.wafer_groups.reduce((total, group) => total + group.wf_ids.length, 0)) return item as Item
}

export function WaferItemCards({ messages, indices }: { messages: readonly ThreadMessage[]; indices: readonly number[] }) {
  const saved = new Map<string, { item: Item; appended: boolean }>()
  for (const i of indices) {
    const message = messages[i]
    if (message?.role !== 'assistant') continue
    for (const part of message.content) {
      if (part.type !== 'tool-call' || (part.toolName !== 'kanban_create' && part.toolName !== 'kanban_comment')) continue
      const item = savedItem(part.result)
      if (item) saved.set(item.id, { item, appended: part.toolName === 'kanban_comment' })
    }
  }
  return [...saved.values()].map(({ item, appended }) => {
    const added = item.added_wafer_groups.reduce((total, group) => total + group.wf_ids.length, 0)
    return <section key={item.id} className="wafer-item-card" role="group" aria-label={item.title}>
      <header><strong>{item.title}</strong><span>총 {item.wafer_count}장</span></header>
      {appended && <p>기존 {item.wafer_count - added}장 + 추가 {added}장</p>}
      <dl>{item.wafer_groups.map(group => <div key={group.lot_id}>
        <dt>{group.lot_id}</dt>
        <dd>{group.wf_ids.map(wf => 'W' + String(wf).padStart(2, '0')).join(' · ')}</dd>
      </div>)}</dl>
    </section>
  })
}
