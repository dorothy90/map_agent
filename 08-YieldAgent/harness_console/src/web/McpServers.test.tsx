import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { McpComposerButton, McpServersPage } from './McpServers'

const container = { current: null }
afterEach(cleanup)
function setup(popover = false) {
  let servers = [
    { name: 'local', command: 'python', args: ['server.py'], env: {}, enabled: true, transport: 'stdio' },
    { name: 'off', command: 'node', args: [], env: {}, enabled: false, transport: 'stdio' },
    { name: 'broken', command: 'missing', args: [], env: {}, enabled: true, transport: 'stdio' },
  ]
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = String(input), method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : null
    let result: unknown = {}
    if (url.endsWith('/test')) result = url.includes('/broken/')
      ? { ok: false, error: '명령을 찾을 수 없어요.', tools: [], elapsed_ms: 10 }
      : { ok: true, tools: [{ name: 'mcp__local__ping', description: 'Ping tool' }], elapsed_ms: 25 }
    else if (method === 'GET') result = { servers }
    else if (method === 'POST') { servers = [...servers, { ...body, transport: 'stdio' }]; result = body }
    else if (method === 'DELETE') servers = servers.filter(s => !url.endsWith('/' + s.name))
    else if (url.endsWith('/enabled')) servers = servers.map(s => url.includes('/' + s.name + '/') ? { ...s, ...body } : s)
    return new Response(JSON.stringify(result), { status: 200 })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const onManage = vi.fn()
  render(<QueryClientProvider client={client}>{popover
    ? <McpComposerButton fetcher={fetcher} container={container} onManage={onManage}/>
    : <McpServersPage fetcher={fetcher} container={container}/>}</QueryClientProvider>)
  return { fetcher, onManage, client }
}

it('renders real cards, counts, filtering, search and enabled mutations', async () => {
  const { fetcher } = setup()
  await screen.findByRole('heading', { name: 'local' })
  await screen.findByText('명령을 찾을 수 없어요.')
  expect(screen.getByRole('button', { name: '전체 3' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '오류 1' }))
  expect(screen.queryByRole('heading', { name: 'local' })).toBeNull()
  expect(screen.getByRole('heading', { name: 'broken' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '전체 3' }))
  fireEvent.change(screen.getByRole('searchbox', { name: '서버 검색' }), { target: { value: 'local' } })
  expect(screen.queryByRole('heading', { name: 'off' })).toBeNull()
  fireEvent.click(screen.getByRole('switch', { name: 'local 사용' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/harness/mcp/servers/local/enabled', expect.objectContaining({ method: 'PUT', body: '{"enabled":false}' })))
})

it('adds a draft with args and env, tests it and displays discovered full tool names', async () => {
  const { fetcher } = setup()
  fireEvent.click(screen.getByRole('button', { name: '+ 서버 추가' }))
  const dialog = screen.getByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('이름'), { target: { value: 'new' } })
  fireEvent.change(within(dialog).getByLabelText('명령'), { target: { value: 'uvx' } })
  const args = within(dialog).getByLabelText('인자')
  fireEvent.change(args, { target: { value: 'my-mcp' } })
  fireEvent.keyDown(args, { key: 'Enter' })
  fireEvent.change(within(dialog).getAllByPlaceholderText('KEY')[0], { target: { value: 'SITE' } })
  fireEvent.change(within(dialog).getAllByPlaceholderText('값')[0], { target: { value: 'FAB3' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '연결 테스트' }))
  expect(await within(dialog).findByText('mcp__local__ping')).toBeTruthy()
  expect(within(dialog).getByText('Ping tool')).toBeTruthy()
  fireEvent.click(within(dialog).getByRole('button', { name: '추가하고 켜기' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/harness/mcp/servers', expect.objectContaining({ method: 'POST', body: JSON.stringify({ name: 'new', command: 'uvx', args: ['my-mcp'], env: { SITE: 'FAB3' }, enabled: true }) })))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

it('edits immutable names and confirms deletion inline', async () => {
  const { fetcher } = setup()
  fireEvent.click(await screen.findByRole('button', { name: 'local 편집' }))
  const dialog = screen.getByRole('dialog')
  expect((within(dialog).getByLabelText('이름') as HTMLInputElement).readOnly).toBe(true)
  fireEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
  expect(within(dialog).getByText('삭제할까요? 되돌릴 수 없어요.')).toBeTruthy()
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/harness/mcp/servers/local', expect.objectContaining({ method: 'DELETE' })))
})

it('popover tests only when opened, shows rows, toggle badge and manage navigation', async () => {
  const { fetcher, onManage } = setup(true)
  const trigger = await screen.findByRole('button', { name: 'MCP 2' })
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/test'))).toBe(false)
  fireEvent.click(trigger)
  expect(await screen.findByText('켜짐 2 / 3 · 내 계정 전용')).toBeTruthy()
  fireEvent.click(screen.getByRole('switch', { name: 'off 사용' }))
  expect(await screen.findByText('다음 메시지부터')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '+ 서버 추가 · 관리 →' }))
  expect(onManage).toHaveBeenCalledOnce()
})
