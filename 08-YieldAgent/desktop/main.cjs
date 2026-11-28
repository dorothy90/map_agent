const { app, BrowserWindow, ipcMain, screen, Menu } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
if (process.env.YORI_DESKTOP_DATA_DIR) {
  fs.mkdirSync(process.env.YORI_DESKTOP_DATA_DIR, { recursive: true });
  app.setPath('userData', path.resolve(process.env.YORI_DESKTOP_DATA_DIR));
}

if (!app.requestSingleInstanceLock()) {
  app.exit(0);
  return;
}

const appUrl = new URL(app.isPackaged
  ? require('./package.json').deploymentUrl
  : process.env.YORI_APP_URL || 'http://127.0.0.1:4173/');
if (!['http:', 'https:'].includes(appUrl.protocol)) throw new Error('YORI_APP_URL must be an HTTP(S) service URL');
appUrl.searchParams.set('desktop', '1');
let window, opened = false, resize = null;
let restoredBounds = null;
let expandedSize = { width: 360, height: 670 };
function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
app.on('activate', showWindow);
app.on('second-instance', showWindow);

const edges = new Set(['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']);
const sizeFile = () => path.join(app.getPath('userData'), 'window-size.json');
const authorized = event => event.sender === window.webContents && event.senderFrame && new URL(event.senderFrame.url).origin === appUrl.origin;
const point = value => Number.isFinite(value?.screenX) && Number.isFinite(value?.screenY);
const resizeEvents = [];
function resizeDiagnostic(type, detail = {}) {
  if (process.env.YORI_RESIZE_DEBUG !== '1') return;
  resizeEvents.push({ type, ...detail, at: new Date().toISOString() });
  if (resizeEvents.length > 40) resizeEvents.shift();
  fs.writeFileSync(path.join(app.getPath('userData'), 'resize-debug.json'), JSON.stringify(resizeEvents));
}
let mascotPinned = true;
let preferMaximized = false;
function rememberSize() {
  if (!opened || restoredBounds) return;
  const { width, height } = window.getBounds();
  expandedSize = { width, height };
  savePreferences();
}
function savePreferences() {
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  fs.writeFileSync(sizeFile()+'.tmp', JSON.stringify({ ...expandedSize, mascotPinned, maximized: preferMaximized }));
  fs.renameSync(sizeFile()+'.tmp', sizeFile());
}
function resizeTo(value) {
  if (!resize || !point(value)) return;
  const { bounds, area, edge, screenX, screenY } = resize;
  const dx = value.screenX-screenX, dy = value.screenY-screenY;
  const width = Math.round(Math.max(Math.min(320, area.width), Math.min(area.width, bounds.width+(edge.includes('e') ? dx : edge.includes('w') ? -dx : 0))));
  const height = Math.round(Math.max(Math.min(480, area.height), Math.min(area.height, bounds.height+(edge.includes('s') ? dy : edge.includes('n') ? -dy : 0))));
  const x = edge.includes('w') ? bounds.x+bounds.width-width : bounds.x;
  const y = edge.includes('n') ? bounds.y+bounds.height-height : bounds.y;
  window.setBounds({ width, height, x: Math.round(Math.max(area.x, Math.min(x, area.x+area.width-width))), y: Math.round(Math.max(area.y, Math.min(y, area.y+area.height-height))) });
}
app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  if (fs.existsSync(sizeFile())) {
    try {
      const saved = JSON.parse(fs.readFileSync(sizeFile(), 'utf8'));
      if (Number.isFinite(saved.width) && Number.isFinite(saved.height)) expandedSize = { width: saved.width, height: saved.height };
      if (typeof saved.mascotPinned === "boolean") mascotPinned = saved.mascotPinned;
      if (typeof saved.maximized === "boolean") preferMaximized = saved.maximized;
    } catch { /* A damaged local preference falls back to the default window size. */ }
  }
  const area = screen.getPrimaryDisplay().workArea;
  window = new BrowserWindow({
    width: 90, height: 100, x: area.x + area.width - 110, y: area.y + area.height - 120,
    // Transparent windows need app-owned resize handles rather than native resizable chrome.
    frame: false, transparent: true, hasShadow: false, alwaysOnTop: mascotPinned, resizable: false, skipTaskbar: true, acceptFirstMouse: true,
    title: 'Y-Agent', backgroundColor: '#00000000',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  let anchorOffset = { x: 0, y: 0 };
  let replyPreview = null;
  const clearReplyPreview = () => {
    if (!replyPreview) return;
    window.setBounds(replyPreview.anchor);
    replyPreview = null;
    window.setAlwaysOnTop(mascotPinned);
  };
  window.on('hide', clearReplyPreview);
  window.webContents.setWindowOpenHandler(({ url }) => {
    const target = new URL(url);
    const reportPath = /^\/harness\/(?:artifacts|results)\/[a-zA-Z0-9_-]+$/;
    if (target.origin === appUrl.origin && reportPath.test(target.pathname)) {
      const report = new BrowserWindow({
        parent: window,
        width: 880,
        height: 680,
        title: 'Y-Agent · 원본',
        webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
      });
      report.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      report.webContents.on('will-frame-navigate', (event) => {
        if (new URL(event.url).origin !== appUrl.origin) event.preventDefault();
      });
      void report.loadURL(target.href).catch(() => {
        if (!report.isDestroyed()) report.close();
      });
    }
    return { action: 'deny' };
  });
  window.webContents.on('before-mouse-event', (event, mouse) => {
    if (mouse.type !== 'mouseMove') resizeDiagnostic(mouse.type, { x: mouse.x, y: mouse.y, button: mouse.button });
  });
  window.webContents.on('cursor-changed', (event, type) => resizeDiagnostic('cursor', { cursor: type }));
  const updater = app.isPackaged && process.platform === 'win32' ? require('./windows-update.cjs') : null;
  window.webContents.on('context-menu', () => {
    const actions = [{ label: 'Y-Agent 종료', click: () => app.quit() }];
    if (updater) actions.unshift(updater.windowsUpdateMenuItem(window), { type: 'separator' });
    if (!opened) actions.unshift({
      label: '항상 위', type: 'checkbox', checked: mascotPinned,
      click: item => {
        mascotPinned = item.checked;
        window.setAlwaysOnTop(mascotPinned || Boolean(replyPreview));
        savePreferences();
      }
    }, { label: '숨기기', click: () => window.hide() });
    Menu.buildFromTemplate(actions).popup({ window });
  });
  window.webContents.on('will-navigate', (event, url) => { if (new URL(url).origin !== appUrl.origin) event.preventDefault(); });
  window.on('blur', () => {
    if (resize) rememberSize();
    resizeDiagnostic('blur');
    resize = null;
  });
  window.on('close', rememberSize);
  window.loadURL(appUrl.toString());
  ipcMain.handle('yori:window-mode', event => {
    if (!authorized(event)) return { maximized: false };
    return { maximized: preferMaximized };
  });
  ipcMain.handle('yori:reply-preview', (event, show) => {
    if (!authorized(event) || opened) return null;
    if (!show || !window.isVisible()) {
      clearReplyPreview();
      return null;
    }
    if (replyPreview) return replyPreview.layout;
    const anchor = window.getBounds();
    const area = screen.getDisplayMatching(anchor).workArea;
    const bubbleHeight = 106;
    const width = Math.min(174, area.width);
    const height = anchor.height + bubbleHeight;
    if (width < anchor.width || height > area.height) return null;
    const preferredX = anchor.x - Math.round((width - anchor.width) / 2 + 19);
    const rightEdge = area.x + area.width;
    const x = Math.max(area.x, Math.min(preferredX, rightEdge - width));
    const below = anchor.y - bubbleHeight < area.y;
    const y = below ? anchor.y : anchor.y - bubbleHeight;
    const layout = { mascotOffsetX: anchor.x - x, below };
    replyPreview = { anchor, layout, bubbleHeight };
    window.setBounds({ x, y, width, height });
    // Show the preview above other windows without activating the app.
    window.setAlwaysOnTop(true);
    return layout;
  });
  ipcMain.on('yori:expanded', (event, value) => {
    if (!authorized(event) || typeof value?.open !== 'boolean' || value.open === opened) return;
    clearReplyPreview();
    resize = null;
    if (opened) {
      preferMaximized = Boolean(restoredBounds);
      if (restoredBounds) savePreferences();
      else rememberSize();
    }
    const current = restoredBounds ?? window.getBounds();
    restoredBounds = null;
    const area = screen.getDisplayMatching(current).workArea;
    const width = value.open ? Math.max(Math.min(320, area.width), Math.min(expandedSize.width, area.width)) : 90;
    const height = value.open ? Math.max(Math.min(480, area.height), Math.min(expandedSize.height, area.height)) : 100;
    const right = current.x + current.width + (value.open ? 0 : anchorOffset.x);
    const bottom = current.y + current.height + (value.open ? 0 : anchorOffset.y);
    const normalBounds = {
      width,
      height,
      x: Math.max(area.x, Math.min(right - width, area.x + area.width - width)),
      y: Math.max(area.y, Math.min(bottom - height, area.y + area.height - height))
    };
    opened = value.open;
    window.setAlwaysOnTop(mascotPinned);
    if (opened) {
      const openMaximized = typeof value.maximized === 'boolean' ? value.maximized : preferMaximized;
      preferMaximized = openMaximized;
      anchorOffset = { x: right - normalBounds.x - normalBounds.width, y: bottom - normalBounds.y - normalBounds.height };
      if (openMaximized) {
        restoredBounds = normalBounds;
        window.setBounds(area);
      } else {
        window.setBounds(normalBounds);
      }
    } else {
      window.setBounds(normalBounds);
    }
  });
  ipcMain.handle('yori:collapse-target', event => {
    if (!authorized(event) || !opened) return null;
    const bounds = window.getBounds();
    const base = restoredBounds ?? bounds;
    const area = screen.getDisplayMatching(base).workArea;
    const x = Math.max(area.x, Math.min(base.x + base.width + anchorOffset.x - 90, area.x + area.width - 90));
    const y = Math.max(area.y, Math.min(base.y + base.height + anchorOffset.y - 100, area.y + area.height - 100));
    return { x: x - bounds.x, y: y - bounds.y };
  });
  ipcMain.handle('yori:get-restore-bounds', event => {
    if (!authorized(event) || !opened || !restoredBounds) return null;
    return { from: window.getBounds(), to: restoredBounds };
  });
  ipcMain.handle('yori:set-maximized', (event, next) => {
    if (!authorized(event) || !opened || typeof next !== 'boolean') return null;
    resize = null;
    if (!next) {
      if (!restoredBounds) return { maximized: false };
      const from = window.getBounds();
      const to = restoredBounds;
      window.setBounds(to);
      restoredBounds = null;
      preferMaximized = false;
      savePreferences();
      return { maximized: false, from, to };
    }
    if (restoredBounds) return { maximized: true, from: restoredBounds, to: window.getBounds() };
    const from = window.getBounds();
    const to = screen.getDisplayMatching(from).workArea;
    rememberSize();
    window.setBounds(to);
    restoredBounds = from;
    preferMaximized = true;
    savePreferences();
    return { maximized: true, from, to };
  });
  ipcMain.handle('yori:toggle-maximized', event => {
    if (!authorized(event) || !opened) return false;
    resize = null;
    if (restoredBounds) {
      window.setBounds(restoredBounds);
      restoredBounds = null;
      preferMaximized = false;
      savePreferences();
      return false;
    }
    const bounds = window.getBounds();
    const { workArea } = screen.getDisplayMatching(bounds);
    rememberSize();
    window.setBounds(workArea);
    restoredBounds = bounds;
    preferMaximized = true;
    savePreferences();
    return true;
  });
  ipcMain.on('yori:move-mascot', (event, value) => {
    if (!authorized(event) || opened || !point(value) || !Number.isFinite(value.offsetX) || !Number.isFinite(value.offsetY)) return;
    const pointer = { x: Math.round(value.screenX), y: Math.round(value.screenY) };
    const area = screen.getDisplayNearestPoint(pointer).workArea;
    const [width, height] = window.getSize();
    window.setPosition(Math.round(Math.max(area.x, Math.min(pointer.x - value.offsetX, area.x + area.width - width))),
      Math.round(Math.max(area.y, Math.min(pointer.y - value.offsetY, area.y + area.height - height))));
    if (replyPreview) {
      const moved = window.getBounds();
      replyPreview.anchor.x = moved.x + replyPreview.layout.mascotOffsetX;
      replyPreview.anchor.y = moved.y + (replyPreview.layout.below ? 0 : replyPreview.bubbleHeight);
    }
  });
  ipcMain.on('yori:move-window', (event, value) => {
    if (!authorized(event) || !opened || restoredBounds || resize || !point(value) || !Number.isFinite(value.offsetX) || !Number.isFinite(value.offsetY)) return;
    const pointer = { x: Math.round(value.screenX), y: Math.round(value.screenY) };
    const area = screen.getDisplayNearestPoint(pointer).workArea;
    const [width] = window.getSize();
    window.setPosition(
      Math.round(Math.max(area.x - width + 80, Math.min(pointer.x - value.offsetX, area.x + area.width - 80))),
      Math.round(Math.max(area.y, Math.min(pointer.y - value.offsetY, area.y + area.height - 48)))
    );
  });
  ipcMain.handle('yori:capture-collapse-frame', async event => {
    if (!authorized(event) || !opened) return null;
    const frame = await window.webContents.capturePage(undefined, { stayHidden: true });
    if (frame.isEmpty()) return null;
    const png = frame.toPNG({ scaleFactor: 1 });
    return png.buffer.slice(png.byteOffset, png.byteOffset + png.byteLength);
  });
  ipcMain.on('yori:resize-start', (event, value) => {
    resizeDiagnostic('resize-start', { authorized: authorized(event), opened, ...value });
    if (!authorized(event) || !opened || restoredBounds || !edges.has(value?.edge) || !point(value)) return;
    const bounds = window.getBounds();
    resize = { edge: value.edge, screenX: value.screenX, screenY: value.screenY, bounds, area: screen.getDisplayMatching(bounds).workArea };
  });
  ipcMain.on('yori:resize-move', (event, value) => { if (authorized(event) && opened) resizeTo(value); });
  ipcMain.handle('yori:resize-end', event => {
    if (!authorized(event)) return null;
    if (resize) { rememberSize(); resizeDiagnostic('resize-end', { bounds: window.getBounds() }); resize = null; }
    return window.getBounds();
  });
});
app.on('window-all-closed', () => app.quit());
