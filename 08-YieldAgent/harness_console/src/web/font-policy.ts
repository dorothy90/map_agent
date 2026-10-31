import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
/** Do not emit bundled web fonts, including KaTeX's transitive font files. */
export function hermesSystemFonts():Plugin {
 const source=fileURLToPath(new URL('../hermes/styles.css',import.meta.url))
 const math=readFileSync(fileURLToPath(new URL('../../node_modules/katex/dist/katex.min.css',import.meta.url)),'utf8').replace(/@font-face\s*\{[^}]*\}/g,'')
 return {name:'hermes-system-fonts',enforce:'pre',transform(code,id) {
   const path=id.split('?')[0]
   if(path.endsWith('/katex/dist/katex.min.css')) return {code:math,map:null}
   if(path!==source)return
   return {code:code.replace("@import 'katex/dist/katex.min.css';",math),map:null}
 }}
}
