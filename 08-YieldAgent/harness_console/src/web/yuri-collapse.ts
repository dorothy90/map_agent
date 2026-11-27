import { animate } from 'motion/react'

export async function captureYuriFrame(capture: () => Promise<ArrayBuffer | null>) {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const png = await Promise.race([
      capture(),
      new Promise<null>(resolve => { timer = setTimeout(() => resolve(null), 600) }),
    ])
    return png ? await createImageBitmap(new Blob([png], { type: 'image/png' })) : null
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

export function animateYuriCollapse(canvas: HTMLCanvasElement, frame: ImageBitmap, target?: {x:number;y:number} | null) {
  const context = canvas.getContext('2d')
  if (!context) return null
  const { width, height } = frame
  const scaleX = width / canvas.clientWidth
  const scaleY = height / canvas.clientHeight
  if (!Number.isFinite(scaleX) || !Number.isFinite(scaleY)) return null

  // Electron's capture can flatten the transparent window margins onto white.
  const surface = new OffscreenCanvas(width, height)
  const mask = surface.getContext('2d')
  if (!mask) return null
  mask.beginPath()
  mask.roundRect(10 * scaleX, 10 * scaleY, width - 20 * scaleX, height - 24 * scaleY, 20 * scaleX)
  mask.clip()
  mask.drawImage(frame, 0, 0)

  canvas.width = width
  canvas.height = height
  const endWidth = 44 * scaleX
  const endHeight = 34 * scaleY
  const endLeft = (target ? target.x + 45 : canvas.clientWidth - 45) * scaleX - endWidth / 2
  const endTop = (target ? target.y + 54 : canvas.clientHeight - 46) * scaleY - endHeight / 2
  const stripHeight = Math.max(1, Math.round(2 * scaleY))

  const draw = (progress: number) => {
    const slide = Math.max(0, (progress - .34) / .66)
    const travel = slide * slide * (3 - 2 * slide)
    const pinch = Math.min(1, progress / .48)
    const nearPull = pinch * pinch * (3 - 2 * pinch)
    const top = endTop * travel
    const currentHeight = height + (endHeight - height) * travel
    context.clearRect(0, 0, width, height)
    context.globalAlpha = Math.min(1, (1 - progress) / .12)
    // The rows nearest the mascot converge first, bending both sides of the window.
    for (let y = 0; y < height; y += stripHeight) {
      const rows = Math.min(stripHeight, height - y)
      const depth = (y + rows / 2) / height
      const position = travel + (endTop < height / 2 ? 1 - depth : depth) * (1 - travel)
      const curve = position * position * (3 - 2 * position)
      const pull = nearPull * curve
      const left = endLeft * pull
      const rowWidth = width + (endWidth - width) * pull
      const rowTop = Math.floor(top + y / height * currentHeight)
      const rowBottom = Math.ceil(top + (y + rows) / height * currentHeight)
      context.drawImage(surface, 0, y, width, rows, left, rowTop, rowWidth, rowBottom - rowTop)
    }
  }
  draw(0)
  canvas.style.visibility = 'visible'
  return animate(0, 1, { duration: .45, ease: 'linear', onUpdate: draw })
}
