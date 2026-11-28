import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { McpComposerButton, McpServersPage } from './McpServers'

const container = { current: null }
afterEach(() => { cleanup(); localStorage.clear() })
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

it('renders short command + tool chips cards, counts, filtering, search, footer test action and enabled mutations', async () => {
  const { fetcher } = setup()
  await screen.findByRole('heading', { name: 'local' })
  await screen.findByText('명령을 찾을 수 없어요.')
  expect(screen.queryByText('stdio')).toBeNull()
  expect(screen.queryByText('연결됨')).toBeNull()
  expect(screen.queryByText('제공 도구')).toBeNull()
  expect(screen.queryByText('안 1')).toBeNull()
  expect(screen.queryByRole('button', { name: '⋯' })).toBeNull()
  expect(screen.getByText('python server.py')).toBeTruthy()
  expect(screen.getByText('ping')).toBeTruthy()
  expect(screen.getByText('도구 1개')).toBeTruthy()
  expect(screen.getByRole('img', { name: '연결됨' })).toBeTruthy()
  expect(screen.getByRole('img', { name: '꺼짐' })).toBeTruthy()
  expect(screen.getByRole('img', { name: '오류' })).toBeTruthy()
  const okTestBtn = screen.getByRole('button', { name: '테스트 완료' })
  expect(okTestBtn.querySelector('.mcp-test-check')).toBeTruthy()
  expect(screen.getByRole('button', { name: '오류 · 재시도' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '테스트' })).toBeTruthy()
  const testCallsBefore = fetcher.mock.calls.filter(([url]) => String(url).endsWith('/local/test')).length
  fireEvent.click(okTestBtn)
  expect(screen.queryByRole('dialog')).toBeNull()
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/local/test')).length).toBe(testCallsBefore + 1))
  expect(screen.getByRole('button', { name: '전체 3' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '켜짐 2' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '꺼짐 1' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '오류 1' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '오류 1' }))
  expect(screen.queryByRole('heading', { name: 'local' })).toBeNull()
  expect(screen.getByRole('heading', { name: 'broken' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '전체 3' }))
  const search = screen.getByRole<HTMLInputElement>('searchbox', { name: '서버 검색' })
  expect(screen.queryByRole('button', { name: '검색어 지우기' })).toBeNull()
  fireEvent.change(search, { target: { value: 'local' } })
  expect(screen.queryByRole('heading', { name: 'off' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '검색어 지우기' }))
  expect(search.value).toBe('')
  expect(screen.getByRole('heading', { name: 'off' })).toBeTruthy()
  fireEvent.change(search, { target: { value: 'local' } })
  expect(screen.queryByRole('heading', { name: 'off' })).toBeNull()
  fireEvent.click(screen.getByRole('switch', { name: 'local 사용' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/harness/mcp/servers/local/enabled', expect.objectContaining({ method: 'PUT', body: '{"enabled":false}' })))
  await waitFor(() => expect(screen.getByRole('switch', { name: 'local 사용' }).getAttribute('aria-checked')).toBe('false'))
  expect(screen.getByText('ping')).toBeTruthy()
  expect(screen.getByText('도구 1개')).toBeTruthy()
  const manualCallsBefore = fetcher.mock.calls.filter(([url]) => String(url).endsWith('/local/test')).length
  fireEvent.click(screen.getByRole('button', { name: '테스트 완료' }))
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/local/test')).length).toBe(manualCallsBefore + 1))
})

it('restores cached tools from localStorage for disabled servers across reload', async () => {
  localStorage.setItem('yagent.mcp.probeCache.v1', JSON.stringify({
    off: {
      probe: { ok: true, tools: [{ name: 'mcp__off__inspect', description: 'Inspect tool' }], elapsed_ms: 15 },
      updatedAt: Date.now() - 60_000,
    },
  }))
  setup()
  await screen.findByRole('heading', { name: 'off' })
  expect(screen.getByText('inspect')).toBeTruthy()
  expect(screen.getAllByText('도구 1개').length).toBeGreaterThanOrEqual(1)
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

it('opens edit modal on card click, shows cached test details, and confirms deletion inline', async () => {
  const { fetcher } = setup()
  await screen.findByText('ping')
  fireEvent.click(screen.getByRole('button', { name: 'local' }))
  const dialog = screen.getByRole('dialog')
  expect(dialog.classList.contains('skill-dialog')).toBe(true)
  expect(within(dialog).getByText('mcp__local__ping')).toBeTruthy()
  expect(within(dialog).getByRole('button', { name: '연결 테스트' })).toBeTruthy()
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
