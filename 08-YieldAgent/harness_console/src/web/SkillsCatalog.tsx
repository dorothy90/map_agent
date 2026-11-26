import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Plus, TrendingUp, X } from 'lucide-react'
import { motion } from 'motion/react'
import { Dialog } from 'radix-ui'
import { useState } from 'react'
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

interface SkillSection {
  heading: string
  body: string
  bullets: string[]
  steps: { title: string; details: string[] }[]
}

function parseSkillSections(content: string, title: string): { intro: string; sections: SkillSection[] } {
  const lines = content.trim().split('\n')
  const first = lines[0]?.trim().replace(/^#+\s*/, '').trim()
  const rest = first && first === title.trim() ? lines.slice(1) : lines

  const introLines: string[] = []
  const rawSections: { heading: string; lines: string[] }[] = []
  let current: { heading: string; lines: string[] } | null = null

  for (const line of rest) {
    const headingMatch = line.match(/^#{1,3}\s+(.+)$/)
    if (headingMatch) {
      current = { heading: headingMatch[1].trim(), lines: [] }
      rawSections.push(current)
    } else if (current) {
      current.lines.push(line)
    } else {
      introLines.push(line)
    }
  }

  const sections: SkillSection[] = rawSections
    .map(({ heading, lines: sectionLines }) => {
      const body = sectionLines.join('\n').trim()
      const nonEmpty = sectionLines.filter(l => l.trim().length > 0)

      // Check if section is a numbered list (possibly with nested bullet details)
      const steps: { title: string; details: string[] }[] = []
      let isNumbered = nonEmpty.length > 0
      for (const l of nonEmpty) {
        const numMatch = l.match(/^\s*\d+\.\s+(.+)$/)
        const subBullet = l.match(/^\s{2,}[-*]\s+(.+)$/)
        if (numMatch) {
          steps.push({ title: numMatch[1].trim(), details: [] })
        } else if (subBullet && steps.length > 0) {
          steps[steps.length - 1].details.push(subBullet[1].trim())
        } else {
          isNumbered = false
          break
        }
      }

      // Check if section is a flat bullet list
      const bullets: string[] = []
      let isBullets = nonEmpty.length > 0 && !isNumbered
      if (isBullets) {
        for (const l of nonEmpty) {
          const bMatch = l.match(/^\s*[-*]\s+(.+)$/)
          if (bMatch) {
            bullets.push(bMatch[1].trim())
          } else {
            isBullets = false
            break
          }
        }
      }

      return {
        heading,
        body,
        bullets: isBullets ? bullets : [],
        steps: isNumbered ? steps : [],
      }
    })
    .filter(s => s.body.length > 0)

  return { intro: introLines.join('\n').trim(), sections }
}

function InlineMarkdown({ text }: { text: string }) {
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        p: ({ children }) => <>{children}</>,
        img: ({ alt }) => alt || null,
      }}
    >
      {text}
    </Markdown>
  )
}

