const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('yoriDesktop', {
  setExpanded: value => ipcRenderer.send('yori:expanded', {
    open: Boolean(value.open),
    details: Boolean(value.details),
    ...(typeof value.maximized === 'boolean' ? { maximized: value.maximized } : {})
  }),
  getWindowMode: () => ipcRenderer.invoke('yori:window-mode'),
  setReplyPreview: show => ipcRenderer.invoke('yori:reply-preview', Boolean(show)),
  getRestoreBounds: () => ipcRenderer.invoke('yori:get-restore-bounds'),
  setMaximized: next => ipcRenderer.invoke('yori:set-maximized', Boolean(next)),
  toggleMaximized: () => ipcRenderer.invoke('yori:toggle-maximized'),
  onCollapse: callback => {
    const listener = () => callback();
    ipcRenderer.on('yori:collapse', listener);
    return () => ipcRenderer.removeListener('yori:collapse', listener);
  },
  getCollapseTarget: () => ipcRenderer.invoke('yori:collapse-target'),
  captureCollapseFrame: () => ipcRenderer.invoke('yori:capture-collapse-frame'),
  beginResize: value => ipcRenderer.send('yori:resize-start', { edge: value.edge, screenX: value.screenX, screenY: value.screenY }),
  moveResize: value => ipcRenderer.send('yori:resize-move', { screenX: value.screenX, screenY: value.screenY }),
  endResize: () => ipcRenderer.invoke('yori:resize-end')
});

// Keep ordinary clicks, double-clicks, and context menus available on the mascot and header.
let gesture = null;
let suppressClick = false;
window.addEventListener('pointerdown', event => {
  suppressClick = false;
  if (!event.isPrimary || event.button !== 0) return;
  const button = event.target.closest?.('.yuri-desktop-launcher:not(:disabled)');
  if (button) {
    button.setPointerCapture(event.pointerId);
    gesture = { channel: 'yori:move-mascot', target: button, captured: true, id: event.pointerId, x: event.screenX, y: event.screenY,
      offsetX: event.clientX, offsetY: event.clientY };
    return;
  }
  const header = event.target.closest?.('.yuri-chat-header, .web-brand');
  if (!header || document.querySelector('[data-yuri-maximized]')) return;
  if (event.target.closest?.('button, a, input, textarea, [role="button"], .yuri-window-controls')) return;
  gesture = { channel: 'yori:move-window', target: header, captured: false, id: event.pointerId, x: event.screenX, y: event.screenY,
    offsetX: event.clientX, offsetY: event.clientY };
}, true);
window.addEventListener('pointermove', event => {
  if (!gesture || event.pointerId !== gesture.id) return;
  if (!suppressClick && Math.hypot(event.screenX - gesture.x, event.screenY - gesture.y) < 5) return;
  if (!gesture.captured) {
    try { gesture.target.setPointerCapture(event.pointerId); } catch {}
    gesture.captured = true;
  }
  suppressClick = true;
  ipcRenderer.send(gesture.channel, { screenX: event.screenX, screenY: event.screenY,
    offsetX: gesture.offsetX, offsetY: gesture.offsetY });
}, true);
for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  window.addEventListener(name, event => {
    if (gesture?.id === event.pointerId) gesture = null;
  }, true);
}
window.addEventListener('click', event => {
  if (suppressClick && event.detail > 0) {
    event.preventDefault();
    event.stopImmediatePropagation();
    suppressClick = false;
  }
}, true);
