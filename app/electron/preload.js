const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vault', {
  isElectron: true,
  login: (store) => ipcRenderer.invoke('store-login', store || 'epic'),
  launch: (appName) => ipcRenderer.invoke('launch', appName),
  info: () => ipcRenderer.invoke('info'),
  quit: () => ipcRenderer.invoke('quit'),
  toggleFullscreen: () => ipcRenderer.invoke('toggle-fullscreen'),
});
