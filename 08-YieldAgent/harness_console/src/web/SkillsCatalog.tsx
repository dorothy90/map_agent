import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight, Layers, Plus, Search, X } from 'lucide-react'
import { Dialog } from 'radix-ui'
import { type ReactElement, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './SkillsCatalog.css'

interface Skill {
  name: string
  title: string
  description: string
  category: string
  artwork: string
  enabled: boolean
  content: string
}

const catalogKey = ['yield-skills'] as const
const waferCells: ReactElement[] = []
for (let y = -10; y <= 10; y++) {
  for (let x = -10; x <= 10; x++) {
    if (x * x + y * y > 96) continue
    const shade = Math.sin(x * .45 + y * .23) + Math.cos(y * .73)
    waferCells.push(<rect key={`${x},${y}`} x={50 + x * 4.4} y={50 + y * 4.4} width="3.5" height="3.5" rx=".65"
      fill={shade > .85 ? '#aa8966' : shade > 0 ? '#c8b49b' : '#d8cdbd'} />)
  }
}

function SkillArtwork({ kind }: { kind: string }) {
  switch (kind) {
    case 'paper':
      return <div aria-hidden="true" className="skill-art skill-art-paper">
        <div className="skill-paper-back"/><div className="skill-paper-page"><i/><i/><span/></div>
      </div>
    case 'trend':
      return <div aria-hidden="true" className="skill-art skill-art-trend"><svg width="150" height="94" viewBox="0 0 150 94" fill="none">
        <path d="M10 22h132M10 43h132M10 64h132M10 84h132" stroke="#d5dcd3"/>
        <path d="M10 70L31 63L53 68L77 45L100 48L123 31L141 22" stroke="#68816b" strokeWidth="3"/>
        <path d="M10 72L32 74L54 56L77 60L100 43L123 45L141 39" stroke="#acb9a9" strokeDasharray="3 4" strokeWidth="2"/>
      </svg></div>
    case 'wafer':
      return <div aria-hidden="true" className="skill-art skill-art-wafer"><svg height="100" width="100" viewBox="0 0 100 100">{waferCells}</svg></div>
    default:
      return <div aria-hidden="true" className="skill-art skill-art-default"><Layers size={66} strokeWidth={1}/></div>
  }
}

function SkillCard({ skill, fetcher }: { skill: Skill; fetcher: typeof fetch }) {
  const client = useQueryClient()
  const toggle = useMutation({
    mutationFn: async (enabled: boolean) => {
      const response = await fetcher(`/harness/skills/${encodeURIComponent(skill.name)}/enabled`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
      })
      if (!response.ok) throw new Error('스킬 사용 설정을 저장하지 못했어요.')
      return enabled
    },
    onSuccess: enabled => {
      client.setQueryData<Skill[]>(catalogKey, current => current?.map(item => item.name === skill.name ? { ...item, enabled } : item))
      return client.invalidateQueries({ queryKey: catalogKey })
    },
  })
  return <Dialog.Root><article className="skill-card">
    <SkillArtwork kind={skill.artwork}/>
    <div className="skill-card-body">
      <div className="skill-card-title"><h2><Dialog.Trigger asChild>
        <button className="skill-open" type="button" aria-label={`${skill.title} 상세 보기`}>{skill.title}</button>
      </Dialog.Trigger></h2>
      <button className="skill-switch" type="button" role="switch" aria-checked={skill.enabled} aria-label={`${skill.title} 사용`}
        title={skill.enabled ? '사용 중' : '사용 안 함'} disabled={toggle.isPending} onClick={() => toggle.mutate(!skill.enabled)}><i/></button></div>
      <p className="skill-summary">{skill.description}</p>
      {toggle.isError && <p className="skill-status-error" role="alert">{toggle.error.message}</p>}
      <div className="skill-card-footer"><span className="skill-category">{skill.category}</span><ArrowUpRight size={16} aria-hidden="true"/></div>
    </div>
    <Dialog.Portal><Dialog.Overlay className="skill-dialog-overlay"/>
      <Dialog.Content className="skill-dialog" aria-describedby={undefined}>
        <div className="skill-dialog-head"><div><span className="skill-category">{skill.category}</span>
          <Dialog.Title>{skill.title}</Dialog.Title><p>{skill.name}</p></div>
          <Dialog.Close asChild><button className="skill-close" type="button" aria-label="닫기"><X size={19}/></button></Dialog.Close>
        </div>
        <div className="skill-instructions"><Markdown remarkPlugins={[remarkGfm]} skipHtml
          components={{ img: ({ alt }) => alt || null }}>{skill.content || skill.description}</Markdown></div>
      </Dialog.Content>
    </Dialog.Portal>
  </article></Dialog.Root>
}

export function SkillsCatalog({ onCreate, fetcher = fetch }: { onCreate: () => void; fetcher?: typeof fetch }) {
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const catalog = useQuery({
    queryKey: catalogKey, refetchOnWindowFocus: true,
    queryFn: async ({ signal }): Promise<Skill[]> => {
      const response = await fetcher('/harness/skills', { signal })
      if (!response.ok) throw new Error('스킬 목록을 불러오지 못했어요.')
      return (await response.json()).skills
    },
  })
  const skills = catalog.data ?? []
  const categories = [...new Set(skills.map(skill => skill.category))]
  const activeCategory = category && categories.includes(category) ? category : null
  const searchTerm = search.trim().toLocaleLowerCase()
  const visible = skills.filter(skill => (!activeCategory || activeCategory === skill.category) &&
    [skill.title, skill.name, skill.description].some(value => value.toLocaleLowerCase().includes(searchTerm)))

  return <section className="skills-catalog" aria-labelledby="skills-title"><div className="skills-inner">
    <div className="skills-heading"><div><h1 id="skills-title">내 스킬</h1>
      {catalog.data && <p>수율 분석에 사용하는 스킬 {skills.length}개</p>}</div>
      <button type="button" className="skills-create" onClick={onCreate}><Plus size={15} aria-hidden="true"/>새 스킬</button>
    </div>
    <div className="skills-toolbar"><div className="skills-filters" role="group" aria-label="스킬 분류">
      <button type="button" aria-pressed={!activeCategory} onClick={() => setCategory(null)}>전체 {catalog.data && <small>{skills.length}</small>}</button>
      {categories.map(value => <button type="button" key={value} aria-pressed={activeCategory === value} onClick={() => setCategory(value)}>{value}</button>)}
    </div><label className="skills-search"><Search size={15} aria-hidden="true"/>
      <input type="search" aria-label="스킬 검색" placeholder="스킬 검색" value={search} onChange={event => setSearch(event.target.value)}/>
    </label></div>
    {catalog.isError && <p className="skills-notice" role="alert">스킬 목록을 불러오지 못했어요. <button type="button" onClick={() => void catalog.refetch()}>다시 시도</button></p>}
    {catalog.isPending && <p className="skills-notice" role="status">스킬을 불러오는 중…</p>}
    {catalog.data && !visible.length && <p className="skills-notice" role="status">{skills.length ? '조건에 맞는 스킬이 없어요.' : '아직 등록된 스킬이 없어요.'}</p>}
    <div className="skills-grid">{visible.map(skill => <SkillCard key={skill.name} skill={skill} fetcher={fetcher}/>)}</div>
  </div></section>
}
