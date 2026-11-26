import { useQuery } from '@tanstack/react-query'
import { Command } from 'cmdk'
import { Check, ChevronDown, Search } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useState, type RefObject } from 'react'

interface Props {
  value: string
  onChange: (value: string) => void
  fetcher: typeof fetch
  container: RefObject<HTMLElement | null>
}

export function ProductPicker({ value, onChange, fetcher, container }: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const catalog = useQuery({
    queryKey: ['yield-product-catalog'],
    enabled: open,
    queryFn: async ({ signal }): Promise<string[]> => {
      const response = await fetcher('/harness/products', { signal })
      if (!response.ok) throw new Error('제품 목록을 불러오지 못했습니다.')
      return (await response.json()).products
    },
  })
  const query = search.trim().toLowerCase()
  const codes = (catalog.data ?? []).filter(code => code.toLowerCase().includes(query))
  function select(code: string) {
    onChange(code)
    setOpen(false)
  }
  const label = value || '전체 제품'
  return <Popover.Root open={open} onOpenChange={next => { setOpen(next); setSearch('') }}>
    <Popover.Trigger asChild>
      <button type="button" className="web-product-trigger" aria-label={`분석 대상 제품, ${label}`}>
        <span className="web-product-icon" aria-hidden="true"/><span>{label}</span><ChevronDown size={13} aria-hidden="true"/>
      </button>
    </Popover.Trigger>
    <Popover.Portal container={container.current}>
      <Popover.Content className="web-product-popup yagent-popover" aria-label="분석 대상 제품" align="start" sideOffset={7} collisionPadding={12}>
        <Command label="제품 코드 검색" shouldFilter={false} loop>
          <div className="web-product-search">
            <Search size={15} aria-hidden="true"/>
            <Command.Input placeholder="제품 코드 검색" value={search} onValueChange={setSearch}/>
          </div>
          <Command.List label="제품 목록">
            {!query && <Command.Item value="all" onSelect={() => select('')}>
              전체 제품{!value && <Check size={15} aria-label="선택됨"/>}
            </Command.Item>}
            {codes.map(code => <Command.Item key={code} value={`product:${code}`} onSelect={() => select(code)}>
              {code}{code === value && <Check size={15} aria-label="선택됨"/>}
            </Command.Item>)}
          </Command.List>
          {catalog.isError ? <div className="web-product-status" role="alert">목록을 불러오지 못했어요. <button type="button" onClick={() => void catalog.refetch()}>다시 시도</button></div>
            : catalog.isPending ? <div className="web-product-status" role="status">제품을 불러오는 중…</div>
            : !codes.length && <div className="web-product-status" role="status">{query ? '검색 결과가 없어요.' : '등록된 제품이 없어요.'}</div>}
        </Command>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
}
