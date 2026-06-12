const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
  startBackend: () => ipcRenderer.invoke('start-backend'),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  onBackendReady: (cb) => ipcRenderer.on('backend-ready', (_, port) => cb(port)),
  onBackendError: (cb) => ipcRenderer.on('backend-error', (_, msg) => cb(msg)),
});
