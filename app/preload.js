// The page can't fetch plans.py itself: it runs from file:// and the server
// allows no cross-origin reads.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dashboard", {
  plans: () => ipcRenderer.invoke("plans"),
});
