const { dialog } = require('electron');
const { autoUpdater } = require('electron-updater');

autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = false;
autoUpdater.on('error', error => console.warn('Y-Agent update failed:', error.code ?? error.name));

let busy = false;
let downloadedVersion;

async function confirmInstall(window) {
  const { response } = await dialog.showMessageBox(window, {
    type: 'info',
    title: 'Y-Agent 업데이트',
    message: `${downloadedVersion} 버전을 설치할 수 있어요.`,
    detail: '설치를 선택하면 Y-Agent가 재시작돼요.',
    buttons: ['지금 설치', '나중에'], defaultId: 0, cancelId: 1,
  });
  if (response === 0) autoUpdater.quitAndInstall(false, true);
}

function windowsUpdateMenuItem(window) {
  return {
    label: busy ? '업데이트 처리 중…' : downloadedVersion ? '다운로드한 업데이트 설치' : '앱 업데이트 확인',
    enabled: !busy,
    click: async () => {
      if (busy) return;
      busy = true;
      try {
        if (downloadedVersion) return await confirmInstall(window);
        const check = await autoUpdater.checkForUpdates();
        if (!check?.isUpdateAvailable) {
          await dialog.showMessageBox(window, { type: 'info', title: 'Y-Agent 업데이트', message: '최신 버전을 사용 중이에요.' });
          return;
        }
        const { response } = await dialog.showMessageBox(window, {
          type: 'info', title: 'Y-Agent 업데이트',
          message: `${check.updateInfo.version} 버전을 다운로드할까요?`,
          buttons: ['다운로드', '취소'], defaultId: 0, cancelId: 1,
        });
        if (response !== 0) return;
        await autoUpdater.downloadUpdate();
        downloadedVersion = check.updateInfo.version;
        await confirmInstall(window);
      } catch {
        if (!window.isDestroyed()) await dialog.showMessageBox(window, {
          type: 'error', title: 'Y-Agent 업데이트',
          message: '업데이트를 확인하거나 다운로드하지 못했어요.',
          detail: '사내 네트워크 연결을 확인하고 다시 시도해 주세요.',
        });
      } finally {
        busy = false;
      }
    },
  };
}

module.exports = { windowsUpdateMenuItem };
