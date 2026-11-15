import { afterEach, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
const css=readFileSync(`${process.cwd()}/src/web/web.css`,'utf8')

afterEach(() => { document.head.innerHTML=''; document.body.innerHTML='' })

it('isolates the row layout and keeps the dock in flow outside both scroll regions', () => {
 const style=document.createElement('style')
 // Reproduce the legacy main rule and upstream dock utilities without a browser.
 style.textContent=`main { display:flex; flex-direction:column } .absolute { position:absolute; bottom:0; left:50%; translate:-50% } ${css}`
 document.head.append(style)
 document.body.innerHTML=`<main id="legacy"></main><main class="hermes-web"><nav></nav><div class="hermes-web-chat"><div class="hermes-web-thread"></div><div class="hermes-web-composer"><div data-slot="composer-dock" class="absolute"></div></div></div></main>`
 const computed=(selector:string) => getComputedStyle(document.querySelector(selector)!)
 expect(computed('#legacy').flexDirection).toBe('column')
 expect(computed('.hermes-web').flexDirection).toBe('row')
 expect(computed('.hermes-web').fontSize).toBe('14px')
 expect(computed('.hermes-web nav').width).toBe('248px')
 expect(computed('.hermes-web-chat').overflow).toBe('hidden')
 expect(computed('.hermes-web-thread').minHeight).toBe('0px')
 expect(computed('[data-slot="composer-dock"]').position).toBe('relative')
 expect(computed('[data-slot="composer-dock"]').translate).toBe('none')
})

it('preserves report colors', () => {
 const style=document.createElement('style');style.textContent=css;document.head.append(style)
 document.body.innerHTML='<main class="hermes-web"><iframe class="yield-table-artifact-frame"></iframe><img alt="wafer"/><iframe title="map"></iframe></main><iframe class="yield-table-artifact-frame" id="other"/>'
 expect(getComputedStyle(document.querySelector('.hermes-web iframe')!).filter).not.toBe('grayscale(1)')
 expect(getComputedStyle(document.querySelector('img')!).filter).not.toBe('grayscale(1)')
 expect(getComputedStyle(document.querySelector('#other')!).filter).not.toBe('grayscale(1)')
})

it('keeps composer input borderless and session titles phrase-wrapped', () => {
 const style=document.createElement('style');style.textContent=css;document.head.append(style)
 document.body.innerHTML='<main class="hermes-web"><div class="web-session"><strong>긴 질문</strong></div><div data-slot="composer-fade"><div contenteditable="true"></div></div></main>'
 const label=getComputedStyle(document.querySelector('strong')!)
 expect(label.wordBreak).toBe('keep-all')
 expect(label.overflowWrap).toBe('break-word')
 expect(label.getPropertyValue('-webkit-line-clamp')).toBe('2')
 expect(getComputedStyle(document.querySelector('[contenteditable]')!).outline).toBe('none')
})
