// CommonJS, because a sandboxed preload can't be an ES module.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("dashboard", {
  plans: () => ipcRenderer.invoke("plans"),
});
