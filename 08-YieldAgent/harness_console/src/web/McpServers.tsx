import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Info, Plug, Plus, Search, X } from 'lucide-react'
import { Dialog, Popover } from 'radix-ui'
import { useState, type RefObject } from 'react'
import './SkillsCatalog.css'
import './McpServers.css'

interface McpServer {
  name: string
  transport: string
  command: string | null
  args: string[]
  env: Record<string, string>
  enabled: boolean
  url?: string | null
}
interface Probe {
  ok: boolean
  error?: string
  tools: { name: string; description: string }[]
  elapsed_ms: number
}
interface Props {
  fetcher: typeof fetch
  container: RefObject<HTMLElement | null>
}
const serversKey = ['yield-mcp-servers'] as const
const changedKey = ['yield-mcp-changed'] as const
const testKey = (name: string) => ['yield-mcp-test', name] as const
const endpoint = (name: string) => `/harness/mcp/servers/${encodeURIComponent(name)}`
const shortToolName = (server: McpServer, name: string) => {
  const prefix = `mcp__${server.name.replaceAll('-', '_')}__`
  return name.startsWith(prefix) ? name.slice(prefix.length) : name
}
const commandSummary = (server: McpServer) => [server.command, ...server.args].filter(Boolean).join(' ') || server.url || server.transport
async function request<T>(fetcher: typeof fetch, url: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetcher(url, { method, signal, ...(body === undefined ? {} : {
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }) })
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'MCP 서버 요청에 실패했어요.')
  return result
}
export function useMcpServers(fetcher: typeof fetch) {
  return useQuery({ queryKey: serversKey, queryFn: async ({ signal }): Promise<McpServer[]> =>
    (await request<{ servers: McpServer[] }>(fetcher, '/harness/mcp/servers', 'GET', undefined, signal)).servers,
  })
}
function useMcpTests(servers: McpServer[], fetcher: typeof fetch, active = true) {
  return useQueries({ queries: servers.map(server => ({
    queryKey: testKey(server.name), staleTime: 5 * 60 * 1000, retry: false,
    enabled: active && server.enabled,
    queryFn: ({ signal }: { signal: AbortSignal }) => request<Probe>(fetcher, endpoint(server.name) + '/test', 'POST', undefined, signal),
  })) })
}
function useMcpMutation(fetcher: typeof fetch) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ name, method, suffix = '', body }: { name: string; method: string; suffix?: string; body?: unknown }) => {
      await client.cancelQueries({ queryKey: testKey(name) })
      return request(fetcher, method === 'POST' ? '/harness/mcp/servers' : endpoint(name) + suffix, method, body)
    },
    onSuccess: async (_, { name, suffix, method }) => {
      if (suffix === '/enabled' || method === 'POST') client.setQueryData<string[]>(changedKey, current => [...new Set([...(current ?? []), name])])
      await client.invalidateQueries({ queryKey: serversKey })
      void client.resetQueries({ queryKey: testKey(name) })
    },
  })
}
function ServerSwitch({ server, fetcher }: { server: McpServer; fetcher: typeof fetch }) {
  const toggle = useMcpMutation(fetcher)
  return <div className="mcp-toggle"><button type="button" className="skill-switch" role="switch" aria-label={`${server.name} 사용`}
    aria-checked={server.enabled} disabled={toggle.isPending}
    onClick={() => toggle.mutate({ name: server.name, method: 'PUT', suffix: '/enabled', body: { enabled: !server.enabled } })}><i/></button>
    {toggle.isError && <p role="alert" className="mcp-error">{toggle.error.message}</p>}</div>
}
function ProbeResult({ result }: { result: Probe }) {
  return <div className="mcp-test-result" role="status">
    <div className={result.ok ? 'mcp-test-pass' : 'mcp-error'}>{result.ok ? `✓ 연결 테스트 통과 · 도구 ${result.tools.length}개` : result.error}
      <span>{(result.elapsed_ms / 1000).toFixed(1)}초</span></div>
    {result.ok && <ul>{result.tools.map(tool => <li key={tool.name}><code>{tool.name}</code><span>{tool.description}</span></li>)}</ul>}
  </div>
}
function ServerPanel({ server, fetcher, close }: { server: McpServer | null; fetcher: typeof fetch; close: () => void }) {
  const [name, setName] = useState(server?.name ?? '')
  const [command, setCommand] = useState(server?.command ?? '')
  const [args, setArgs] = useState(server?.args ?? [])
  const [arg, setArg] = useState('')
  const [env, setEnv] = useState<[string, string][]>([...Object.entries(server?.env ?? {}), ['', '']])
  const [confirmDelete, setConfirmDelete] = useState(false)
  const mutation = useMcpMutation(fetcher)
  const test = useMutation({ mutationFn: (body: unknown) => request<Probe>(fetcher, endpoint(name) + '/test', 'POST', body) })
  const http = !!server && server.transport !== 'stdio'
  const busy = mutation.isPending || test.isPending
  const body = () => ({ command, args: arg ? [...args, arg] : args, env: Object.fromEntries(env.filter(([key, value]) => key || value)) })
  function updateEnv(index: number, column: number, value: string) {
    setEnv(current => {
      const next = current.map((row, i) => i === index ? row.map((cell, j) => j === column ? value : cell) as [string, string] : row)
      if (next.at(-1)?.some(Boolean)) next.push(['', ''])
      return next
    })
  }
  return <>
    <div className="mcp-panel-head"><div><Dialog.Title>{server ? '서버 편집' : '서버 추가'}</Dialog.Title>
      <Dialog.Description>내 프로필 config.yaml 에 저장돼요</Dialog.Description></div>
      <Dialog.Close asChild><button type="button" className="skill-close" aria-label="닫기"><X size={19}/></button></Dialog.Close></div>
    <div className="mcp-panel-body">
      <fieldset disabled={busy || http} onChange={() => test.reset()}>
        <div className="mcp-field"><label htmlFor="mcp-name">이름</label><input id="mcp-name" aria-describedby="mcp-name-help" value={name} readOnly={!!server} onChange={event => setName(event.target.value)}/>
          <small id="mcp-name-help">도구 이름이 <code>mcp__{name.replaceAll('-', '_') || '<name>'}__도구</code> 로 붙어요.</small></div>
        <div className="mcp-field"><span>연결 방식</span><div className="mcp-segment"><button type="button" aria-pressed={!http}>로컬 명령 (stdio)</button><button type="button" disabled>HTTP · 지원 안 함</button></div></div>
        <label className="mcp-field">명령<input value={command} onChange={event => setCommand(event.target.value)}/></label>
        <div className="mcp-field"><label htmlFor="mcp-args">인자</label><div className="mcp-args">{args.map((value, index) => <span key={index}><code>{value}</code>
          <button type="button" aria-label={`${value} 인자 삭제`} onClick={() => { setArgs(args.filter((_, i) => i !== index)); test.reset() }}>×</button></span>)}
          <input id="mcp-args" placeholder="인자 입력 후 Enter" value={arg} onChange={event => setArg(event.target.value)} onKeyDown={event => {
            if (event.key === 'Enter') { event.preventDefault(); if (arg) { setArgs([...args, arg]); setArg(''); test.reset() } }
            if (event.key === 'Backspace' && !arg) { setArgs(args.slice(0, -1)); test.reset() }
          }}/></div></div>
        <div className="mcp-field"><span>환경 변수</span>{env.map(([key, value], index) => <div className="mcp-env" key={index}>
          <input aria-label={`환경 변수 이름 ${index + 1}`} placeholder="KEY" value={key} onChange={event => updateEnv(index, 0, event.target.value)}/>
          <input aria-label={`환경 변수 값 ${index + 1}`} placeholder="값" value={value} onChange={event => updateEnv(index, 1, event.target.value)}/>
          {index < env.length - 1 && <button type="button" aria-label={`환경 변수 ${index + 1} 삭제`} onClick={() => { setEnv(env.filter((_, i) => i !== index)); test.reset() }}><X size={15}/></button>}
        </div>)}</div>
      </fieldset>
      {http && <p className="mcp-hint">HTTP 서버는 여기서 편집할 수 없어요. 테스트하거나 삭제할 수 있어요.</p>}
      {test.data && <ProbeResult result={test.data}/>}
      {(test.isError || mutation.isError) && <p role="alert" className="mcp-error">{test.error?.message || mutation.error?.message}</p>}
    </div>
    <div className="mcp-panel-footer"><p className="mcp-hint"><Info size={14}/>다음 메시지부터 대화에서 쓸 수 있어요.</p>
      {confirmDelete && <p className="mcp-error">삭제할까요? 되돌릴 수 없어요.</p>}
      <div className="mcp-panel-actions">{server && <button type="button" className="mcp-delete" disabled={busy} onClick={() => confirmDelete
        ? mutation.mutate({ name, method: 'DELETE' }, { onSuccess: close }) : setConfirmDelete(true)}>삭제</button>}
        <button type="button" className="mcp-button" disabled={busy || !name || (!http && !command.trim())} onClick={() => test.mutate(http ? undefined : body())}>{test.isPending ? '테스트 중…' : '연결 테스트'}</button>
        <Dialog.Close asChild><button type="button" className="mcp-button">취소</button></Dialog.Close>
        <button type="button" className="skills-create" disabled={busy || http || !name || !command.trim()} onClick={() => mutation.mutate({
          name, method: server ? 'PUT' : 'POST', body: server ? body() : { name, ...body(), enabled: true },
        }, { onSuccess: close })}>{server ? '저장' : '추가하고 켜기'}</button>
      </div>
    </div>
  </>
}

