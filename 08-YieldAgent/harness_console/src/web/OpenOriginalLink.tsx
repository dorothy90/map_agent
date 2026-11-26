import { Tip } from '@/components/ui/tooltip'

export function OpenOriginalLink({ href }: { href: string }) {
  return (
    <Tip label="원본 열기" side="bottom">
      <a className="web-report-link" href={href} target="_blank" rel="noopener noreferrer" aria-label="원본 열기" />
    </Tip>
  )
}
