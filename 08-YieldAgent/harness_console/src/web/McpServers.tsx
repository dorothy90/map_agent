import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { motion } from 'motion/react'
import { Dialog, Popover } from 'radix-ui'
import { useState, type RefObject } from 'react'
import { Tip } from '@/components/ui/tooltip'
import { useComposerFlyout } from './use-composer-flyout'
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
const probeCacheStorageKey = 'yagent.mcp.probeCache.v1'
interface CachedProbe {
  probe: Probe
  updatedAt: number
}
function readProbeCache(): Record<string, CachedProbe> {
  try {
    const raw = globalThis.localStorage?.getItem(probeCacheStorageKey)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, CachedProbe> : {}
  } catch {
    return {}
  }
}
function writeProbeCache(update: (cache: Record<string, CachedProbe>) => void) {
  try {
    const cache = readProbeCache()
    update(cache)
    globalThis.localStorage?.setItem(probeCacheStorageKey, JSON.stringify(cache))
  } catch {
    // Ignore storage errors (e.g. private browsing or quota exceeded).
  }
}
function saveCachedProbe(name: string, probe: Probe, updatedAt = Date.now()) {
  if (!probe.ok) return
  writeProbeCache(cache => { cache[name] = { probe, updatedAt } })
}
function removeCachedProbe(name: string) {
  writeProbeCache(cache => { delete cache[name] })
}
const endpoint = (name: string) => `/harness/mcp/servers/${encodeURIComponent(name)}`
const shortToolName = (server: McpServer, name: string) => {
  const prefix = `mcp__${server.name.replaceAll('-', '_')}__`
  return name.startsWith(prefix) ? name.slice(prefix.length) : name
}
const commandSummary = (server: McpServer) => [server.command, ...server.args].filter(Boolean).join(' ') || server.url || server.transport
const shortArg = (arg: string) => {
  if (arg.startsWith('@') && arg.split('/').length === 2) return arg
  if (arg.includes('/') || arg.includes('\\')) return arg.split(/[\\/]/).pop() || arg
  return arg
}
const shortCommand = (server: McpServer) => {
  if (!server.command) return server.url || server.transport
  const bin = server.command.split(/[\\/]/).pop() || server.command
  return [bin, ...server.args.map(shortArg)].filter(Boolean).join(' ')
}
const serverStatus = (server: McpServer, ok: boolean | undefined, error: boolean) => error ? '오류' : !server.enabled ? '꺼짐' : ok ? '연결됨' : '켜짐·테스트 전'
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
    initialData: () => readProbeCache()[server.name]?.probe,
    initialDataUpdatedAt: () => readProbeCache()[server.name]?.updatedAt,
    queryFn: async ({ signal }: { signal: AbortSignal }) => {
      const probe = await request<Probe>(fetcher, endpoint(server.name) + '/test', 'POST', undefined, signal)
      if (probe.ok) saveCachedProbe(server.name, probe)
      return probe
    },
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
      if (suffix !== '/enabled') {
        removeCachedProbe(name)
        void client.resetQueries({ queryKey: testKey(name) })
      }
    },
  })
}
const formatClockTime = (timestamp: number) => new Date(timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
const formatTestTime = (timestamp?: number) => timestamp ? formatClockTime(timestamp) + ' 테스트' : '테스트 전'
function ServerSwitch({ server, fetcher }: { server: McpServer; fetcher: typeof fetch }) {
  const toggle = useMcpMutation(fetcher)
  return <div className="mcp-toggle" onClick={event => event.stopPropagation()}><button type="button" className="skill-switch" role="switch" aria-label={`${server.name} 사용`}
    aria-checked={server.enabled} disabled={toggle.isPending}
    onClick={event => { event.stopPropagation(); toggle.mutate({ name: server.name, method: 'PUT', suffix: '/enabled', body: { enabled: !server.enabled } }) }}><i/></button>
    {toggle.isError && <p role="alert" className="mcp-error">{toggle.error.message}</p>}</div>
}
function ProbeResult({ result, testedAt }: { result: Probe; testedAt?: number }) {
  return <div className="mcp-test-result" role="status">
    <div className={result.ok ? 'mcp-test-pass' : 'mcp-test-fail'}>
      <span className="mcp-test-summary">{result.ok ? <><span className="mcp-test-check" aria-hidden="true"/>연결 테스트 통과 · 도구 {result.tools.length}개</> : result.error}</span>
      <span className="mcp-test-meta">{testedAt ? `${formatTestTime(testedAt)} · ` : ''}{(result.elapsed_ms / 1000).toFixed(1)}초</span>
    </div>
    {result.ok && <ul>{result.tools.map(tool => <li key={tool.name}><code>{tool.name}</code><span>{tool.description}</span></li>)}</ul>}
  </div>
}
interface InitialTest {
  data?: Probe
  dataUpdatedAt?: number
  isError?: boolean
  error?: Error | null
}
function ServerPanel({ server, initialTest, fetcher, close }: { server: McpServer | null; initialTest?: InitialTest; fetcher: typeof fetch; close: () => void }) {
  const [name, setName] = useState(server?.name ?? '')
  const [command, setCommand] = useState(server?.command ?? '')
  const [args, setArgs] = useState(server?.args ?? [])
  const [arg, setArg] = useState('')
  const [env, setEnv] = useState<[string, string][]>([...Object.entries(server?.env ?? {}), ['', '']])
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [testedAt, setTestedAt] = useState(0)
  const mutation = useMcpMutation(fetcher)
  const test = useMutation({
    mutationFn: (body: unknown) => request<Probe>(fetcher, endpoint(name) + '/test', 'POST', body),
    onSettled: () => setTestedAt(Date.now()),
  })
  const resetTest = () => { setDirty(true); test.reset() }
  const http = !!server && server.transport !== 'stdio'
  const busy = mutation.isPending || test.isPending
  const activeProbe = test.data ?? (!dirty ? initialTest?.data : undefined)
  const activeTestedAt = test.data || test.isError ? testedAt : (!dirty ? initialTest?.dataUpdatedAt : 0)
  const activeError = test.error?.message || mutation.error?.message || (!dirty && initialTest?.isError ? initialTest.error?.message : undefined)
  const body = () => ({ command, args: arg ? [...args, arg] : args, env: Object.fromEntries(env.filter(([key, value]) => key || value)) })
  function updateEnv(index: number, column: number, value: string) {
    setEnv(current => {
      const next = current.map((row, i) => i === index ? row.map((cell, j) => j === column ? value : cell) as [string, string] : row)
      if (next.at(-1)?.some(Boolean)) next.push(['', ''])
      return next
    })
  }
  return <>
    <div className="skill-dialog-head">
      <div className="skill-dialog-heading">
        <Dialog.Title>{server ? '서버 편집' : '서버 추가'}</Dialog.Title>
        <Dialog.Description className="skill-dialog-submeta">
          내 프로필 config.yaml 에 저장돼요{server && <> · {formatTestTime(activeTestedAt)}</>}
        </Dialog.Description>
      </div>
      <div className="skill-dialog-controls">
        <Dialog.Close asChild><button type="button" className="skill-close" aria-label="닫기"><X size={15} strokeWidth={1.8}/></button></Dialog.Close>
      </div>
    </div>
    <div className="skill-dialog-scroll mcp-dialog-body">
      <fieldset disabled={busy || http} onChange={resetTest}>
        <div className="mcp-field"><label htmlFor="mcp-name">이름</label><input id="mcp-name" aria-describedby="mcp-name-help" value={name} readOnly={!!server} onChange={event => setName(event.target.value)}/>
          <small id="mcp-name-help">도구 이름이 <code>mcp__{name.replaceAll('-', '_') || '<name>'}__도구</code> 로 붙어요.</small></div>
        <div className="mcp-field"><span>연결 방식</span><div className="mcp-segment"><button type="button" aria-pressed={!http}>로컬 명령 (stdio)</button><button type="button" disabled>HTTP · 지원 안 함</button></div></div>
        <label className="mcp-field">명령<input value={command} onChange={event => setCommand(event.target.value)}/></label>
        <div className="mcp-field"><label htmlFor="mcp-args">인자</label><div className="mcp-args">{args.map((value, index) => <span key={index}><code>{value}</code>
          <button type="button" aria-label={`${value} 인자 삭제`} onClick={() => { setArgs(args.filter((_, i) => i !== index)); resetTest() }}>×</button></span>)}
          <input id="mcp-args" placeholder="인자 입력 후 Enter" value={arg} onChange={event => setArg(event.target.value)} onKeyDown={event => {
            if (event.key === 'Enter') { event.preventDefault(); if (arg) { setArgs([...args, arg]); setArg(''); resetTest() } }
            if (event.key === 'Backspace' && !arg) { setArgs(args.slice(0, -1)); resetTest() }
          }}/></div></div>
        <div className="mcp-field"><span>환경 변수</span>{env.map(([key, value], index) => <div className="mcp-env" key={index}>
          <input aria-label={`환경 변수 이름 ${index + 1}`} placeholder="KEY" value={key} onChange={event => updateEnv(index, 0, event.target.value)}/>
          <input aria-label={`환경 변수 값 ${index + 1}`} placeholder="값" value={value} onChange={event => updateEnv(index, 1, event.target.value)}/>
          {index < env.length - 1 && <button type="button" aria-label={`환경 변수 ${index + 1} 삭제`} onClick={() => { setEnv(env.filter((_, i) => i !== index)); resetTest() }}><X size={15}/></button>}
        </div>)}</div>
      </fieldset>
      {http && <p className="mcp-hint">HTTP 서버는 여기서 편집할 수 없어요. 테스트하거나 삭제할 수 있어요.</p>}
      {activeProbe && <ProbeResult result={activeProbe} testedAt={activeTestedAt}/>}
      {activeError && <p role="alert" className="mcp-error">{activeError}</p>}
    </div>
    <div className="mcp-dialog-footer"><p className="mcp-hint"><span className="mcp-info-icon" aria-hidden="true"/>다음 메시지부터 대화에서 쓸 수 있어요.</p>
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
  const selectedRow = panel ? rows.find(row => row.server.name === panel.name) : undefined
  return <section className="skills-catalog mcp-page" aria-labelledby="mcp-title"><div className="skills-inner">
    <div className="skills-heading"><div><h1 id="mcp-title">내 MCP 서버</h1><p>내 계정에만 등록되는 로컬 도구 서버예요. 켜 둔 서버는 대화에서 바로 쓸 수 있어요.</p></div>
      <button type="button" className="skills-create" onClick={() => setPanel(null)} aria-label="+ 서버 추가"><span className="mcp-plus-icon" aria-hidden="true"/>서버 추가</button></div>
    <div className="skills-toolbar">
      <div className="skills-filters" role="group" aria-label="서버 상태">
        {Object.entries(counts).map(([label, count]) => (
          <button type="button" key={label} className="skills-filter-tab" aria-pressed={filter === label} onClick={() => setFilter(label)}>
            {filter === label && <motion.span layoutId="mcp-filter-pill" className="skills-filter-pill" transition={{ type:'spring', stiffness:460, damping:36 }}/>}
            <span className="skills-filter-label">{label}{catalog.data && <>{' '}<small>{count}</small></>}</span>
          </button>
        ))}
      </div>
      <label className="skills-search">
        <span className="skill-mask-icon skill-mask-search" aria-hidden="true"/>
        <input type="search" aria-label="서버 검색" placeholder="서버 검색" value={search} onChange={event => setSearch(event.target.value)}/>
        {search && (
          <button
            type="button"
            className="skills-search-clear"
            aria-label="검색어 지우기"
            onClick={event => {
              event.preventDefault()
              setSearch('')
            }}
          >
            <X size={13} strokeWidth={1.8}/>
          </button>
        )}
      </label>
    </div>
    {catalog.isPending && <p role="status" className="mcp-hint">서버를 불러오는 중…</p>}
    {catalog.isError && <p role="alert" className="mcp-error">{catalog.error.message} <button onClick={() => void catalog.refetch()}>다시 시도</button></p>}
    <div className="mcp-grid">{visible.map(({ server, test, error }) => {
      const status = serverStatus(server, test.data?.ok, error)
      const tools = test.data?.tools ?? []
      const toolListTooltip = !test.data
        ? '테스트 후 확인 가능'
        : tools.length > 0
          ? tools.map(tool => shortToolName(server, tool.name)).join(', ')
          : '제공 도구가 없어요'
      const testTooltip = test.dataUpdatedAt
        ? `마지막 테스트: ${formatClockTime(test.dataUpdatedAt)} · 클릭하여 다시 테스트`
        : '클릭하여 연결 테스트'
      const testButtonTone = !test.isFetching && error ? ' mcp-test-btn-error' : ''
      return <article key={server.name} className={`mcp-card${server.enabled ? '' : ' mcp-off'}`} onClick={() => setPanel(server)}>
        <div className="mcp-card-head">
          <div className="mcp-card-title">
            <span className={`mcp-dot ${error ? 'mcp-dot-error' : server.enabled && test.data?.ok ? 'mcp-dot-ok' : ''}`} role="img" aria-label={status} title={status}/>
            <h2><button type="button" className="skill-open" onClick={event => { event.stopPropagation(); setPanel(server) }}>{server.name}</button></h2>
          </div>
          <ServerSwitch server={server} fetcher={fetcher}/>
        </div>
        <div className="mcp-card-body">
          {error ? (
            <p className="mcp-error">{test.data?.error || test.error?.message}</p>
          ) : (
            <>
              <p className="mcp-card-cmd" title={commandSummary(server)}>{shortCommand(server)}</p>
              {tools.length > 0 ? (
                <div className="mcp-tools">
                  {tools.slice(0, 3).map(tool => <code key={tool.name}>{shortToolName(server, tool.name)}</code>)}
                  {tools.length > 3 && (
                    <Tip label={tools.slice(3).map(tool => shortToolName(server, tool.name)).join(', ')} side="top">
                      <code className="mcp-tools-more">+{tools.length - 3}</code>
                    </Tip>
                  )}
                </div>
              ) : (!server.enabled || test.isFetching) && (
                <p className="mcp-card-empty">{!server.enabled ? '꺼져 있어요. 켜면 제공 도구를 불러와요.' : '도구 목록을 확인하는 중…'}</p>
              )}
            </>
          )}
        </div>
        <div className="mcp-card-footer">
          <Tip label={toolListTooltip} side="top">
            <span className="mcp-tool-badge"><span className="mcp-wrench-icon" aria-hidden="true"/>도구 {test.data ? tools.length : '—'}개</span>
          </Tip>
          <Tip label={testTooltip} side="top">
            <button
              type="button"
              className={`mcp-test-btn${testButtonTone}`}
              disabled={test.isFetching}
              onClick={event => { event.stopPropagation(); void test.refetch() }}
            >
              {test.isFetching ? (
                <><Loader2 size={12} strokeWidth={1.8} className="mcp-spin" aria-hidden="true"/>테스트 중…</>
              ) : error ? (
                '오류 · 재시도'
              ) : test.data?.ok ? (
                <><span className="mcp-test-check" aria-hidden="true"/>테스트 완료</>
              ) : (
                '테스트'
              )}
            </button>
          </Tip>
        </div>
      </article>
    })}<button type="button" className="mcp-add-card" onClick={() => setPanel(null)}><span className="mcp-plus-icon mcp-plus-icon-lg" aria-hidden="true"/><span>로컬 MCP 서버 추가</span><small>명령으로 실행하는 stdio 서버</small></button></div>
    <p className="mcp-hint mcp-foot-hint"><span className="mcp-info-icon" aria-hidden="true"/>추가하거나 켠 도구는 다음 메시지부터 대화에 붙어요.</p>
    <Dialog.Root open={panel !== undefined} onOpenChange={open => { if (!open) setPanel(undefined) }}><Dialog.Portal>
      <Dialog.Overlay className="skill-dialog-overlay"/><Dialog.Content className="skill-dialog mcp-dialog">
        {panel !== undefined && <ServerPanel key={panel?.name ?? 'new'} server={panel} initialTest={selectedRow?.test} fetcher={fetcher} close={() => setPanel(undefined)}/>}
      </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div></section>
}

export function McpComposerButton({ fetcher, container, onManage, menuRow }: Props & { onManage: () => void; menuRow?: boolean }) {
  const [open, setOpen] = useState(false)
  const flyout = useComposerFlyout(open, Boolean(menuRow), 380)
  const catalog = useMcpServers(fetcher)
  const servers = catalog.data ?? []
  const tests = useMcpTests(servers, fetcher, open)
  const changed = useQuery<string[]>({ queryKey: changedKey, queryFn: () => [], initialData: [], enabled: false })
  const enabled = servers.filter(server => server.enabled).length
  return <Popover.Root open={open} onOpenChange={next => { setOpen(next); if (next) void catalog.refetch() }}>
    <Tip label="MCP 서버" side="bottom">
      <Popover.Trigger asChild>
        <button ref={flyout.trigger} type="button" className={menuRow ? "web-composer-option" : "mcp-composer-trigger"} aria-label={`MCP ${enabled}`}>
          <span className="web-mcp-icon" aria-hidden="true"/>{menuRow ? <>MCP 서버 <small>{enabled}</small></> : enabled}
        </button>
      </Popover.Trigger>
    </Tip>
    <Popover.Portal container={container.current}><Popover.Content className="mcp-popover" aria-label="MCP 서버" side={flyout.side} align="start" sideOffset={8} collisionPadding={8} collisionBoundary={flyout.collisionBoundary}>
      <div className="mcp-popover-head"><strong>MCP 서버</strong><span>켜짐 {enabled} / {servers.length} · 내 계정 전용</span></div>
      <div className="mcp-popover-list">{servers.map((server, index) => {
        const test = tests[index], error = test.data?.ok === false || test.isError, status = serverStatus(server, test.data?.ok, error)
        return <div className="mcp-popover-row" key={server.name}>
          <span className={`mcp-dot ${error ? 'mcp-dot-error' : server.enabled && test.data?.ok ? 'mcp-dot-ok' : ''}`} aria-label={status} title={status}/>
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
