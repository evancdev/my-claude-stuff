// CommonJS, because a sandboxed preload can't be an ES module.
import { contextBridge, ipcRenderer, webFrame } from "electron";

contextBridge.exposeInMainWorld("dashboard", {
  plans: () => ipcRenderer.invoke("plans"),
  planFile: (repo: string, plan: string, file: string) => ipcRenderer.invoke("plan-file", repo, plan, file),
  planStatus: (repo: string, plan: string) => ipcRenderer.invoke("plan-status", repo, plan),
  zoom: () => webFrame.getZoomFactor(),
});
