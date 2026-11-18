/* AUREA Studio — puente seguro entre el renderer y el proceso principal. */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aurea', {
  API: `http://127.0.0.1:${process.env.AUREA_PORT || 8765}`,
  openBookDialog: () => ipcRenderer.invoke('dialog:openBook'),
  openAudioDialog: () => ipcRenderer.invoke('dialog:openAudio'),
  // Controles de la barra de título propia (ventana frameless)
  minimizeWindow: () => ipcRenderer.send('window:minimize'),
  toggleMaximize: () => ipcRenderer.send('window:maximizeToggle'),
  closeWindow: () => ipcRenderer.send('window:close'),
  isMaximized: () => ipcRenderer.invoke('window:isMaximized'),
  onMaximizeChanged: (cb) => ipcRenderer.on('window:maximizedChanged', (_e, v) => cb(v)),
});