function SkillIcon({ kind }: { kind: string }) {
  switch (kind) {
    case 'paper':
      return <FileText size={16} strokeWidth={1.6} aria-hidden="true" />
    case 'trend':
      return <TrendingUp size={16} strokeWidth={1.6} aria-hidden="true" />
    case 'wafer':
      return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
          <path d="M8.5 5.5v13M12 3.5v17M15.5 5.5v13M5.5 8.5h13M3.5 12h17M5.5 15.5h13" stroke="currentColor" strokeWidth="1.2" opacity=".6" />
        </svg>
      )
    default:
      return <span className="skill-mask-icon skill-mask-layers" aria-hidden="true" />
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

  return (
    <Dialog.Root>
      <article className="skill-card" data-enabled={skill.enabled ? 'true' : 'false'}>
        <div className="skill-card-top">
          <span className="skill-icon-badge">
            <SkillIcon kind={skill.artwork} />
          </span>
          <button
            className="skill-switch"
            type="button"
            role="switch"
            aria-checked={skill.enabled}
            aria-label={`${skill.title} 사용`}
            title={skill.enabled ? '사용 중' : '사용 안 함'}
            disabled={toggle.isPending}
            onClick={() => toggle.mutate(!skill.enabled)}
          >
            <i />
          </button>
        </div>
        <div className="skill-card-body">
          <h2 className="skill-card-title">
            <Dialog.Trigger asChild>
              <button className="skill-open" type="button" aria-label={`${skill.title} 상세 보기`}>
                {skill.title}
              </button>
            </Dialog.Trigger>
          </h2>
          <p className="skill-summary">{skill.description}</p>
          {toggle.isError && <p className="skill-status-error" role="alert">{toggle.error.message}</p>}
          <div className="skill-card-footer">
            <span className="skill-category">{skill.category}</span>
          </div>
        </div>
        <Dialog.Portal>
          <Dialog.Overlay className="skill-dialog-overlay" />
          <Dialog.Content className="skill-dialog" aria-describedby={undefined}>
            <div className="skill-dialog-head">
              <div className="skill-dialog-hero">
                <span className="skill-icon-badge skill-dialog-icon">
                  <SkillIcon kind={skill.artwork} />
                </span>
                <div className="skill-dialog-heading">
                  <Dialog.Title>{skill.title}</Dialog.Title>
                  <p className="skill-dialog-submeta">{skill.category}</p>
                </div>
              </div>
              <div className="skill-dialog-controls">
                <button
                  className="skill-dialog-toggle"
                  type="button"
                  role="switch"
                  aria-checked={skill.enabled}
                  aria-label={`${skill.title} 사용`}
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate(!skill.enabled)}
                >
                  <span className="skill-dialog-toggle-dot" aria-hidden="true" />
                  <span>{skill.enabled ? '사용 중' : '사용 안 함'}</span>
                </button>
                <Dialog.Close asChild>
                  <button className="skill-close" type="button" aria-label="닫기">
                    <X size={15} strokeWidth={1.8} />
                  </button>
                </Dialog.Close>
              </div>
            </div>
            <div className="skill-dialog-scroll">
              {(() => {
                const parsed = parseSkillSections(skill.content || skill.description, skill.title)
                if (!parsed.sections.length) {
                  return (
                    <div className="skill-instructions">
                      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => alt || null }}>
                        {parsed.intro || skill.description}
                      </Markdown>
                    </div>
                  )
                }
                // Merge short single-paragraph prose sections (e.g. "목적") into a clean top lead paragraph
                const proseIdx = !parsed.intro
                  ? parsed.sections.findIndex(s => !s.bullets.length && !s.steps.length && !s.body.includes('\n\n') && s.body.length < 260)
                  : -1
                const leadText = parsed.intro || (proseIdx >= 0 ? parsed.sections[proseIdx].body : '')
                const remaining = parsed.sections.filter((_, idx) => idx !== proseIdx)

                return (
                  <div className="skill-editorial">
                    {leadText && (
                      <div className="skill-lead skill-instructions">
                        <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => alt || null }}>
                          {leadText}
                        </Markdown>
                      </div>
                    )}
                    {remaining.map(section => (
                      <section className="skill-section" key={section.heading}>
                        <h3 className="skill-section-title">{section.heading}</h3>

                        {section.steps.length > 0 ? (
                          <ol className="skill-pattern-box">
                            {section.steps.map((step, idx) => (
                              <li className="skill-pattern-row" key={step.title}>
                                <span className="skill-pattern-num">{String(idx + 1).padStart(2, '0')}</span>
                                <div className="skill-pattern-content">
                                  <strong className="skill-pattern-title">
                                    <InlineMarkdown text={step.title} />
                                  </strong>
                                  {step.details.map(detail => (
                                    <p className="skill-pattern-detail" key={detail}>
                                      <InlineMarkdown text={detail} />
                                    </p>
                                  ))}
                                </div>
                              </li>
                            ))}
                          </ol>
                        ) : section.bullets.length > 0 ? (
                          <ul className="skill-clean-list">
                            {section.bullets.map(item => (
                              <li key={item}>
                                <InlineMarkdown text={item} />
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <div className="skill-instructions">
                            <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => alt || null }}>
                              {section.body}
                            </Markdown>
                          </div>
                        )}
                      </section>
                    ))}
                  </div>
                )
              })()}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </article>
    </Dialog.Root>
  )
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

  const filterOptions: { key: string | null; label: string; count?: number }[] = [
    { key: null, label: '전체', count: catalog.data ? skills.length : undefined },
    ...categories.map(value => ({ key: value, label: value })),
  ]

  return (
    <section className="skills-catalog" aria-labelledby="skills-title">
      <div className="skills-inner">
        <div className="skills-heading">
          <div>
            <h1 id="skills-title">내 스킬</h1>
            {catalog.data && <p>수율 분석에 사용하는 스킬 {skills.length}개</p>}
          </div>
          <button type="button" className="skills-create" onClick={onCreate}>
            <Plus size={14} strokeWidth={1.8} aria-hidden="true" />
            <span>새 스킬</span>
          </button>
        </div>
        <div className="skills-toolbar">
          <div className="skills-filters" role="group" aria-label="스킬 분류">
            {filterOptions.map(option => {
              const selected = activeCategory === option.key
              return (
                <button
                  type="button"
                  key={option.key ?? '__all__'}
                  className="skills-filter-tab"
                  aria-pressed={selected}
                  onClick={() => setCategory(option.key)}
                >
                  {selected && (
                    <motion.span
                      layoutId="skills-filter-pill"
                      className="skills-filter-pill"
                      transition={{ type: 'spring', stiffness: 460, damping: 36 }}
                    />
                  )}
                  <span className="skills-filter-label">
                    {option.label}
                    {option.count !== undefined && <small>{option.count}</small>}
                  </span>
                </button>
              )
            })}
          </div>
          <label className="skills-search">
            <span className="skill-mask-icon skill-mask-search" aria-hidden="true" />
            <input
              type="search"
              aria-label="스킬 검색"
              placeholder="스킬 검색"
              value={search}
              onChange={event => setSearch(event.target.value)}
            />
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
                <X size={13} strokeWidth={1.8} />
              </button>
            )}
          </label>
        </div>
        {catalog.isError && (
          <p className="skills-notice" role="alert">
            스킬 목록을 불러오지 못했어요.{' '}
            <button type="button" onClick={() => void catalog.refetch()}>다시 시도</button>
          </p>
        )}
        {catalog.isPending && <p className="skills-notice" role="status">스킬을 불러오는 중…</p>}
        {catalog.data && !visible.length && (
          <p className="skills-notice" role="status">
            {skills.length ? '조건에 맞는 스킬이 없어요.' : '아직 등록된 스킬이 없어요.'}
          </p>
        )}
        <div className="skills-grid">
          {visible.map(skill => <SkillCard key={skill.name} skill={skill} fetcher={fetcher} />)}
        </div>
      </div>
    </section>
  )
}