export function McpServersPage({ fetcher, container }: Props) {
  const catalog = useMcpServers(fetcher)
  const servers = catalog.data ?? []
  const tests = useMcpTests(servers, fetcher)
  const [filter, setFilter] = useState('전체')
  const [search, setSearch] = useState('')
  const [panel, setPanel] = useState<McpServer | null | undefined>(undefined)
  const rows = servers.map((server, index) => ({ server, test: tests[index], error: tests[index].data?.ok === false || tests[index].isError }))
  const counts: Record<string, number> = { 전체: servers.length, 켜짐: servers.filter(s => s.enabled).length, 꺼짐: servers.filter(s => !s.enabled).length, 오류: rows.filter(r => r.error).length }
  const visible = rows.filter(({ server, error }) => (filter === '전체' || (filter === '켜짐' && server.enabled) || (filter === '꺼짐' && !server.enabled) || (filter === '오류' && error))
    && (server.name + ' ' + commandSummary(server)).toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  return <section className="mcp-page" aria-labelledby="mcp-title"><div className="skills-inner">
    <div className="skills-heading"><div><h1 id="mcp-title">내 MCP 서버</h1><p>내 계정에만 등록되는 로컬 도구 서버예요. 켜 둔 서버는 대화에서 바로 쓸 수 있어요.</p></div>
      <button type="button" className="skills-create" onClick={() => setPanel(null)} aria-label="+ 서버 추가"><Plus size={15}/>서버 추가</button></div>
    <div className="skills-toolbar"><div className="skills-filters" role="group" aria-label="서버 상태">{Object.entries(counts).map(([label, count]) => <button type="button" key={label} aria-pressed={filter === label} onClick={() => setFilter(label)}>{label} <small>{count}</small></button>)}</div>
      <label className="skills-search"><Search size={15}/><input type="search" aria-label="서버 검색" placeholder="서버 검색" value={search} onChange={event => setSearch(event.target.value)}/></label></div>
    {catalog.isPending && <p role="status" className="mcp-hint">서버를 불러오는 중…</p>}
    {catalog.isError && <p role="alert" className="mcp-error">{catalog.error.message} <button onClick={() => void catalog.refetch()}>다시 시도</button></p>}
    <div className="mcp-grid">{visible.map(({ server, test, error }) => <article key={server.name} className={`mcp-card${server.enabled ? '' : ' mcp-off'}`}>
      <div className="mcp-card-head"><div><h2>{server.name}</h2><div className="mcp-badges"><span className={`mcp-badge ${error ? 'mcp-badge-error' : server.enabled && test.data?.ok ? 'mcp-badge-ok' : ''}`}>{error ? '오류' : !server.enabled ? '꺼짐' : test.data?.ok ? '연결됨' : '켜짐·테스트 전'}</span><span className="mcp-badge">{server.transport}</span></div></div><ServerSwitch server={server} fetcher={fetcher}/></div>
      <p className="mcp-command" title={commandSummary(server)}>{commandSummary(server)}</p>
      {error ? <p className="mcp-error">{test.data?.error || test.error?.message}</p> : <div className="mcp-tools">{test.data?.tools.slice(0, 3).map(tool => <code key={tool.name}>{shortToolName(server, tool.name)}</code>)}{(test.data?.tools.length ?? 0) > 3 && <code>+{test.data!.tools.length - 3}</code>}</div>}
      <div className="mcp-card-footer"><span>도구 {test.data ? test.data.tools.length : '—'}개 · {test.dataUpdatedAt ? new Date(test.dataUpdatedAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) + ' 테스트' : '테스트 전'}</span>
        <button type="button" disabled={test.isFetching} onClick={() => void test.refetch()}>{test.isFetching ? '테스트 중…' : error ? '다시 테스트' : '테스트'}</button>
        <button type="button" aria-label={`${server.name} 편집`} onClick={() => setPanel(server)}>⋯</button></div>
    </article>)}<button type="button" className="mcp-add-card" onClick={() => setPanel(null)}><Plus size={24}/><span>로컬 MCP 서버 추가</span><small>명령으로 실행하는 stdio 서버</small></button></div>
    <p className="mcp-hint mcp-foot-hint"><Info size={14}/>추가하거나 켠 도구는 다음 메시지부터 대화에 붙어요.</p>
    <Dialog.Root open={panel !== undefined} onOpenChange={open => { if (!open) setPanel(undefined) }}><Dialog.Portal container={container.current}>
      <Dialog.Overlay className="skill-dialog-overlay"/><Dialog.Content className="mcp-panel">
        {panel !== undefined && <ServerPanel key={panel?.name ?? 'new'} server={panel} fetcher={fetcher} close={() => setPanel(undefined)}/>}
      </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div></section>
}

export function McpComposerButton({ fetcher, container, onManage }: Props & { onManage: () => void }) {
  const [open, setOpen] = useState(false)
  const catalog = useMcpServers(fetcher)
  const servers = catalog.data ?? []
  const tests = useMcpTests(servers, fetcher, open)
  const changed = useQuery<string[]>({ queryKey: changedKey, queryFn: () => [], initialData: [], enabled: false })
  const enabled = servers.filter(server => server.enabled).length
  return <Popover.Root open={open} onOpenChange={next => { setOpen(next); if (next) void catalog.refetch() }}>
    <Popover.Trigger asChild><button type="button" className="mcp-composer-trigger"><Plug size={15} aria-hidden="true"/>MCP {enabled}</button></Popover.Trigger>
    <Popover.Portal container={container.current}><Popover.Content className="mcp-popover" aria-label="MCP 서버" side="top" align="start" sideOffset={8} collisionPadding={12}>
      <div className="mcp-popover-head"><strong>MCP 서버</strong><span>켜짐 {enabled} / {servers.length} · 내 계정 전용</span></div>
      <div className="mcp-popover-list">{servers.map((server, index) => {
        const test = tests[index], error = test.data?.ok === false || test.isError
        return <div className="mcp-popover-row" key={server.name}>
          <span className={`mcp-dot ${error ? 'mcp-dot-error' : server.enabled && test.data?.ok ? 'mcp-dot-ok' : ''}`} aria-label={error ? '오류' : !server.enabled ? '꺼짐' : test.data?.ok ? '연결됨' : '켜짐·테스트 전'}/>
          <div className="mcp-popover-info"><strong>{server.name}</strong>{changed.data.includes(server.name) && <span className="mcp-pending">다음 메시지부터</span>}
            <p className={error ? 'mcp-popover-error' : 'mcp-command'} title={commandSummary(server)}>{error ? test.data?.error || test.error?.message : commandSummary(server)}</p></div>
          {error ? <button type="button" className="mcp-button" disabled={test.isFetching} onClick={() => void test.refetch()}>{test.isFetching ? '테스트 중…' : '다시 테스트'}</button> : <span className="mcp-tool-count">도구 {test.data ? test.data.tools.length : '—'}</span>}
          <ServerSwitch server={server} fetcher={fetcher}/>
        </div>
      })}</div>
      {catalog.isPending && <p className="mcp-popover-notice">서버를 불러오는 중…</p>}
      {catalog.isError && <p role="alert" className="mcp-error">{catalog.error.message} <button onClick={() => void catalog.refetch()}>다시 시도</button></p>}
      {catalog.data && !servers.length && <p className="mcp-popover-notice">아직 등록된 서버가 없어요.</p>}
      <div className="mcp-popover-footer"><span>새로 켠 도구는 다음 메시지부터 붙어요</span><button type="button" onClick={() => { setOpen(false); onManage() }}>+ 서버 추가 · 관리 →</button></div>
    </Popover.Content></Popover.Portal>
  </Popover.Root>
}
