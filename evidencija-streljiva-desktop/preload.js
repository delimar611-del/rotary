const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("evidencijaAPI", {
  load: () => ipcRenderer.invoke("evidencija:load"),
  save: (text) => ipcRenderer.invoke("evidencija:save", text),
  dataPath: () => ipcRenderer.invoke("evidencija:dataPath"),
});
