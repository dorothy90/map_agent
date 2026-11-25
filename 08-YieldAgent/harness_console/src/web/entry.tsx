import { createRoot } from 'react-dom/client'
import { HermesWebApp, type HermesWebOptions } from './HermesWebApp'
import { YieldTableArtifactFrame } from '../hermes/components/assistant-ui/embeds/frame-embed'
import { matrixArtifactPath } from './matrix-panel-bridge'
import '../hermes/styles.css'
import './web.css'
declare global { interface Window { hermesWebOptions?: HermesWebOptions } }
export async function mount() {
 const viewer=new URLSearchParams(location.search).get('matrix_viewer')
 if (viewer !== null) {
   const src=matrixArtifactPath(viewer)
   createRoot(document.getElementById('root')!).render(src
     ? <YieldTableArtifactFrame src={src} title="웨이퍼 누적 맵">{null}</YieldTableArtifactFrame>
     : <p>맵 주소를 확인해 주세요.</p>)
   return
 }
 let options=window.hermesWebOptions ?? {}
 if (import.meta.env.DEV && !options.resolver) options=await import('./dev-connection').then(module => module.devConnectionOptions())
 createRoot(document.getElementById('root')!).render(<HermesWebApp {...options}/>)
}
