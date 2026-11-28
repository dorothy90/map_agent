import { Popover } from 'radix-ui'
import { useRef, useState, type ReactNode, type RefObject } from 'react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { McpComposerButton } from './McpServers'
import { ProductPicker } from './ProductPicker'

export function ComposerOptions({ container, fetcher, product, onProduct, onMcp, onNewChat, onCreateSkill,
  busyMode, onPickFile, fileDisabled, notice, ready }: {
 container: RefObject<HTMLElement | null>
 fetcher: typeof fetch
 product: string
 onProduct: (product: string) => void
 onMcp: () => void
 onNewChat: () => void
 onCreateSkill: () => void
 busyMode: ReactNode
 onPickFile: () => void
 fileDisabled: boolean
 notice: string
 ready: boolean
}) {
 const [open, setOpen] = useState(false)
 const content = useRef<HTMLDivElement>(null)
 const run = (fn: () => void) => { setOpen(false); fn() }
 return <div className="web-composer-options">
  <Popover.Root open={open} onOpenChange={setOpen}>
   <TooltipProvider delayDuration={0}>
    <Tooltip open={Boolean(notice)}>
     <TooltipTrigger asChild>
      <Popover.Trigger asChild>
       <button type="button" className="web-composer-add" aria-label="채팅 추가 옵션"><span aria-hidden="true"/></button>
      </Popover.Trigger>
     </TooltipTrigger>
     <TooltipContent side="bottom">{notice}</TooltipContent>
    </Tooltip>
   </TooltipProvider>
   <Popover.Portal container={container.current}>
    <Popover.Content ref={content} className="web-composer-options-menu yagent-popover" aria-label="채팅 추가 옵션"
      side="top" align="start" sideOffset={8} collisionPadding={8}
      collisionBoundary={container.current?.querySelector(".yuri-window-frame") ?? undefined}
      onOpenAutoFocus={event => { event.preventDefault(); content.current?.querySelector('button')?.focus() }}>
     <div className="web-composer-options-list">
     <ProductPicker value={product} onChange={onProduct} fetcher={fetcher} container={content} menuRow/>
     <button type="button" className="web-composer-option" disabled={!ready} onClick={() => run(onCreateSkill)}>
      <span className="web-option-skill-icon" aria-hidden="true"/>스킬 추가
     </button>
     <McpComposerButton fetcher={fetcher} container={content} onManage={() => run(onMcp)} menuRow/>
     <div className="web-composer-options-divider" aria-hidden="true"/>
     {busyMode}
     <button type="button" className="web-composer-option" disabled={fileDisabled} onClick={() => run(onPickFile)}>
      <span className="web-attach-icon" aria-hidden="true"/>파일 첨부
     </button>
     <div className="web-composer-options-divider" aria-hidden="true"/>
     <button type="button" className="web-composer-option" disabled={!ready} onClick={() => run(onNewChat)}>
      <span className="web-option-new-icon" aria-hidden="true"/>새 채팅
     </button>
     </div>
    </Popover.Content>
   </Popover.Portal>
  </Popover.Root>
 </div>
}
