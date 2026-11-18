/* AUREA Studio — puente seguro entre el renderer y el proceso principal. */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('aurea', {
  API: `http://127.0.0.1:${process.env.AUREA_PORT || 8765}`,
  openBookDialog: () => ipcRenderer.invoke('dialog:openBook'),
  openAudioDialog: () => ipcRenderer.invoke('dialog:openAudio'),
});
